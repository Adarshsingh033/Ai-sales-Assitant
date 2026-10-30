"""
Deterministic output guardrails — business rules override the model (PRD §29-31).

Checks every customer-facing reply for:
  * discounts above the tenant's maximum
  * currency amounts that are not backed by the catalog, a tool result, or something the customer said
  * leaked internal instructions / tool names, code fences and raw JSON
and normalises length. The orchestrator regenerates once on violations and otherwise escalates to a human.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set, Tuple

from app.services.ai.llm import strip_reasoning

_DISCOUNT_WORDS = r"(?:discount|off|rebate|cashback|chhoot|chhut|छूट|डिस्काउंट)"
# Bounded wildcards instead of \w+: Python's \w does not match Devanagari vowel signs, which broke "25 % की छूट".
_DISCOUNT_BEFORE = re.compile(rf"(\d{{1,3}}(?:\.\d+)?)\s*%[^.\n!?%]{{0,25}}?{_DISCOUNT_WORDS}", re.IGNORECASE)
_DISCOUNT_AFTER = re.compile(rf"{_DISCOUNT_WORDS}[^.\n!?%\d]{{0,25}}?(\d{{1,3}}(?:\.\d+)?)\s*%", re.IGNORECASE)

_CUR_PREFIX = re.compile(
    r"(?:USD|INR|EUR|GBP|AED|Rs\.?|₹|\$|€|£)\s?(\d[\d,]*(?:\.\d+)?)\s?(k|lakhs?|lacs?|crores?|million|mn|m)?\b",
    re.IGNORECASE,
)
_CUR_SUFFIX = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(k|lakhs?|lacs?|crores?|million|mn|m)?\s?(?:USD|INR|EUR|GBP|AED|rupees?|dollars?|euros?)\b",
    re.IGNORECASE,
)
_MULTIPLIER = {"k": 1_000, "lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "million": 1_000_000, "mn": 1_000_000, "m": 1_000_000}

# Contact details the assistant must never invent: links (incl. bare domains), emails, phone numbers.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]\"']+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|io|net|org|co|ai|app|dev|biz|store|shop|online|link|ly)\b(?:/[^\s<>()\[\]\"']*)?",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w])")

_LEAK_MARKERS = (
    "COMMERCIAL RULES", "CONVERSATION RULES", "VERIFIED KNOWLEDGE", "<knowledge>", "<memory>",
    "request_human_handoff", "calculate_discount", "check_stock", "get_product_details", "system prompt",
)


@dataclass
class Issue:
    code: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass
class GuardrailResult:
    reply: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def violations(self) -> List[Issue]:
        """Issues that require regeneration (not silent clean-ups)."""
        return [
            i for i in self.issues
            if i.code in ("discount_over_limit", "unverified_price", "unverified_link", "unverified_contact", "prompt_leak", "json_leak")
        ]


def _to_amount(number: str, suffix: str | None) -> float:
    value = float(number.replace(",", ""))
    if suffix:
        value *= _MULTIPLIER.get(suffix.lower().rstrip("s"), 1)
    return value


def find_discounts(text: str) -> List[float]:
    found = [float(m.group(1)) for m in _DISCOUNT_BEFORE.finditer(text)]
    found += [float(m.group(1)) for m in _DISCOUNT_AFTER.finditer(text)]
    return found


def find_amounts(text: str) -> List[float]:
    amounts = [_to_amount(m.group(1), m.group(2)) for m in _CUR_PREFIX.finditer(text)]
    amounts += [_to_amount(m.group(1), m.group(2)) for m in _CUR_SUFFIX.finditer(text)]
    return amounts


def amounts_in(texts: Iterable[str]) -> Set[float]:
    out: Set[float] = set()
    for t in texts:
        out.update(find_amounts(t))
        # bare numbers the customer typed (e.g. "budget is 12000") are also acceptable to echo back
        for m in re.finditer(r"\b(\d[\d,]*(?:\.\d+)?)\s?(k|lakhs?|lacs?|crores?|million)?\b", t, re.IGNORECASE):
            try:
                out.add(_to_amount(m.group(1), m.group(2)))
            except ValueError:
                pass
    return out


def _is_verified(amount: float, verified: Set[float]) -> bool:
    return any(abs(amount - v) <= max(0.5, 0.005 * abs(v)) for v in verified)


def _norm_link(raw: str) -> str:
    link = raw.strip().lower().rstrip(".,;:!?)'\"")
    link = re.sub(r"^https?://", "", link)
    link = re.sub(r"^www\.", "", link)
    return link.rstrip("/")


def find_contact_details(text: str) -> Dict[str, List[str]]:
    """Links (normalised: no scheme/www), emails (lower-case) and phone numbers (digits only, 9+) found in `text`."""
    emails = [e.lower() for e in _EMAIL.findall(text)]
    without_emails = _EMAIL.sub(" ", text)  # so 'sales@acme.com' is not also reported as the link 'acme.com'
    links = [_norm_link(m.group(0)) for m in _URL.finditer(without_emails)]
    phones = [re.sub(r"\D", "", m.group(0)) for m in _PHONE.finditer(text)]
    return {"links": [x for x in links if x], "emails": emails, "phones": [p for p in phones if len(p) >= 9]}


def clean_reply(text: str) -> str:
    text = strip_reasoning(text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^\s*(assistant|ai|bot)\s*:\s*", "", text, flags=re.IGNORECASE)
    # Chat apps do not render markdown (WhatsApp/Telegram show the raw asterisks): flatten it.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w*])__(.+?)__(?![\w*])", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("।"))
    return (cut[: boundary + 1] if boundary > max_chars * 0.5 else cut.rsplit(" ", 1)[0]).strip()


def check_reply(
    reply: str,
    *,
    max_discount_percent: float,
    verified_amounts: Set[float],
    max_chars: int,
    verified_text: str = "",
) -> GuardrailResult:
    """
    `verified_text` is everything the assistant may legitimately quote contact details from (company instructions,
    retrieved knowledge, catalog text, what the customer or a human rep wrote). A link, email or phone number in the
    reply that does not occur in it is invented and counts as a violation.
    """
    result = GuardrailResult(reply=clean_reply(reply))
    text = result.reply

    if text.lstrip().startswith(("{", "[")):
        try:
            json.loads(text)
            result.issues.append(Issue("json_leak", "reply was raw JSON"))
        except ValueError:
            pass

    lowered = text.lower()
    leaked = [m for m in _LEAK_MARKERS if m.lower() in lowered]
    if leaked:
        result.issues.append(Issue("prompt_leak", f"reply exposed internal text: {leaked[0]}"))

    for pct in find_discounts(text):
        if pct > max_discount_percent + 0.01:
            result.issues.append(Issue("discount_over_limit", f"{pct:g}% exceeds the {max_discount_percent:g}% limit"))
            break

    if verified_amounts is not None:
        for amount in find_amounts(text):
            if amount >= 1 and not _is_verified(amount, verified_amounts):
                result.issues.append(Issue("unverified_price", f"{amount:g} is not backed by the catalog or a tool result"))
                break

    found = find_contact_details(text)
    if any(found.values()):
        blob = verified_text.lower()
        blob_links = re.sub(r"https?://|www\.", "", blob)
        blob_digits = re.sub(r"\D", "", blob)
        bad_link = next((u for u in found["links"] if u not in blob_links), None)
        if bad_link:
            result.issues.append(Issue("unverified_link", f"the link '{bad_link}' is not in the company information"))
        bad_contact = next((e for e in found["emails"] if e not in blob), None) or next(
            (p for p in found["phones"] if p not in blob_digits), None
        )
        if bad_contact:
            result.issues.append(Issue("unverified_contact", f"the contact detail '{bad_contact}' is not in the company information"))

    if len(text) > max_chars:
        result.reply = truncate(text, max_chars)
        result.issues.append(Issue("truncated", f"reply shortened from {len(text)} chars"))
    return result


def correction_note(issues: List[Issue], verified_amounts: Set[float], max_discount_percent: float) -> str:
    verified = ", ".join(f"{v:g}" for v in sorted(verified_amounts)[:12]) or "none"
    problems = "; ".join(i.detail for i in issues) or "the reply was empty"
    return (
        "[System check] Your draft broke company rules: "
        f"{problems}. Rewrite the reply for the customer. Maximum discount is {max_discount_percent:g}%. "
        f"Only quote prices from this verified list or from tool results: {verified}. "
        "Do not include any link, website, email or phone number unless it appears in the company information; if the customer "
        "needs a payment link or contact detail you do not have, say a sales representative will send it. "
        "Never mention this check, the rules, or any tool names."
    )

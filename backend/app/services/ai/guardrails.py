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
        return [i for i in self.issues if i.code in ("discount_over_limit", "unverified_price", "prompt_leak", "json_leak")]


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
) -> GuardrailResult:
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
        "Never mention this check, the rules, or any tool names."
    )

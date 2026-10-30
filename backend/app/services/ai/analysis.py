"""
Turn analysis: structured signal extraction (LLM, JSON validated by Pydantic, heuristic fallback) and a
deterministic, explainable lead-scoring rubric.

The LLM is only asked for *signals* (intent, budget, timeline, objections, ...). The 0-100 score is computed
here from those signals, so it is stable, auditable, uses the tenant's qualification threshold, and cannot be
talked up by a hallucinated number.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.services.ai.llm import LLMError, LLMResult

logger = logging.getLogger(__name__)

INTENTS = (
    "product_inquiry", "pricing_inquiry", "product_comparison", "purchase_request", "demo_request",
    "quote_request", "support_question", "complaint", "refund_request", "human_request",
    "general_inquiry", "out_of_scope",
)

_BASE_SCORE = {
    "purchase_request": 80, "demo_request": 70, "quote_request": 65, "pricing_inquiry": 50,
    "product_comparison": 48, "product_inquiry": 38, "support_question": 15, "complaint": 20,
    "refund_request": 15, "human_request": 30, "general_inquiry": 15, "out_of_scope": 5,
}
_INTENT_ALIASES = {
    "purchase_intent": "purchase_request", "buy": "purchase_request", "purchase": "purchase_request",
    "demo": "demo_request", "quote": "quote_request", "pricing": "pricing_inquiry", "price_inquiry": "pricing_inquiry",
    "product": "product_inquiry", "comparison": "product_comparison", "support": "support_question",
    "refund": "refund_request", "human": "human_request", "general": "general_inquiry",
}

_PERSON = r"(?:a |an |the |your |our )?(?:real |live |human )?(?:human|person|manager|someone|somebody|agent|representative|rep|sales ?(?:rep|person|team|executive))"
_HUMAN_RE = re.compile(
    rf"\b(?:(?:talk|speak|chat|connect|transfer|escalate)\w*\s+(?:me\s+)?(?:to|with)\s+{_PERSON}"
    rf"|(?:want|need|get|prefer|give me|put me through to)\s+{_PERSON}"
    r"|real person|live agent|human agent|human representative|call me|have someone call|kisi se baat|insaan se)\b",
    re.IGNORECASE,
)
_OPT_OUT_RE = re.compile(
    r"^\s*(stop|unsubscribe|opt[ -]?out|cancel)\s*[.!]*\s*$|"
    r"\b(do not|don't|dont) (message|contact|text|call) me\b|\bstop (messaging|texting|contacting)\b|"
    r"\bremove me\b|\bmat bhejo\b|\bmessage mat\b|\bband karo\b",
    re.IGNORECASE,
)
_DISCOUNT_REQ_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%[^.\n]{0,25}(?:discount|off|chhoot|rebate)|(?:discount|off|rebate)[^.\n\d]{0,25}(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_DECLINE_PHRASES = (
    "don't sell", "do not sell", "don't offer", "do not offer", "outside our scope", "not in our catalog",
    "we don't carry", "we do not carry", "not something we offer",
)


def detect_human_request(text: str) -> bool:
    return bool(_HUMAN_RE.search(text or ""))


def detect_opt_out(text: str) -> bool:
    return bool(_OPT_OUT_RE.search(text or ""))


def detect_discount_request(text: str) -> Optional[float]:
    best: Optional[float] = None
    for m in _DISCOUNT_REQ_RE.finditer(text or ""):
        value = float(m.group(1) or m.group(2))
        best = value if best is None else max(best, value)
    return best


class TurnAnalysis(BaseModel):
    intent: str = "general_inquiry"
    buying_intent: bool = False
    out_of_scope: bool = False
    products_mentioned: List[str] = Field(default_factory=list)
    quantity: Optional[int] = None
    budget: Optional[float] = None
    timeline: Optional[str] = None
    requirements: Optional[str] = None
    company_size: Optional[int] = None
    objections: List[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    wants_human: bool = False
    is_complaint: bool = False
    legal_or_contract: bool = False
    requested_discount_percent: Optional[float] = None
    opt_out: bool = False
    language: Optional[str] = None
    recommended_action: Optional[str] = None
    customer_facts: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("intent", mode="before")
    @classmethod
    def _norm_intent(cls, v: Any) -> str:
        v = str(v or "general_inquiry").strip().lower().replace(" ", "_").replace("-", "_")
        return _INTENT_ALIASES.get(v, v)

    @field_validator("budget", "requested_discount_percent", mode="before")
    @classmethod
    def _num(cls, v: Any) -> Optional[float]:
        if v in (None, "", "null"):
            return None
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return None

    @field_validator("quantity", "company_size", mode="before")
    @classmethod
    def _int(cls, v: Any) -> Optional[int]:
        if v in (None, "", "null"):
            return None
        try:
            return int(float(str(v).replace(",", "")))
        except ValueError:
            return None

    @field_validator("objections", "products_mentioned", mode="before")
    @classmethod
    def _list(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v][:8]

    @field_validator("customer_facts", mode="before")
    @classmethod
    def _facts(cls, v: Any) -> Dict[str, Any]:
        return v if isinstance(v, dict) else {}

    @field_validator("timeline", mode="before")
    @classmethod
    def _timeline(cls, v: Any) -> Optional[str]:
        return str(v) if v not in (None, "", "null") else None


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------
_PROMPT = """You analyse one sales conversation turn and return ONLY a JSON object (no prose).

COMPANY CATALOG (what this company sells):
{catalog}

RECENT CONVERSATION:
{history}

LATEST CUSTOMER MESSAGE: "{user_message}"
LATEST ASSISTANT REPLY: "{ai_reply}"

Rules:
- intent: one of {intents}. Use "out_of_scope" only when the customer wants something this company does not sell.
- buying_intent: true only for a real intent to buy/trial/book a demo of THIS company's offerings.
- budget: ONLY an amount the CUSTOMER explicitly gave as their budget (never a price the assistant quoted, never a discount), else null.
- quantity: units/licenses/seats the CUSTOMER asked for, else null.
- timeline: "immediate" | "within 30 days" | "1-3 months" | "exploring" — ONLY when the customer says when they need it
  (not inferred from stock or availability questions), else null.
- objections: subset of ["price","competitor","approval","not_ready","need_info","discount","demo","implementation"].
- sentiment: "positive" | "neutral" | "negative". wants_human / is_complaint / legal_or_contract / opt_out: booleans.
- requested_discount_percent: the discount % the customer asked for, or null.
- customer_facts: short durable facts worth remembering (keys like company, role, team_size, preference, location), values are strings.
- recommended_action: one short next step for the sales rep.

JSON schema keys: intent, buying_intent, out_of_scope, products_mentioned, quantity, budget, timeline, requirements,
company_size, objections, sentiment, wants_human, is_complaint, legal_or_contract, requested_discount_percent, opt_out,
language, recommended_action, customer_facts."""


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def analyze_turn(
    llm: Any,
    *,
    history: List[Tuple[str, str]],
    user_message: str,
    ai_reply: str,
    catalog_summary: str,
) -> Tuple[TurnAnalysis, Optional[LLMResult]]:
    """Returns (analysis, llm_result). Falls back to keyword heuristics when the LLM or JSON fails."""
    recent = "\n".join(f"{'Customer' if who == 'CUSTOMER' else 'Assistant'}: {text}" for who, text in history[-6:])
    prompt = _PROMPT.format(
        catalog=catalog_summary or "Software products, SaaS subscriptions, and digital services.",
        history=recent or "(none)",
        user_message=user_message.replace('"', "'"),
        ai_reply=ai_reply.replace('"', "'"),
        intents=", ".join(INTENTS),
    )
    last_result: Optional[LLMResult] = None
    for attempt in range(2):
        try:
            res = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=700,
                response_format={"type": "json_object"},
            )
            last_result = res
            analysis = TurnAnalysis.model_validate(_extract_json(res.content))
            customer_text = " ".join([c for who, c in history if who == "CUSTOMER"] + [user_message])
            return _ground(_apply_rules(analysis, user_message, ai_reply), customer_text), res
        except LLMError as exc:
            logger.warning("Turn analysis LLM call failed: %s", exc)
            break  # transport failure — the gateway already retried; use heuristics
        except Exception as exc:  # malformed JSON / schema: retry once, then heuristics
            logger.warning("Turn analysis produced invalid output (attempt %d): %s", attempt + 1, exc)
    return heuristic_analysis(user_message, ai_reply), last_result


def _apply_rules(a: TurnAnalysis, user_message: str, ai_reply: str) -> TurnAnalysis:
    """Deterministic overrides that must hold regardless of what the model said."""
    if a.intent not in _BASE_SCORE:
        a.intent = "general_inquiry"
    if any(p in ai_reply.lower() for p in _DECLINE_PHRASES):
        a.out_of_scope = True
    if a.out_of_scope or a.intent == "out_of_scope":
        a.out_of_scope, a.intent, a.buying_intent = True, "out_of_scope", False
    if detect_human_request(user_message):
        a.wants_human = True
    if detect_opt_out(user_message):
        a.opt_out = True
    asked = detect_discount_request(user_message)
    if asked is not None:
        a.requested_discount_percent = max(asked, a.requested_discount_percent or 0)
    if a.wants_human and a.intent == "general_inquiry":
        a.intent = "human_request"
    if a.is_complaint and a.intent == "general_inquiry":
        a.intent = "complaint"
    return a


_TIMELINE_EVIDENCE = re.compile(
    r"\b(now|immediately|asap|urgent\w*|today|tonight|tomorrow|this (?:week|month|quarter)|next (?:week|month|quarter)|"
    r"within \d+|in \d+ (?:days?|weeks?|months?)|by (?:the )?(?:end|next|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
    r"q[1-4]|abhi|turant|jaldi|\d+ din|is (?:hafte|mahine)|exploring|just looking|not sure when)\b|अभी|जल्दी",
    re.IGNORECASE,
)


def _ground(a: TurnAnalysis, customer_text: str) -> TurnAnalysis:
    """
    Keep a lead signal only when the CUSTOMER's own words support it. Small models readily 'find' a budget in a
    price the assistant quoted or an urgent timeline in a stock question; those phantom signals inflate lead scores.
    """
    from app.services.ai.guardrails import amounts_in

    said = amounts_in([customer_text])
    if a.budget is not None and not any(abs(a.budget - v) <= max(0.5, 0.01 * v) for v in said):
        a.budget = None
    digits = {int(n) for n in re.findall(r"\d+", customer_text.replace(",", ""))}
    if a.quantity is not None and a.quantity not in digits:
        a.quantity = None
    if a.company_size is not None and a.company_size not in digits:
        a.company_size = None
    if a.timeline and not _TIMELINE_EVIDENCE.search(customer_text):
        a.timeline = None
    return a


_PRICE_WORDS = ("price", "cost", "how much", "pricing", "rate", "kitna", "kimat", "daam", "कीमत", "कितना")
_BUY_WORDS = ("buy", "purchase", "order", "checkout", "khareed", "kharid", "lena hai", "chahiye", "खरीद")
_DEMO_WORDS = ("demo", "trial", "free trial", "demonstration")
_QUOTE_WORDS = ("quote", "quotation", "proposal", "estimate")


def heuristic_analysis(user_message: str, ai_reply: str = "") -> TurnAnalysis:
    low = user_message.lower()
    a = TurnAnalysis(requirements=user_message[:160])
    if any(w in low for w in _BUY_WORDS):
        a.intent, a.buying_intent = "purchase_request", True
    elif any(w in low for w in _DEMO_WORDS):
        a.intent, a.buying_intent = "demo_request", True
    elif any(w in low for w in _QUOTE_WORDS):
        a.intent = "quote_request"
    elif any(w in low for w in _PRICE_WORDS):
        a.intent = "pricing_inquiry"
    elif "?" in low or len(low.split()) > 3:
        a.intent = "product_inquiry"
    return _apply_rules(a, user_message, ai_reply)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def capture_threshold(qualification_threshold: int) -> int:
    """Score at which a conversation becomes a Lead (40 at the default threshold of 60)."""
    return max(20, qualification_threshold - 20)


def priority_for(score: int, qualification_threshold: int) -> str:
    if score >= qualification_threshold + 20:
        return "HOT"
    if score >= qualification_threshold + 5:
        return "HIGH"
    if score >= capture_threshold(qualification_threshold):
        return "MEDIUM"
    return "LOW"


def score_lead(
    a: TurnAnalysis,
    *,
    previous_score: Optional[int],
    customer_turns: int,
) -> Tuple[int, str]:
    """Deterministic 0-100 score with a human-readable reason."""
    base = _BASE_SCORE.get(a.intent, 15)
    factors = [f"{a.intent.replace('_', ' ')} ({base})"]
    score = base

    if a.out_of_scope:
        score = min(score, 15)
        return _smooth(score, previous_score, a), "Requested product is not offered by this company."

    def add(points: int, label: str) -> None:
        nonlocal score
        score += points
        factors.append(f"{label} ({points:+d})")

    if a.buying_intent and base < 70:
        add(10, "buying intent")
    if a.budget:
        add(8, "budget stated")
    tl = (a.timeline or "").lower()
    if tl == "immediate":
        add(10, "needs it immediately")
    elif "30" in tl:
        add(6, "timeline within 30 days")
    elif "1-3" in tl or "1–3" in tl:
        add(2, "timeline 1-3 months")
    if a.quantity and a.quantity > 1:
        add(5, f"quantity {a.quantity}")
    if a.company_size and a.company_size >= 50:
        add(5, "larger organisation")
    if customer_turns > 1:
        add(min(8, 2 * (customer_turns - 1)), "sustained engagement")
    if "not_ready" in a.objections:
        add(-8, "not ready yet")
    if a.sentiment == "negative":
        add(-5, "negative sentiment")

    score = max(0, min(100, score))
    return _smooth(score, previous_score, a), "; ".join(factors)


def _smooth(raw: int, previous: Optional[int], a: TurnAnalysis) -> int:
    """A lead cools down gradually: one polite 'thanks' must not erase a hot lead."""
    if previous is not None and not a.out_of_scope:
        raw = max(raw, previous - 5)
    return max(0, min(100, raw))


def timeline_label(value: Optional[str]) -> Optional[str]:
    return value[:100] if value else None

"""Deterministic guardrails, signal detection, analysis parsing and the lead-scoring rubric."""
import pytest

from app.services.ai import guardrails
from app.services.ai.analysis import (
    TurnAnalysis,
    analyze_turn,
    capture_threshold,
    detect_discount_request,
    detect_human_request,
    detect_opt_out,
    heuristic_analysis,
    priority_for,
    score_lead,
)
from app.services.ai.llm import LLMError, LLMResult
from tests.helpers import FakeLLM


def check(reply, *, max_discount=10.0, verified=(), max_chars=1500):
    return guardrails.check_reply(reply, max_discount_percent=max_discount, verified_amounts=set(verified), max_chars=max_chars)


# ----------------------------------------------------------------------------- guardrails
def test_discount_above_limit_is_a_violation_and_within_limit_is_fine():
    assert [i.code for i in check("I can offer a 25% discount today!").violations] == ["discount_over_limit"]
    assert check("You get 10% off with the annual plan.").violations == []
    assert check("Happy to give a discount of up to 15 %").violations  # discount-word-first phrasing
    assert check("Our uptime is 99.99% guaranteed.").violations == []  # percentages unrelated to discounts


def test_prices_must_be_backed_by_catalog_tools_or_the_customer():
    assert check("The Pro plan costs $750 per month.", verified=[999]).violations[0].code == "unverified_price"
    assert check("The Pro plan costs $999/month.", verified=[999.0]).violations == []
    assert check("That would be ₹5 lakh in total.", verified=[500000]).violations == []
    assert check("It is USD 12,000 per year.", verified=[12000]).violations == []
    assert check("10k USD works.", verified=[10000]).violations == []
    assert check("No prices here, just features.", verified=[]).violations == []


def test_amount_parsing():
    assert guardrails.find_amounts("₹5 lakh and $12,000/year and 3 crore INR") == [500000.0, 12000.0, 30000000.0]
    assert guardrails.amounts_in(["my budget is 12k"]) >= {12000.0}


def test_internal_text_code_fences_json_and_reasoning_are_never_sent():
    assert check("Per my COMMERCIAL RULES I cannot.").violations[0].code == "prompt_leak"
    assert check("I will call request_human_handoff now").violations[0].code == "prompt_leak"
    assert check('{"reply": "hi"}').violations[0].code == "json_leak"
    cleaned = check("<think>plan</think>Sure!\n```python\nprint(1)\n```\nAnything else?")
    assert "print" not in cleaned.reply and "plan" not in cleaned.reply and cleaned.reply.startswith("Sure!")


def test_long_replies_are_trimmed_at_a_sentence_boundary():
    res = check("First sentence is here. Second sentence follows. " * 20, max_chars=120)
    assert len(res.reply) <= 120 and res.reply.endswith(".")
    assert any(i.code == "truncated" for i in res.issues)


def test_correction_note_lists_problems_and_verified_prices():
    note = guardrails.correction_note(check("50% discount").violations, {999.0}, 10.0)
    assert "50%" in note and "999" in note and "10%" in note


# ----------------------------------------------------------------------------- signal detection
@pytest.mark.parametrize("text,expected", [
    ("I want to talk to a manager", True), ("can I speak to someone please", True), ("please call me tomorrow", True),
    ("Is there a live agent?", True), ("kisi se baat karni hai", True),
    ("Tell me about management software pricing", False), ("our human resources team needs a tool", False),
])
def test_human_request_detection(text, expected):
    assert detect_human_request(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("STOP", True), ("stop.", True), ("Please stop messaging me", True), ("don't contact me again", True),
    ("mujhe message mat bhejo", True), ("I can't stop thinking about your product", False), ("how do I stop the trial?", False),
])
def test_opt_out_detection(text, expected):
    assert detect_opt_out(text) is expected


def test_discount_request_parsing():
    assert detect_discount_request("can I get 25% off?") == 25
    assert detect_discount_request("any discount around 12.5 % maybe") == 12.5
    assert detect_discount_request("50% of our team will use it") is None


# ----------------------------------------------------------------------------- analysis parsing
def test_turn_analysis_is_lenient_about_model_output():
    a = TurnAnalysis.model_validate(
        {"intent": "Purchase Intent", "budget": "12,000", "quantity": "50", "objections": "price", "timeline": "immediate",
         "customer_facts": None, "requested_discount_percent": "15"}
    )
    assert (a.intent, a.budget, a.quantity, a.objections, a.requested_discount_percent) == ("purchase_request", 12000.0, 50, ["price"], 15.0)


@pytest.mark.asyncio
async def test_analysis_uses_llm_json_then_applies_hard_rules():
    llm = FakeLLM()
    llm.analysis = {"intent": "pricing_inquiry", "buying_intent": True, "wants_human": False}
    a, res = await analyze_turn(llm.for_task("fast"), history=[], user_message="can I speak to a manager? I want 30% off",
                                ai_reply="Sure.", catalog_summary="- CRM")
    assert a.wants_human is True  # deterministic override even though the model said False
    assert a.requested_discount_percent == 30
    assert res is not None


@pytest.mark.asyncio
async def test_analysis_falls_back_to_heuristics_on_bad_json_or_llm_failure():
    class BadJson:
        async def complete(self, *a, **k):
            return LLMResult(content="definitely not json")

    a, _ = await analyze_turn(BadJson(), history=[], user_message="How much does the pro plan cost?", ai_reply="", catalog_summary="")
    assert a.intent == "pricing_inquiry"

    class Down:
        async def complete(self, *a, **k):
            raise LLMError("down", retriable=True)

    a, res = await analyze_turn(Down(), history=[], user_message="I want to buy 5 licenses", ai_reply="", catalog_summary="")
    assert a.intent == "purchase_request" and a.buying_intent and res is None


@pytest.mark.asyncio
async def test_declined_product_is_out_of_scope_whatever_the_model_says():
    llm = FakeLLM()
    llm.analysis = {"intent": "purchase_request", "buying_intent": True}
    a, _ = await analyze_turn(llm.for_task("fast"), history=[], user_message="Do you sell chocolate?",
                              ai_reply="Sorry, we don't sell chocolate.", catalog_summary="- CRM")
    score, reason = score_lead(a, previous_score=None, customer_turns=1)
    assert a.intent == "out_of_scope" and a.buying_intent is False
    assert score <= 15 and "not offered" in reason


def test_hinglish_heuristics():
    assert heuristic_analysis("iska price kitna hai?").intent == "pricing_inquiry"
    assert heuristic_analysis("mujhe ye khareedna hai").buying_intent is True


# ----------------------------------------------------------------------------- scoring rubric
def test_score_is_explainable_and_rewards_real_buying_signals():
    a = TurnAnalysis(intent="pricing_inquiry", buying_intent=True, budget=5000, timeline="immediate", quantity=20)
    score, reason = score_lead(a, previous_score=None, customer_turns=1)
    assert score == 50 + 10 + 8 + 10 + 5
    for part in ("pricing inquiry (50)", "buying intent (+10)", "budget stated (+8)", "needs it immediately (+10)", "quantity 20 (+5)"):
        assert part in reason


def test_engagement_and_negative_signals():
    base = TurnAnalysis(intent="product_inquiry")
    s1, _ = score_lead(base, previous_score=None, customer_turns=1)
    s5, _ = score_lead(base, previous_score=None, customer_turns=5)
    assert s5 == s1 + 8
    cold, _ = score_lead(TurnAnalysis(intent="product_inquiry", objections=["not_ready"], sentiment="negative"), previous_score=None, customer_turns=1)
    assert cold == s1 - 13


def test_a_polite_thanks_cannot_collapse_a_hot_lead():
    thanks = TurnAnalysis(intent="general_inquiry")
    score, _ = score_lead(thanks, previous_score=90, customer_turns=6)
    assert score == 85  # cools by at most 5 per turn
    assert score_lead(TurnAnalysis(intent="general_inquiry", out_of_scope=True), previous_score=90, customer_turns=6)[0] <= 15


def test_thresholds_follow_the_tenants_qualification_setting():
    assert capture_threshold(60) == 40 and capture_threshold(30) == 20
    assert [priority_for(s, 60) for s in (85, 70, 45, 10)] == ["HOT", "HIGH", "MEDIUM", "LOW"]
    assert priority_for(70, 40) == "HOT"  # a stricter/looser threshold shifts the tiers


def test_hindi_discount_claims_are_caught_and_markdown_is_flattened():
    """Live finding: Python's \w skips Devanagari vowel signs, so '25 % की छूट' slipped past the discount check."""
    assert check("बिल्कुल! 25 % की छूट के साथ कुल कीमत होगी।").violations[0].code == "discount_over_limit"
    assert check("छूट 30% तक मिल सकती है").violations[0].code == "discount_over_limit"
    assert check("10 % की छूट मिलेगी").violations == []
    res = check("Total is **USD 14,250.00** now.\n## Next steps\nShall we proceed?", verified=[14250])
    assert "**" not in res.reply and "##" not in res.reply and "USD 14,250.00" in res.reply


@pytest.mark.asyncio
async def test_lead_signals_must_be_grounded_in_what_the_customer_said():
    """Live finding: the model 'found' a budget in the assistant's own quote and an urgent timeline in a stock question."""
    llm = FakeLLM()
    llm.analysis = {"intent": "purchase_request", "buying_intent": True, "budget": 11250, "timeline": "immediate", "quantity": 10, "company_size": 500}
    history = [("CUSTOMER", "10 licenses with 25% off?"), ("AI", "The total comes to $11,250.00."), ("CUSTOMER", "ok. is it in stock?")]
    a, _ = await analyze_turn(llm.for_task("fast"), history=history[:-1], user_message="ok. is it in stock?", ai_reply="Yes, 100 units.", catalog_summary="")
    assert a.budget is None  # 11,250 is the assistant's price, not the customer's budget
    assert a.timeline is None  # a stock question is not an urgency statement
    assert a.quantity == 10  # the customer did say 10
    assert a.company_size is None  # 500 appears nowhere in the customer's words

    llm.analysis = {"intent": "purchase_request", "budget": 12000, "timeline": "immediate", "quantity": 50}
    a, _ = await analyze_turn(llm.for_task("fast"), history=[], user_message="We need 50 seats asap, budget is 12k", ai_reply="", catalog_summary="")
    assert (a.budget, a.timeline, a.quantity) == (12000, "immediate", 50)  # real signals survive

"""
Conversation Orchestrator — the single entry point for every customer turn, on every channel.

Pipeline
  1  persist the inbound message (committed first: a customer message is never lost)
  2  stop pending follow-ups, honour opt-out, AI on/off switch and monthly token budget
  3  build a token-budgeted context: persona + rules, customer memory, rolling summary, hybrid-retrieved
     knowledge, relevant products (authoritative prices) and live stock
  4  generate with a small tool loop (stock / price / discount / knowledge / human handoff), streaming optional
  5  validate with deterministic guardrails; regenerate once, otherwise escalate to a human
  6  persist the reply, trace and real token usage
  7  analyse the turn (structured signals → deterministic lead score, memory, summary, handoff) —
     inline for the web chat, deferred to the job queue for webhook channels
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.product import Product
from app.services.ai import guardrails, leads, memory as memory_svc, prompts, quota, summary
from app.services.ai.analysis import (
    TurnAnalysis,
    analyze_turn,
    detect_discount_request,
    detect_human_request,
    detect_opt_out,
    heuristic_analysis,
    score_lead,
)
from app.services.ai.llm import LLMError, LLMResult, LLMUsage, build_llm_client
from app.services.ai.tools import TOOL_SPECS, ToolContext, run_tool
from app.services.ai.tracing import TurnTrace, persist_trace
from app.services.inventory_service import InventoryService
from app.services.jobs import JobQueue, lane_key
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None]]

_OPT_OUT_REPLY = "Understood — I won't send you any more messages. If you ever need help, just write to us here."
_REWRITE_MARKERS = (
    "it", "that", "this", "they", "them", "those", "these", "its", "one", "iska", "uska", "isme", "usme", "wo", "woh",
    "ye", "yeh", "inka", "unka",
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class TurnResult:
    reply: str
    intent: Optional[str] = None
    score: int = 0
    score_reason: Optional[str] = None
    needs_human_handoff: bool = False
    lead_id: Optional[str] = None
    trace_id: Optional[str] = None
    # Set when analysis_inline=False: kwargs for `post_process_turn`, to be run after the reply is delivered.
    pending: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "intent": self.intent,
            "score": self.score,
            "score_reason": self.score_reason,
            "needs_human_handoff": self.needs_human_handoff,
            "lead_id": self.lead_id,
            "trace_id": self.trace_id,
        }


@dataclass
class PromptContext:
    messages: List[Dict[str, Any]]
    verified_amounts: set
    knowledge_matches: List[Dict[str, Any]] = field(default_factory=list)
    product_names: List[str] = field(default_factory=list)


class ConversationOrchestrator:
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @classmethod
    async def handle_message(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
        trigger: str = "INBOUND",
        on_token: Optional[TokenCallback] = None,
        analysis_inline: bool = True,
        save_inbound: bool = True,
    ) -> TurnResult:
        text = user_message_text.strip()
        trace = TurnTrace(
            tenant_id=tenant_id, conversation_id=conversation_id, customer_id=customer_id,
            trigger=trigger, user_message=text,
        )
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id))
        ).scalar_one()
        customer = (await db.execute(select(Customer).where(Customer.id == customer_id))).scalar_one()
        config = await cls.load_config(db, tenant_id)

        # 1. durable inbound message
        if save_inbound:
            db.add(Message(conversation_id=conversation_id, sender_type="CUSTOMER", content=text))
            conv.last_activity_at = _utcnow()
            await db.commit()

        # 2. opt-out wins over everything; otherwise a reply makes pending follow-ups obsolete
        if detect_opt_out(text):
            return await cls._opt_out(db, conv, customer, trace)

        from app.services.follow_up_service import FollowUpService

        await FollowUpService.mark_customer_replied(db, conversation_id)

        if not config.is_enabled:
            await cls._escalate(db, conv, config, customer, "AI assistant is switched off for this company", notify=True)
            trace.handoff_reason = conv.handoff_reason
            await persist_trace(db, trace)
            await db.commit()
            return TurnResult(reply="", intent="ai_disabled", needs_human_handoff=True)

        status = await quota.check_quota(db, tenant_id, config)
        if status["exceeded"]:
            reason = "Monthly AI token limit reached"
            reply = prompts.FALLBACK_REPLY
            await cls._save_ai_message(db, conv, reply)
            await cls._escalate(db, conv, config, customer, reason, notify=True)
            trace.handoff_reason, trace.reply = reason, reply
            await persist_trace(db, trace)
            await db.commit()
            return TurnResult(reply=reply, intent="quota_exceeded", needs_human_handoff=True)

        # 3-5. context → generation → guardrails
        reply, gen_ctx = await cls._respond(db, tenant_id, customer, conv, config, text, trace, on_token)

        # 6. persist reply + telemetry, durably, before any slow analysis
        await cls._save_ai_message(db, conv, reply)
        trace.reply = reply
        trace.handoff_reason = gen_ctx.handoff_reason
        trace_id = await persist_trace(db, trace)
        await db.commit()

        result = TurnResult(reply=reply, trace_id=str(trace_id) if trace_id else None)
        forced_handoff = gen_ctx.handoff_reason

        # 7. analysis
        if analysis_inline:
            outcome = await cls.post_process_turn(
                db, tenant_id=tenant_id, customer_id=customer_id, conversation_id=conversation_id,
                user_message_text=text, ai_reply=reply, handoff_reason=forced_handoff,
            )
            result.intent, result.score = outcome["intent"], outcome["score"]
            result.score_reason, result.lead_id = outcome["score_reason"], outcome["lead_id"]
            result.needs_human_handoff = outcome["needs_human_handoff"]
        else:
            # Channel path: the caller delivers the reply first, then runs `post_process_turn(**result.pending)`.
            result.needs_human_handoff = bool(forced_handoff) or detect_human_request(text)
            if forced_handoff or detect_human_request(text):
                await cls._escalate(db, conv, config, customer, forced_handoff or "Customer asked for a human", notify=True)
            result.pending = {
                "tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id,
                "user_message_text": text, "ai_reply": reply, "handoff_reason": forced_handoff,
            }
        return result

    @classmethod
    async def post_process_turn(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
        ai_reply: str,
        handoff_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyse the finished turn: lead score, memory, handoff, summary. Never raises into the caller."""
        conv = (await db.execute(select(Conversation).where(Conversation.id == conversation_id))).scalar_one()
        customer = (await db.execute(select(Customer).where(Customer.id == customer_id))).scalar_one()
        config = await cls.load_config(db, tenant_id)
        trace = TurnTrace(
            tenant_id=tenant_id, conversation_id=conversation_id, customer_id=customer_id, trigger="ANALYSIS"
        )
        history = await cls._history(db, conversation_id, 8)
        try:
            analysis, res = await analyze_turn(
                build_llm_client(config, task="fast"),
                history=history,
                user_message=user_message_text,
                ai_reply=ai_reply,
                catalog_summary=await cls._catalog_summary(db, tenant_id),
            )
            if res is not None:
                trace.add_llm(res, "LEAD_EXTRACTION")
        except Exception as exc:  # pragma: no cover - analysis must never break the conversation
            logger.exception("Turn analysis crashed: %s", exc)
            analysis = heuristic_analysis(user_message_text, ai_reply)

        lead = await leads.get_lead(db, tenant_id, conversation_id)
        customer_turns = int(
            (await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation_id, Message.sender_type == "CUSTOMER"
                )
            )).scalar_one()
        )
        score, reason = score_lead(analysis, previous_score=lead.score if lead else None, customer_turns=customer_turns)

        conv.detected_intent = analysis.intent
        conv.last_activity_at = _utcnow()

        reasons: List[str] = [handoff_reason] if handoff_reason else []
        if analysis.wants_human:
            reasons.append("Customer asked for a human representative")
        if analysis.is_complaint:
            reasons.append("Customer complaint")
        if analysis.legal_or_contract:
            reasons.append("Legal / contract question")
        asked = max(analysis.requested_discount_percent or 0, detect_discount_request(user_message_text) or 0)
        if asked > config.max_discount_percent + 0.01:
            reasons.append(f"Discount request {asked:g}% exceeds the {config.max_discount_percent:g}% limit")
        needs_handoff = bool(reasons)
        handoff_text = "; ".join(dict.fromkeys(reasons)) or None

        if leads.should_capture(score, analysis, config, has_lead=lead is not None):
            lead = await leads.sync_lead(
                db, tenant_id=tenant_id, customer=customer, conversation=conv, config=config,
                analysis=analysis, score=score, reason=reason, lead=lead,
            )

        try:
            await memory_svc.update_memory(db, tenant_id, customer, analysis)
        except Exception as exc:  # pragma: no cover
            logger.warning("Memory update failed: %s", exc)

        if needs_handoff or not conv.summary_message_count:  # keep the LLM rolling summary unless a rep needs a fresh brief
            conv.ai_summary = summary.build_brief(customer, analysis, lead, score, handoff_text)
        if needs_handoff:
            await cls._escalate(db, conv, config, customer, handoff_text or "", notify=True, lead_score=score)
        await persist_trace(db, trace)
        await db.commit()

        total_msgs = int((await db.execute(select(func.count(Message.id)).where(Message.conversation_id == conversation_id))).scalar_one())
        if total_msgs - (conv.summary_message_count or 0) >= 10:
            await JobQueue.enqueue(
                db, "SUMMARIZE_CONVERSATION", {"conversation_id": str(conversation_id)},
                tenant_id=tenant_id, conversation_id=lane_key(tenant_id, customer.phone or str(customer_id)), dedupe=True,
            )

        return {
            "intent": analysis.intent,
            "score": score,
            "score_reason": reason,
            "needs_human_handoff": needs_handoff,
            "lead_id": str(lead.id) if lead else None,
        }

    # ------------------------------------------------------------------
    # Respond: context → generation → guardrails
    # ------------------------------------------------------------------
    @classmethod
    async def _respond(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer: Customer,
        conv: Conversation,
        config: AIAssistantConfig,
        text: str,
        trace: TurnTrace,
        on_token: Optional[TokenCallback],
    ) -> Tuple[str, ToolContext]:
        t_ctx = time.monotonic()
        llm = build_llm_client(config, task="reply")
        fast = build_llm_client(config, task="fast")
        tools_ctx = ToolContext(db=db, tenant_id=tenant_id, max_discount_percent=config.max_discount_percent)

        try:
            ctx = await cls._build_context(db, tenant_id, customer, conv, config, text, trace, fast)
        except Exception as exc:
            logger.exception("Context build failed; answering without retrieved context: %s", exc)
            trace.error = f"context: {exc}"
            ctx = await cls._minimal_context(db, config, conv, text)
        tools_ctx.verified_amounts |= ctx.verified_amounts
        trace.mark("context_ms", t_ctx)

        t_llm = time.monotonic()
        try:
            draft = await cls._generate(llm, ctx.messages, tools_ctx, trace, on_token)
        except LLMError as exc:
            logger.error("LLM unavailable for conversation %s: %s", conv.id, exc)
            trace.error = f"llm: {exc}"
            trace.mark("llm_ms", t_llm)
            await cls._escalate(db, conv, config, customer, "AI service unavailable — customer is waiting", notify=True)
            tools_ctx.handoff_reason = tools_ctx.handoff_reason or "AI service unavailable"
            if on_token:
                await on_token(prompts.FALLBACK_REPLY)
            return prompts.FALLBACK_REPLY, tools_ctx

        checked = guardrails.check_reply(
            draft,
            max_discount_percent=config.max_discount_percent,
            verified_amounts=tools_ctx.verified_amounts | guardrails.amounts_in(tools_ctx.knowledge_texts),
            max_chars=settings.AI_MAX_REPLY_CHARS,
        )
        trace.guardrails.extend(i.as_dict() for i in checked.issues)

        if checked.violations or not checked.reply:
            retry_messages = list(ctx.messages) + [
                {"role": "assistant", "content": checked.reply or draft},
                {"role": "user", "content": guardrails.correction_note(
                    checked.violations, tools_ctx.verified_amounts, config.max_discount_percent)},
            ]
            try:
                second = await cls._generate(llm, retry_messages, tools_ctx, trace, None, temperature=0.1)
                checked2 = guardrails.check_reply(
                    second,
                    max_discount_percent=config.max_discount_percent,
                    verified_amounts=tools_ctx.verified_amounts | guardrails.amounts_in(tools_ctx.knowledge_texts),
                    max_chars=settings.AI_MAX_REPLY_CHARS,
                )
                trace.guardrails.append({"code": "regenerated", "detail": "second draft after violation"})
                trace.guardrails.extend(i.as_dict() for i in checked2.issues)
            except LLMError:
                checked2 = guardrails.GuardrailResult(reply="", issues=[guardrails.Issue("prompt_leak", "retry failed")])
            if checked2.violations or not checked2.reply:
                trace.guardrails.append({"code": "fallback", "detail": "violation persisted; escalated to human"})
                tools_ctx.handoff_reason = tools_ctx.handoff_reason or "Guardrail: reply could not be verified (price/discount)"
                final = prompts.HANDOFF_REPLY
            else:
                final = checked2.reply
        else:
            final = checked.reply

        trace.mark("llm_ms", t_llm)
        return final, tools_ctx

    @classmethod
    async def _generate(
        cls,
        llm: Any,
        messages: List[Dict[str, Any]],
        tools_ctx: ToolContext,
        trace: TurnTrace,
        on_token: Optional[TokenCallback],
        temperature: float = 0.3,
    ) -> str:
        """One generation with a bounded tool loop. Streams tokens of the final answer when `on_token` is set."""
        msgs = list(messages)
        rounds = settings.AI_MAX_TOOL_ROUNDS
        for round_no in range(rounds + 1):
            use_tools = TOOL_SPECS if round_no < rounds else None
            if on_token is not None:
                result = await cls._stream_round(llm, msgs, use_tools, on_token, temperature)
            else:
                result = await llm.complete(msgs, tools=use_tools, temperature=temperature, max_tokens=700)
            trace.add_llm(result, "SALES_CHAT")
            if result.tool_calls and use_tools:
                msgs.append(
                    {
                        "role": "assistant",
                        "content": result.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments)},
                            }
                            for tc in result.tool_calls
                        ],
                    }
                )
                for tc in result.tool_calls:
                    out = await run_tool(tools_ctx, tc)
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out, default=str)[:3000]})
                trace.tool_calls = list(tools_ctx.trace)
                continue
            return result.content
        return ""  # pragma: no cover - loop always returns above

    @staticmethod
    async def _stream_round(
        llm: Any,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        on_token: TokenCallback,
        temperature: float,
    ) -> LLMResult:
        parts: List[str] = []
        final: Optional[LLMResult] = None
        try:
            async for ev in llm.stream(messages, tools=tools, temperature=temperature, max_tokens=700):
                if ev.kind == "token":
                    parts.append(ev.text)
                    await on_token(ev.text)
                elif ev.kind == "done":
                    final = ev.result
        except LLMError:
            if not parts:
                raise
            logger.warning("LLM stream interrupted after %d chars; using the partial reply", sum(map(len, parts)))
            return LLMResult(content="".join(parts).strip(), usage=LLMUsage(estimated=True))
        return final or LLMResult(content="".join(parts).strip(), usage=LLMUsage(estimated=True))

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    @classmethod
    async def _build_context(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer: Customer,
        conv: Conversation,
        config: AIAssistantConfig,
        text: str,
        trace: TurnTrace,
        fast_llm: Any,
    ) -> PromptContext:
        budgets = prompts.budget_chars()
        history = await cls._history(db, conv.id, settings.AI_HISTORY_MESSAGES)
        is_first_turn = not any(who == "AI" for who, _ in history)

        query = await cls._retrieval_query(fast_llm, history, text, trace)
        knowledge = await RAGService.retrieve(db, tenant_id, query, top_k=settings.RAG_TOP_K, source_type="DOCUMENT")
        prod_hits = await RAGService.retrieve(
            db, tenant_id, query, top_k=5, min_score=settings.RAG_MIN_SCORE_PRODUCT, source_type="PRODUCT"
        )
        trace.retrieval = {
            "query": query,
            "backend": knowledge.backend,
            "confidence": knowledge.confidence,
            "latency_ms": knowledge.latency_ms + prod_hits.latency_ms,
            "documents": [{"chunk_id": m["chunk_id"], "file": m["filename"], "score": m["score"]} for m in knowledge.matches],
            "products": [{"chunk_id": m["chunk_id"], "score": m["score"]} for m in prod_hits.matches],
        }

        products = await cls._load_products(db, tenant_id, [m["product_id"] for m in prod_hits.matches if m.get("product_id")])
        stock = await InventoryService.get_stock_for_products(db, tenant_id, [p.id for p in products])
        product_dicts = [
            {
                "name": p.name, "sku": p.sku, "category": p.category, "currency": p.currency,
                "price": float(p.price), "description": p.description,
                "stock_summary": InventoryService.summarize_stock(stock.get(p.id)),
            }
            for p in products
        ]

        memory = await memory_svc.load_memory(db, customer.id)
        summary_text = conv.ai_summary if (conv.summary_message_count or 0) > 0 else None
        dynamic = prompts.build_dynamic_prompt(
            memory_text=memory_svc.memory_text(memory, budgets["memory"]),
            summary=(summary_text or "")[: budgets["summary"]] or None,
            knowledge_text=prompts.format_knowledge(knowledge.matches, budgets["knowledge"]),
            products_text=prompts.format_products(product_dicts, budgets["products"]),
            retrieval_confidence=knowledge.confidence if knowledge.matches else None,
        )
        system = prompts.build_static_prompt(config, is_first_turn) + "\n\n" + dynamic

        chat = [
            {"role": "user" if who == "CUSTOMER" else "assistant", "content": content}
            for who, content in history
        ]
        chat = prompts.fit_history(chat, budgets["history"])
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}] + chat

        verified = {round(p["price"], 2) for p in product_dicts}
        # Everything already said in this conversation — by the customer, a rep, or an earlier assistant reply that
        # passed these same checks — may be repeated. Re-quoting a price the assistant itself verified last turn
        # must not trigger a regeneration.
        verified |= guardrails.amounts_in([content for _, content in history])
        verified |= guardrails.amounts_in([m["content"] for m in knowledge.matches])
        return PromptContext(
            messages=messages,
            verified_amounts=verified,
            knowledge_matches=knowledge.matches,
            product_names=[p["name"] for p in product_dicts],
        )

    @classmethod
    async def _minimal_context(cls, db: AsyncSession, config: AIAssistantConfig, conv: Conversation, text: str) -> PromptContext:
        history = await cls._history(db, conv.id, settings.AI_HISTORY_MESSAGES)
        is_first = not any(who == "AI" for who, _ in history)
        system = prompts.build_static_prompt(config, is_first) + "\n\n" + prompts.build_dynamic_prompt(
            memory_text=None, summary=None, knowledge_text=prompts.format_knowledge([], 100),
            products_text=prompts.format_products([], 100), retrieval_confidence=None,
        )
        chat = [{"role": "user" if w == "CUSTOMER" else "assistant", "content": c} for w, c in history]
        return PromptContext(messages=[{"role": "system", "content": system}] + chat, verified_amounts=set())

    @classmethod
    async def _retrieval_query(cls, fast_llm: Any, history: List[Tuple[str, str]], text: str, trace: TurnTrace) -> str:
        """Make follow-ups ("and the price?") searchable by resolving them against the conversation."""
        prior_customer = [c for who, c in history[:-1] if who == "CUSTOMER"]
        words = text.lower().split()
        needs = bool(prior_customer) and (len(words) <= 6 or any(w.strip("?.,!") in _REWRITE_MARKERS for w in words))
        if not needs:
            return text
        convo = "\n".join(f"{'Customer' if w == 'CUSTOMER' else 'Assistant'}: {c}" for w, c in history[-6:-1])
        try:
            res = await fast_llm.complete(
                [
                    {
                        "role": "user",
                        "content": (
                            "Rewrite the customer's last message as one standalone search query that includes any product or "
                            "topic it refers to from the conversation. Keep the customer's language. Return only the query.\n\n"
                            f"Conversation:\n{convo}\n\nLast message: {text}"
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=60,
            )
            trace.add_llm(res, "QUERY_REWRITE", count_in_trace=False)
            rewritten = res.content.strip().strip('"')
            if 2 <= len(rewritten) <= 300:
                return rewritten
        except LLMError:
            pass
        return f"{prior_customer[-1]} {text}"[:300]

    @staticmethod
    async def _load_products(db: AsyncSession, tenant_id: uuid.UUID, product_ids: List[str]) -> List[Product]:
        ids = [uuid.UUID(p) for p in dict.fromkeys(product_ids)]
        found: List[Product] = []
        if ids:
            rows = (
                await db.execute(select(Product).where(Product.tenant_id == tenant_id, Product.is_active.is_(True), Product.id.in_(ids)))
            ).scalars().all()
            order = {pid: i for i, pid in enumerate(ids)}
            found = sorted(rows, key=lambda p: order[p.id])
            if len(found) >= 3:
                return found
        # Small catalogs: show everything so "what do you sell?" works even without a semantic hit.
        everything = (
            await db.execute(select(Product).where(Product.tenant_id == tenant_id, Product.is_active.is_(True)).order_by(Product.name).limit(13))
        ).scalars().all()
        if 0 < len(everything) <= 12:
            return list(everything)
        return found

    @staticmethod
    async def _catalog_summary(db: AsyncSession, tenant_id: uuid.UUID) -> str:
        rows = (
            await db.execute(
                select(Product.name, Product.category).where(Product.tenant_id == tenant_id, Product.is_active.is_(True)).limit(30)
            )
        ).all()
        if not rows:
            return "Software solutions, digital sales tools, and SaaS subscription plans."
        return "\n".join(f"- {n} ({c or 'General'})" for n, c in rows)

    @staticmethod
    async def _history(db: AsyncSession, conversation_id: uuid.UUID, limit: int) -> List[Tuple[str, str]]:
        rows = (
            await db.execute(
                select(Message.sender_type, Message.content)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], r[1]) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # Persistence / state helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def load_config(db: AsyncSession, tenant_id: uuid.UUID) -> AIAssistantConfig:
        config = (await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id))).scalar_one_or_none()
        if config is None:
            config = AIAssistantConfig(tenant_id=tenant_id)
            db.add(config)
            await db.flush()
        return config

    @staticmethod
    async def _save_ai_message(db: AsyncSession, conv: Conversation, reply: str) -> None:
        db.add(Message(conversation_id=conv.id, sender_type="AI", content=reply.strip()))
        conv.last_activity_at = _utcnow()
        await db.flush()

    @classmethod
    async def _opt_out(cls, db: AsyncSession, conv: Conversation, customer: Customer, trace: TurnTrace) -> TurnResult:
        from app.services.follow_up_service import FollowUpService

        customer.do_not_contact = True
        await FollowUpService.cancel_for_customer(db, customer.id, "CUSTOMER_OPTED_OUT")
        await cls._save_ai_message(db, conv, _OPT_OUT_REPLY)
        conv.detected_intent = "opt_out"
        trace.reply = _OPT_OUT_REPLY
        await persist_trace(db, trace)
        await db.commit()
        return TurnResult(reply=_OPT_OUT_REPLY, intent="opt_out")

    @classmethod
    async def _escalate(
        cls,
        db: AsyncSession,
        conv: Conversation,
        config: AIAssistantConfig,
        customer: Customer,
        reason: str,
        *,
        notify: bool,
        lead_score: Optional[int] = None,
    ) -> None:
        """Move the conversation to the sales queue (once) and alert the assigned rep with a useful brief."""
        was_active = conv.status == "ACTIVE_AI"
        conv.handoff_reason = reason or conv.handoff_reason
        if was_active:
            conv.status = "WAITING_SALES"
        await db.flush()
        if notify and was_active:
            from app.services.notification_service import NotificationService

            brief = (conv.ai_summary or "").replace("\n", " | ")
            await NotificationService.create_notification(
                db=db,
                tenant_id=conv.tenant_id,
                title="🚨 Human Takeover Requested",
                message=f"{reason}. {brief}"[:480],
                notif_type="HUMAN_TAKEOVER_REQUESTED",
                recipient_user_id=conv.assigned_user_id,
                link_url=f"#/conversations?id={conv.id}",
            )

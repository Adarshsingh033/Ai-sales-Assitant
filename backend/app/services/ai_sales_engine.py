"""
Central AI Sales Engine.
Orchestrates RAG context retrieval, product lookup, conversational generation with Groq,
intent detection, automated lead qualification, and 0-100 lead scoring.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.product import Product
from app.services.groq_client import GroqClient
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class AISalesEngine:
    @classmethod
    async def prepare_chat_context(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
    ) -> Tuple[List[Dict[str, str]], GroqClient, AIAssistantConfig, Conversation]:
        """
        Saves incoming customer message, retrieves RAG/product context, and prepares Groq LLM messages.
        """
        # 1. Save customer message
        customer_msg = Message(
            conversation_id=conversation_id,
            sender_type="CUSTOMER",
            content=user_message_text.strip(),
        )
        db.add(customer_msg)
        await db.flush()

        # 2. Load tenant AI assistant configuration
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id)
        config = (await db.execute(stmt_cfg)).scalar_one_or_none()
        if not config:
            config = AIAssistantConfig(tenant_id=tenant_id)
            db.add(config)
            await db.flush()

        # 3. Retrieve recent conversation history (up to 10 messages)
        stmt_hist = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(10)
        )
        hist_rows = (await db.execute(stmt_hist)).scalars().all()
        conversation_history = list(reversed(hist_rows))

        # Check whether this is the start of the conversation or ongoing
        prior_ai_messages = [m for m in conversation_history if m.sender_type == "AI"]
        is_first_turn = len(prior_ai_messages) == 0

        # 4. Search knowledge base via RAG
        knowledge_matches = await RAGService.search_knowledge(
            db=db,
            tenant_id=tenant_id,
            query=user_message_text,
            top_k=3,
            similarity_threshold=0.35,
        )
        knowledge_context = "\n---\n".join([m["content"] for m in knowledge_matches]) if knowledge_matches else "No specific document knowledge found."

        # 5. Fetch available active products
        stmt_prods = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)
        products = (await db.execute(stmt_prods)).scalars().all()
        if products:
            product_list_str = "\n".join([
                f"- {p.name} (Category: {p.category or 'General'}, Price: {p.currency} {p.price:.2f}): {p.description or ''}"
                for p in products[:10]
            ])
        else:
            product_list_str = "No catalog products listed yet."

        greeting_instruction = (
            f"This is the start of the conversation. Greet the customer warmly, introduce yourself as '{config.assistant_name}', and address their question directly."
            if is_first_turn
            else f"This is an ONGOING conversation. DO NOT say 'Hi there!', 'Hello!', or re-introduce yourself. Jump directly into your helpful, context-aware reply."
        )

        # 6. Formulate system prompt
        system_prompt = f"""You are '{config.assistant_name}', an expert digital sales assistant for this company.
Communication style: {config.personality}.
Sales guidelines and instructions:
{config.custom_instructions or 'Be helpful, understand customer requirements, recommend suitable products, and qualify leads.'}

COMMERCIAL RULES:
- Maximum automatic discount you may offer is {config.max_discount_percent}%. Never offer discounts above this limit.
- If a customer requests a higher discount or insists on human negotiation, acknowledge it politely and state that a sales manager will be notified.
- Prioritize company-provided knowledge. Do NOT invent prices, specifications, or false guarantees.

COMPANY VERIFIED KNOWLEDGE:
{knowledge_context}

PRODUCT CATALOG:
{product_list_str}

CRITICAL SALES & CONVERSATIONAL RULES:
- GREETING: {greeting_instruction}
- ACKNOWLEDGMENTS & SHORT REPLIES: If the customer replies with an acknowledgment or short response (e.g. 'ok', 'okay', 'got it', 'cool', 'sounds good', 'thanks', 'sure', 'alright') after receiving product, pricing, or purchase info:
  1. DO NOT reset the conversation or ask generic discovery questions ('How can I help you today?', 'What is your budget?', etc.).
  2. Maintain context and advance the sale: warmly offer clear next steps, such as sending a direct purchase/checkout link, booking a quick 15-minute product demo, or asking if they have specific questions before getting started.
- NATURAL SALES PACING: Keep responses concise (2 to 4 sentences maximum), human-like, and proactive. Ask at most ONE natural question or call-to-action per turn. Never bombard the prospect with a bulleted list of discovery questions.
- IDENTITY: Always speak as '{config.assistant_name}'. Never say you are an AI language model.
- Formatting: Never output markdown code blocks or JSON in customer replies."""

        # Assemble LLM messages
        llm_messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for m in conversation_history:
            role = "user" if m.sender_type == "CUSTOMER" else "assistant"
            llm_messages.append({"role": role, "content": m.content})

        groq_client = GroqClient(
            api_key=config.groq_api_key or settings.GROQ_API_KEY,
            model=config.groq_model or settings.GROQ_MODEL,
        )

        stmt_conv = select(Conversation).where(Conversation.id == conversation_id)
        conv = (await db.execute(stmt_conv)).scalar_one()

        return llm_messages, groq_client, config, conv

    @classmethod
    async def finalize_ai_response(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
        ai_reply: str,
        groq_client: GroqClient,
        config: AIAssistantConfig,
        catalog_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Saves AI reply, extracts lead intelligence, updates conversation status, syncs lead, and commits DB.
        """
        # 7. Save AI reply message
        ai_msg = Message(
            conversation_id=conversation_id,
            sender_type="AI",
            content=ai_reply.strip(),
        )
        db.add(ai_msg)

        # Retrieve recent history for intelligence extraction
        stmt_hist = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(10)
        )
        hist_rows = (await db.execute(stmt_hist)).scalars().all()
        conversation_history = list(reversed(hist_rows))

        # Retrieve catalog summary if not provided
        if not catalog_summary:
            stmt_prods = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)
            products = (await db.execute(stmt_prods)).scalars().all()
            if products:
                catalog_summary = "\n".join([
                    f"- {p.name} (Category: {p.category or 'General'}, Price: {p.currency} {p.price:.2f}): {p.description or ''}"
                    for p in products[:10]
                ])
            else:
                catalog_summary = "Software solutions, digital sales tools, and SaaS subscription plans."

        # 8. Extract intent, lead qualification, and score
        lead_intel = await cls._extract_lead_intelligence(
            groq_client=groq_client,
            user_message=user_message_text,
            ai_reply=ai_reply,
            conversation_history=conversation_history,
            max_discount_allowed=config.max_discount_percent,
            catalog_summary=catalog_summary,
        )

        # 9. Update conversation status
        stmt_conv = select(Conversation).where(Conversation.id == conversation_id)
        conv = (await db.execute(stmt_conv)).scalar_one()
        conv.last_activity_at = _utcnow()
        conv.detected_intent = lead_intel.get("intent", "general_inquiry")

        if lead_intel.get("needs_human_handoff"):
            conv.status = "WAITING_SALES"

        # 10. Automatic Lead creation / update
        lead_id = None
        score = lead_intel.get("score", 0)
        if score >= 40 or lead_intel.get("buying_intent"):
            lead_id = await cls._sync_lead(
                db=db,
                tenant_id=tenant_id,
                customer_id=customer_id,
                conversation_id=conversation_id,
                lead_intel=lead_intel,
            )

        await db.commit()

        return {
            "reply": ai_reply,
            "intent": lead_intel.get("intent"),
            "score": score,
            "score_reason": lead_intel.get("score_reason"),
            "needs_human_handoff": lead_intel.get("needs_human_handoff", False),
            "lead_id": str(lead_id) if lead_id else None,
        }

    @classmethod
    async def process_customer_message(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_text: str,
    ) -> Dict[str, Any]:
        """
        Main entry point for customer messages from WhatsApp or Web Simulator.
        1. Saves customer message and prepares context.
        2. Calls Groq LLM for natural sales response.
        3. Extracts intent, lead qualification, and 0-100 lead score.
        4. Updates conversation state and creates/updates Lead record.
        """
        llm_messages, groq_client, config, _ = await cls.prepare_chat_context(
            db=db,
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            user_message_text=user_message_text,
        )

        try:
            ai_reply = await groq_client.chat_completion(llm_messages, temperature=0.3)
        except Exception as exc:
            logger.error("Groq chat completion failed: %s", exc)
            ai_reply = "Thank you for reaching out! Our sales team has received your message and will get back to you shortly."

        return await cls.finalize_ai_response(
            db=db,
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            user_message_text=user_message_text,
            ai_reply=ai_reply,
            groq_client=groq_client,
            config=config,
        )

    @classmethod
    async def _extract_lead_intelligence(
        cls,
        groq_client: GroqClient,
        user_message: str,
        ai_reply: str,
        conversation_history: List[Message],
        max_discount_allowed: float,
        catalog_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyze customer conversation and extract structured lead intelligence.
        Returns: {intent, buying_intent, budget, timeline, requirements, score, score_reason, needs_human_handoff}
        """
        # Fast fallback heuristics
        msg_lower = user_message.lower()
        reply_lower = ai_reply.lower()

        needs_human = any(w in msg_lower for w in ["human", "agent", "representative", "speak to someone", "call me", "manager"])
        
        # Detect if assistant explicitly declined product or stated it is out of scope / not sold
        is_assistant_declining_product = any(
            phrase in reply_lower
            for phrase in [
                "don't sell", "do not sell", "not sell",
                "don't offer", "do not offer", "not offer",
                "outside our scope", "not in our catalog", "we don't carry", "we do not carry"
            ]
        )

        # Check discount request above allowed
        discount_match = re.search(r"(\d+)\s*%", user_message)
        if discount_match and float(discount_match.group(1)) > max_discount_allowed:
            needs_human = True

        recent_context_str = "\n".join([
            f"{'Customer' if m.sender_type == 'CUSTOMER' else 'Assistant'}: {m.content}"
            for m in conversation_history[-6:]
        ])

        catalog_ctx = catalog_summary or "Software products, SaaS subscriptions, and digital services."

        prompt = f"""You are an expert sales qualification system. Analyze this sales conversation between a customer and the AI sales assistant.

OFFICIAL PRODUCT CATALOG & OFFERINGS OF THIS COMPANY:
{catalog_ctx}

RECENT CONVERSATION CONTEXT:
{recent_context_str}

LATEST CUSTOMER MESSAGE: "{user_message}"
LATEST ASSISTANT REPLY: "{ai_reply}"

CRITICAL QUALIFICATION & SCORING RULES:
1. RELEVANCE TO BUSINESS: The customer must be inquiring about products or services actually offered by this company (see catalog above).
2. OUT-OF-SCOPE / IRRELEVANT / NON-EXISTENT PRODUCTS:
   - If the customer asks to buy or inquires about items/services NOT sold by this company (e.g. asking for chocolate, shoes, food, pizza, cryptocurrency, or off-topic requests):
     * "intent": "out_of_scope"
     * "buying_intent": false
     * "score": 5 to 15 (Never give high scores or priority to non-existent or irrelevant products!)
     * "score_reason": Clearly state that the requested product is not offered by this company.
3. IN-SCOPE LEADS:
   - High Score (75-100): Strong buying intent for company products, asking for payment/checkout link for real products, booking demo, clear budget or timeline.
   - Warm Score (40-74): Inquiring about company pricing, product capabilities, comparing plans.
   - Low Score (10-39): Casual greeting, vague remarks, polite acknowledgments.

Extract sales intelligence as valid JSON ONLY with these fields:
{{
  "intent": "product_inquiry" | "pricing_inquiry" | "demo_request" | "quote_request" | "complaint" | "human_request" | "general_inquiry" | "out_of_scope",
  "buying_intent": true | false,
  "budget": float or null,
  "timeline": "immediate" | "within 30 days" | "1-3 months" | "exploring" or null,
  "requirements": "brief summary of customer need" or null,
  "score": integer from 0 to 100 based on genuine purchase intent for THIS company's offerings,
  "score_reason": "concise explanation of why this score was assigned",
  "needs_human_handoff": true | false
}}"""

        try:
            intel_text = await groq_client.chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(intel_text)
            if "score" not in data and "qualification_score" in data:
                data["score"] = data["qualification_score"]
            if "score_reason" not in data and "qualification_reason" in data:
                data["score_reason"] = data["qualification_reason"]
            try:
                data["score"] = int(data.get("score", 0))
            except (ValueError, TypeError):
                data["score"] = 0

            # Guard against out-of-scope hallucination or high scores on unoffered goods
            if is_assistant_declining_product or data.get("intent") == "out_of_scope":
                data["intent"] = "out_of_scope"
                data["buying_intent"] = False
                data["score"] = min(data["score"], 15)
                if not data.get("score_reason") or "not" not in data.get("score_reason", "").lower():
                    data["score_reason"] = "Requested product is not offered by this company."

            if needs_human:
                data["needs_human_handoff"] = True
            return data
        except Exception:
            # Fallback heuristic calculation if LLM JSON fails
            if is_assistant_declining_product:
                return {
                    "intent": "out_of_scope",
                    "buying_intent": False,
                    "budget": None,
                    "timeline": None,
                    "requirements": user_message[:100],
                    "score": 10,
                    "score_reason": "Requested product is not offered by this company.",
                    "needs_human_handoff": needs_human,
                }

            score = 30
            intent = "general_inquiry"
            if any(w in msg_lower for w in ["price", "cost", "how much", "quote"]):
                intent = "pricing_inquiry"
                score += 30
            if any(w in msg_lower for w in ["demo", "trial"]):
                intent = "demo_request"
                score += 35
            elif any(w in msg_lower for w in ["buy", "purchase"]):
                intent = "product_inquiry"
                score += 20
            return {
                "intent": intent,
                "buying_intent": score >= 50,
                "budget": None,
                "timeline": "within 30 days" if score >= 60 else "exploring",
                "requirements": user_message[:100],
                "score": min(score, 100),
                "score_reason": f"Detected {intent} with active engagement.",
                "needs_human_handoff": needs_human,
            }

    @classmethod
    async def _sync_lead(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID,
        lead_intel: Dict[str, Any],
    ) -> uuid.UUID:
        """
        Creates or updates a Lead record for the customer.
        """
        # Check if lead already exists for this conversation
        stmt = select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.conversation_id == conversation_id,
        )
        lead = (await db.execute(stmt)).scalar_one_or_none()

        score = lead_intel.get("score", 50)
        priority = "HOT" if score >= 80 else ("HIGH" if score >= 65 else ("MEDIUM" if score >= 40 else "LOW"))

        if not lead:
            # Get customer name for title
            cust_stmt = select(Customer).where(Customer.id == customer_id)
            cust = (await db.execute(cust_stmt)).scalar_one_or_none()
            cust_name = cust.name or cust.phone or "New Lead"

            lead = Lead(
                tenant_id=tenant_id,
                customer_id=customer_id,
                conversation_id=conversation_id,
                title=f"Opportunity - {cust_name}",
                status="QUALIFIED" if score >= 60 else "NEW",
                score=score,
                score_reason=lead_intel.get("score_reason"),
                requirements=lead_intel.get("requirements"),
                budget=lead_intel.get("budget"),
                timeline=lead_intel.get("timeline"),
                priority=priority,
                source="WHATSAPP",
            )
            db.add(lead)
        else:
            # Update existing lead with latest score & requirements
            lead.score = score
            if lead_intel.get("score_reason"):
                lead.score_reason = lead_intel.get("score_reason")
            if lead_intel.get("requirements"):
                lead.requirements = lead_intel.get("requirements")
            if lead_intel.get("budget"):
                lead.budget = lead_intel.get("budget")
            if lead_intel.get("timeline"):
                lead.timeline = lead_intel.get("timeline")
            lead.priority = priority
            if score >= 60 and lead.status == "NEW":
                lead.status = "QUALIFIED"
            elif score < 40 and lead.status == "NEW":
                lead.status = "NEW"

        await db.flush()
        return lead.id

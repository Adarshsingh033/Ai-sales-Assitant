"""
Controlled business operations the assistant may call (PRD §19, §28).

The model never touches the database directly: it can only invoke these tools, each tenant-scoped and mostly
read-only. Tools are also the source of truth for money — every figure they return is registered as a
"verified amount", which is what the guardrails compare reply prices against.
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.product import Product
from app.services.ai.llm import ToolCall
from app.services.inventory_service import InventoryService
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check live warehouse stock for a product by name or SKU.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string", "description": "Product name or SKU"}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Get authoritative price, category, features and stock for a product by name or SKU.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string", "description": "Product name or SKU"}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_discount",
            "description": (
                "Calculate the price for a quantity of a product with a requested discount. Applies the company's "
                "maximum automatic discount and says whether a human must approve the rest. Use for ANY total or "
                "discounted price."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product name or SKU"},
                    "quantity": {"type": "integer", "description": "Number of units/licenses (default 1)"},
                    "requested_discount_percent": {"type": "number", "description": "Discount the customer asked for (default 0)"},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the company's verified documents (policies, FAQs, manuals) for a specific question.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Standalone search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_human_handoff",
            "description": "Hand the conversation to a human sales representative (customer asked for a person, complaint, legal/contract, discount beyond the limit, or information cannot be verified).",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "Short reason for the sales team"}},
                "required": ["reason"],
            },
        },
    },
]


@dataclass
class ToolContext:
    db: AsyncSession
    tenant_id: uuid.UUID
    max_discount_percent: float
    verified_amounts: Set[float] = field(default_factory=set)
    knowledge_texts: List[str] = field(default_factory=list)
    handoff_reason: Optional[str] = None
    trace: List[Dict[str, Any]] = field(default_factory=list)


async def _resolve_product(ctx: ToolContext, query: str) -> Optional[Product]:
    q = (query or "").strip()
    if not q:
        return None
    base = select(Product).where(Product.tenant_id == ctx.tenant_id, Product.is_active.is_(True))
    by_sku = (await ctx.db.execute(base.where(func.lower(Product.sku) == q.lower()))).scalars().first()
    if by_sku:
        return by_sku
    by_name = (
        await ctx.db.execute(base.where(func.lower(Product.name).like(f"%{q.lower()}%")).order_by(Product.name).limit(1))
    ).scalars().first()
    if by_name:
        return by_name
    found = await RAGService.retrieve(
        ctx.db, ctx.tenant_id, q, top_k=1, min_score=settings.RAG_MIN_SCORE_PRODUCT, source_type="PRODUCT"
    )
    if found.matches and found.matches[0].get("product_id"):
        pid = uuid.UUID(found.matches[0]["product_id"])
        return (await ctx.db.execute(base.where(Product.id == pid))).scalars().first()
    return None


def _money(ctx: ToolContext, value: float) -> float:
    value = round(float(value), 2)
    ctx.verified_amounts.add(value)
    return value


async def _check_stock(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    p = await _resolve_product(ctx, str(args.get("product", "")))
    if not p:
        return {"found": False, "message": "No matching product in the catalog."}
    stock = (await InventoryService.get_stock_for_products(ctx.db, ctx.tenant_id, [p.id])).get(p.id)
    if not stock:
        return {"found": True, "product": p.name, "tracked": False, "message": "No inventory records for this product."}
    total = sum(r["available"] for r in stock)
    return {"found": True, "product": p.name, "in_stock": total > 0, "total_available": total, "locations": stock}


async def _product_details(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    p = await _resolve_product(ctx, str(args.get("product", "")))
    if not p:
        return {"found": False, "message": "No matching product in the catalog."}
    stock = (await InventoryService.get_stock_for_products(ctx.db, ctx.tenant_id, [p.id])).get(p.id)
    return {
        "found": True,
        "name": p.name,
        "sku": p.sku,
        "category": p.category,
        "price": _money(ctx, p.price),
        "currency": p.currency,
        "description": p.description,
        "features": p.features,
        "stock": InventoryService.summarize_stock(stock),
    }


async def _calculate_discount(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    p = await _resolve_product(ctx, str(args.get("product", "")))
    if not p:
        return {"found": False, "message": "No matching product in the catalog."}
    try:
        qty = max(1, int(args.get("quantity") or 1))
        requested = max(0.0, float(args.get("requested_discount_percent") or 0))
    except (TypeError, ValueError):
        return {"error": "quantity and requested_discount_percent must be numbers"}
    allowed = min(requested, ctx.max_discount_percent)
    needs_human = requested > ctx.max_discount_percent + 0.01
    unit = float(p.price)
    subtotal = unit * qty
    discount_amount = subtotal * allowed / 100.0
    total = subtotal - discount_amount
    if needs_human:
        ctx.handoff_reason = (
            f"Customer requested {requested:g}% discount on {p.name} (automatic limit {ctx.max_discount_percent:g}%)"
        )
    return {
        "found": True,
        "product": p.name,
        "currency": p.currency,
        "unit_price": _money(ctx, unit),
        "quantity": qty,
        "subtotal": _money(ctx, subtotal),
        "discount_percent_applied": allowed,
        "discount_amount": _money(ctx, discount_amount),
        "total": _money(ctx, total),
        "requested_discount_percent": requested,
        "requires_human_approval": needs_human,
        "note": (
            f"Offer at most {ctx.max_discount_percent:g}%; tell the customer a sales manager will review the higher request."
            if needs_human else "Within the automatic discount limit."
        ),
    }


async def _search_knowledge(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    res = await RAGService.retrieve(ctx.db, ctx.tenant_id, str(args.get("query", "")), top_k=3, source_type="DOCUMENT")
    ctx.knowledge_texts.extend(m["content"] for m in res.matches)
    return {
        "matches": [
            {"source": m["filename"], "page": m["page"], "text": m["content"][:700]} for m in res.matches
        ],
        "confidence": res.confidence,
        "message": None if res.matches else "No verified document covers this.",
    }


async def _handoff(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    ctx.handoff_reason = str(args.get("reason") or "Assistant requested human help")[:300]
    return {"status": "handoff_requested", "message": "A sales specialist will follow up. Tell the customer this politely."}


_HANDLERS = {
    "check_stock": _check_stock,
    "get_product_details": _product_details,
    "calculate_discount": _calculate_discount,
    "search_knowledge": _search_knowledge,
    "request_human_handoff": _handoff,
}


async def run_tool(ctx: ToolContext, call: ToolCall) -> Dict[str, Any]:
    started = time.monotonic()
    handler = _HANDLERS.get(call.name)
    ok = True
    try:
        if handler is None:
            result: Dict[str, Any] = {"error": f"Unknown tool '{call.name}'."}
            ok = False
        else:
            result = await handler(ctx, call.arguments)
    except Exception as exc:  # a failing tool must not fail the turn
        logger.exception("Tool %s failed", call.name)
        result = {"error": "The lookup failed; do not guess — say you will confirm and hand off if needed."}
        ok = False
        await ctx.db.rollback()
    ctx.trace.append(
        {
            "name": call.name,
            "args": call.arguments,
            "ok": ok,
            "ms": int((time.monotonic() - started) * 1000),
            "result_preview": json.dumps(result, default=str)[:300],
        }
    )
    return result

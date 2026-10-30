"""
Customer memory — durable facts carried across conversations (PRD §32).

Facts are extracted per turn by the analysis step and merged here: scalar facts overwrite when a new value
appears, list facts are unioned and capped. Everything is size-limited so memory can never bloat the prompt.
"""
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.customer_memory import CustomerMemory
from app.services.ai.analysis import TurnAnalysis

_MAX_FACTS = 14
_MAX_VALUE = 140
_LIST_KEYS = ("products_discussed", "objections_raised")


async def load_memory(db: AsyncSession, customer_id: uuid.UUID) -> Optional[CustomerMemory]:
    return (
        await db.execute(select(CustomerMemory).where(CustomerMemory.customer_id == customer_id))
    ).scalar_one_or_none()


def memory_text(memory: Optional[CustomerMemory], max_chars: int) -> Optional[str]:
    if not memory or not memory.facts:
        return None
    lines: List[str] = []
    for key, value in memory.facts.items():
        shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        lines.append(f"- {key.replace('_', ' ')}: {shown}")
    text = "\n".join(lines)
    return text[:max_chars]


def merge_facts(existing: Dict[str, Any], analysis: TurnAnalysis) -> Dict[str, Any]:
    facts = dict(existing or {})

    for key, value in (analysis.customer_facts or {}).items():
        key = str(key).strip().lower().replace(" ", "_")[:40]
        if not key or value in (None, ""):
            continue
        facts[key] = str(value)[:_MAX_VALUE]

    if analysis.budget:
        facts["budget"] = f"{analysis.budget:g}"
    if analysis.timeline:
        facts["timeline"] = analysis.timeline
    if analysis.company_size:
        facts["company_size"] = str(analysis.company_size)
    if analysis.requirements:
        facts["latest_requirement"] = analysis.requirements[:_MAX_VALUE]

    for key, new_items in (
        ("products_discussed", analysis.products_mentioned),
        ("objections_raised", analysis.objections),
    ):
        if new_items:
            merged = list(facts.get(key) or [])
            for item in new_items:
                item = str(item).strip()[:60]
                if item and item not in merged:
                    merged.append(item)
            facts[key] = merged[-10:]

    if len(facts) > _MAX_FACTS:  # keep list facts + the most recent scalars
        keep = {k: v for k, v in facts.items() if k in _LIST_KEYS}
        for k, v in reversed(list(facts.items())):
            if len(keep) >= _MAX_FACTS:
                break
            keep.setdefault(k, v)
        facts = keep
    return facts


async def update_memory(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    customer: Customer,
    analysis: TurnAnalysis,
) -> CustomerMemory:
    memory = await load_memory(db, customer.id)
    if memory is None:
        memory = CustomerMemory(tenant_id=tenant_id, customer_id=customer.id, facts={})
        db.add(memory)
    memory.facts = merge_facts(memory.facts, analysis)  # new dict object → change is tracked
    if analysis.language:
        memory.language = analysis.language[:20]
        customer.preferred_language = analysis.language[:20]
    company = (analysis.customer_facts or {}).get("company")
    if company and not customer.company_name:
        customer.company_name = str(company)[:255]
    await db.flush()
    return memory

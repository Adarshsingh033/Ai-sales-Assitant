"""
Warehouse & Product Inventory Service.
Manages custom warehouse locations, real-time stock levels, low-stock alerts,
and generates formatted inventory context for AI Sales Engine inquiries.
"""
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inventory import ProductInventory, Warehouse
from app.models.product import Product
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class InventoryService:
    @staticmethod
    async def ensure_default_warehouses(db: AsyncSession, tenant_id: uuid.UUID) -> Warehouse:
        """Provision a default central fulfillment warehouse if none exist."""
        stmt = select(Warehouse).where(Warehouse.tenant_id == tenant_id).limit(1)
        wh = (await db.execute(stmt)).scalar_one_or_none()
        if not wh:
            wh = Warehouse(
                tenant_id=tenant_id,
                name="Main Central Fulfillment Hub",
                code="WH-MAIN-01",
                city="Chicago, IL",
                is_active=True,
            )
            db.add(wh)
            await db.commit()
            await db.refresh(wh)
            logger.info("Provisioned default warehouse for tenant %s: %s", tenant_id, wh.code)
        return wh

    @staticmethod
    async def list_warehouses(db: AsyncSession, tenant_id: uuid.UUID) -> List[Warehouse]:
        """List active warehouses for tenant."""
        stmt = select(Warehouse).where(Warehouse.tenant_id == tenant_id).order_by(Warehouse.created_at.asc())
        warehouses = list((await db.execute(stmt)).scalars().all())
        if not warehouses:
            default_wh = await InventoryService.ensure_default_warehouses(db, tenant_id)
            return [default_wh]
        return warehouses

    @staticmethod
    async def create_warehouse(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        name: str,
        code: str,
        city: Optional[str] = None,
    ) -> Warehouse:
        """Create a new warehouse location."""
        wh = Warehouse(
            tenant_id=tenant_id,
            name=name.strip(),
            code=code.strip().upper(),
            city=city.strip() if city else None,
            is_active=True,
        )
        db.add(wh)
        await db.commit()
        await db.refresh(wh)
        return wh

    @staticmethod
    async def get_stock_by_product(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> List[ProductInventory]:
        """Get inventory across all warehouses for a single product."""
        stmt = (
            select(ProductInventory)
            .options(selectinload(ProductInventory.warehouse))
            .where(
                ProductInventory.tenant_id == tenant_id,
                ProductInventory.product_id == product_id,
            )
        )
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def list_inventory(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        low_stock_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List all inventory items joined with products and warehouses."""
        stmt = (
            select(ProductInventory)
            .options(
                selectinload(ProductInventory.product),
                selectinload(ProductInventory.warehouse),
            )
            .where(ProductInventory.tenant_id == tenant_id)
        )
        if low_stock_only:
            stmt = stmt.where(ProductInventory.quantity_available <= ProductInventory.low_stock_threshold)

        items = (await db.execute(stmt)).scalars().all()
        result = []
        for item in items:
            result.append({
                "id": str(item.id),
                "product_id": str(item.product_id),
                "product_name": item.product.name if item.product else "Unknown",
                "product_sku": item.product.sku if item.product else None,
                "product_price": float(item.product.price) if item.product else 0.0,
                "warehouse_id": str(item.warehouse_id),
                "warehouse_name": item.warehouse.name if item.warehouse else "Unknown",
                "warehouse_code": item.warehouse.code if item.warehouse else "N/A",
                "warehouse_city": item.warehouse.city if item.warehouse else None,
                "quantity_on_hand": item.quantity_on_hand,
                "quantity_reserved": item.quantity_reserved,
                "quantity_available": item.quantity_available,
                "low_stock_threshold": item.low_stock_threshold,
                "is_low_stock": item.quantity_available <= item.low_stock_threshold,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            })
        return result

    @staticmethod
    async def update_stock(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        quantity_on_hand: int,
        quantity_reserved: int = 0,
        low_stock_threshold: int = 5,
    ) -> ProductInventory:
        """Set or update stock levels for product at warehouse."""
        stmt = select(ProductInventory).where(
            ProductInventory.tenant_id == tenant_id,
            ProductInventory.product_id == product_id,
            ProductInventory.warehouse_id == warehouse_id,
        )
        inv = (await db.execute(stmt)).scalar_one_or_none()
        quantity_available = max(0, quantity_on_hand - quantity_reserved)

        if not inv:
            inv = ProductInventory(
                tenant_id=tenant_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                quantity_on_hand=quantity_on_hand,
                quantity_reserved=quantity_reserved,
                quantity_available=quantity_available,
                low_stock_threshold=low_stock_threshold,
            )
            db.add(inv)
        else:
            inv.quantity_on_hand = quantity_on_hand
            inv.quantity_reserved = quantity_reserved
            inv.quantity_available = quantity_available
            inv.low_stock_threshold = low_stock_threshold
            inv.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(inv)

        # Trigger low stock alert if below threshold
        if quantity_available <= low_stock_threshold:
            stmt_p = select(Product).where(Product.id == product_id)
            p = (await db.execute(stmt_p)).scalar_one_or_none()
            prod_name = p.name if p else "Product"
            await NotificationService.create_notification(
                db=db,
                tenant_id=tenant_id,
                title=f"⚠️ Low Stock Alert: {prod_name}",
                message=f"Only {quantity_available} units available in warehouse. (Threshold: {low_stock_threshold})",
                notif_type="INVENTORY_LOW",
                link_url="#/products",
            )

        return inv

    @staticmethod
    async def get_inventory_context_for_ai(
        db: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> str:
        """
        Formats current product warehouse stock levels into concise text
        for system prompt injection into AISalesEngine.
        """
        stmt = (
            select(ProductInventory)
            .options(
                selectinload(ProductInventory.product),
                selectinload(ProductInventory.warehouse),
            )
            .where(ProductInventory.tenant_id == tenant_id)
        )
        items = (await db.execute(stmt)).scalars().all()
        if not items:
            return ""

        lines = ["CURRENT REAL-TIME WAREHOUSE INVENTORY & STOCK LEVELS:"]
        for it in items:
            p_name = it.product.name if it.product else "Item"
            wh_name = it.warehouse.name if it.warehouse else "Warehouse"
            city = f" ({it.warehouse.city})" if it.warehouse and it.warehouse.city else ""
            status = "IN STOCK" if it.quantity_available > 0 else "OUT OF STOCK"
            lines.append(
                f"- {p_name}: {it.quantity_available} units available [{status}] at {wh_name}{city} (Total on hand: {it.quantity_on_hand})"
            )

        return "\n".join(lines)

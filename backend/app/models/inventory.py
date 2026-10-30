"""
Warehouse and Product Inventory ORM Models.
Stores custom warehouse locations and real-time product stock levels.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., WH-MAIN-01
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    inventory_items = relationship("ProductInventory", back_populates="warehouse", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_warehouses_tenant_code"),
        Index("ix_warehouses_tenant_active", "tenant_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Warehouse id={self.id} code={self.code} name={self.name}>"


class ProductInventory(Base):
    __tablename__ = "product_inventory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    warehouse = relationship("Warehouse", back_populates="inventory_items")
    product = relationship("Product")

    __table_args__ = (
        UniqueConstraint("tenant_id", "product_id", "warehouse_id", name="uq_product_inventory_tenant_prod_wh"),
        Index("ix_prod_inv_tenant_prod", "tenant_id", "product_id"),
        Index("ix_prod_inv_tenant_wh", "tenant_id", "warehouse_id"),
    )

    def __repr__(self) -> str:
        return f"<ProductInventory prod={self.product_id} wh={self.warehouse_id} avail={self.quantity_available}>"

"""
Tenant Admin Warehouse & Product Inventory API Router.
Handles warehouse location CRUD and stock adjustments.
"""
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.user import User
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/tenant-admin/inventory", tags=["Tenant Admin - Inventory & Warehouses"])


class WarehouseCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    code: str = Field(..., min_length=2, max_length=50)
    city: Optional[str] = Field(None, max_length=100)


class StockUpdateRequest(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity_on_hand: int = Field(..., ge=0)
    quantity_reserved: int = Field(0, ge=0)
    low_stock_threshold: int = Field(5, ge=0)


@router.get("/warehouses", summary="List Warehouses")
async def list_warehouses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> List[Dict[str, Any]]:
    """List all warehouses for the tenant."""
    warehouses = await InventoryService.list_warehouses(db, current_user.tenant_id)
    return [
        {
            "id": str(w.id),
            "name": w.name,
            "code": w.code,
            "city": w.city,
            "is_active": w.is_active,
            "created_at": w.created_at.isoformat(),
        }
        for w in warehouses
    ]


@router.post("/warehouses", summary="Create Warehouse")
async def create_warehouse(
    data: WarehouseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    """Create a new warehouse location."""
    wh = await InventoryService.create_warehouse(
        db=db,
        tenant_id=current_user.tenant_id,
        name=data.name,
        code=data.code,
        city=data.city,
    )
    return {
        "id": str(wh.id),
        "name": wh.name,
        "code": wh.code,
        "city": wh.city,
        "is_active": wh.is_active,
    }


@router.get("/stock", summary="List Product Inventory Across Warehouses")
async def list_inventory(
    low_stock_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> List[Dict[str, Any]]:
    """Get stock levels for products across all warehouses."""
    return await InventoryService.list_inventory(
        db=db,
        tenant_id=current_user.tenant_id,
        low_stock_only=low_stock_only,
    )


@router.put("/stock", summary="Update Product Stock Level")
async def update_stock(
    data: StockUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> Dict[str, Any]:
    """Set or update stock levels for a product in a warehouse."""
    inv = await InventoryService.update_stock(
        db=db,
        tenant_id=current_user.tenant_id,
        product_id=data.product_id,
        warehouse_id=data.warehouse_id,
        quantity_on_hand=data.quantity_on_hand,
        quantity_reserved=data.quantity_reserved,
        low_stock_threshold=data.low_stock_threshold,
    )
    return {
        "id": str(inv.id),
        "product_id": str(inv.product_id),
        "warehouse_id": str(inv.warehouse_id),
        "quantity_on_hand": inv.quantity_on_hand,
        "quantity_reserved": inv.quantity_reserved,
        "quantity_available": inv.quantity_available,
        "low_stock_threshold": inv.low_stock_threshold,
        "updated_at": inv.updated_at.isoformat(),
    }

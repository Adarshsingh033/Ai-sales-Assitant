"""
Product Catalog endpoints for Tenant Admin and Sales Users.
Provides tenant-scoped CRUD operations for products and services.
"""
import math
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_sales_user, require_tenant_admin
from app.models.product import Product
from app.models.user import User
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)

router = APIRouter(prefix="/tenant-admin/products", tags=["Tenant Admin Products"])


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Product",
)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> ProductResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required to manage products.",
        )

    product = Product(
        tenant_id=current_user.tenant_id,
        name=data.name.strip(),
        sku=data.sku.strip() if data.sku else None,
        category=data.category.strip() if data.category else None,
        description=data.description.strip() if data.description else None,
        price=data.price,
        currency=data.currency.strip().upper(),
        features=data.features.strip() if data.features else None,
        is_active=data.is_active,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return ProductResponse.model_validate(product)


@router.get(
    "",
    response_model=ProductListResponse,
    summary="List Products",
)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ProductListResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required to list products.",
        )

    stmt = select(Product).where(Product.tenant_id == current_user.tenant_id)

    if is_active is not None:
        stmt = stmt.where(Product.is_active == is_active)

    if category:
        stmt = stmt.where(Product.category == category.strip())

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Product.name).like(pattern)
            | func.lower(Product.sku).like(pattern)
            | func.lower(Product.description).like(pattern)
        )

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Pagination & sorting
    stmt = (
        stmt.order_by(Product.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    pages = math.ceil(total / page_size) if page_size > 0 else 1

    return ProductListResponse(
        items=[ProductResponse.model_validate(p) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get Product",
)
async def get_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ProductResponse:
    stmt = select(Product).where(
        Product.id == product_id,
        Product.tenant_id == current_user.tenant_id,
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    return ProductResponse.model_validate(product)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update Product",
)
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> ProductResponse:
    stmt = select(Product).where(
        Product.id == product_id,
        Product.tenant_id == current_user.tenant_id,
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    update_dict = data.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(product, field, val)

    await db.commit()
    await db.refresh(product)
    return ProductResponse.model_validate(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Product",
)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> None:
    stmt = select(Product).where(
        Product.id == product_id,
        Product.tenant_id == current_user.tenant_id,
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    await db.delete(product)
    await db.commit()

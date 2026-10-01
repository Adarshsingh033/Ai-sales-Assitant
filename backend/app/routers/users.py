import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.user import UserProfileResponse, ChangePasswordRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

UPLOAD_DIR = Path("uploads/profiles")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get Current User Profile",
)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    return current_user


@router.put(
    "/me",
    response_model=UserProfileResponse,
    summary="Update Current User Profile",
)
async def update_my_profile(
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    profile_image: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    
    if first_name is not None:
        current_user.first_name = first_name
    if last_name is not None:
        current_user.last_name = last_name
    if phone_number is not None:
        current_user.phone_number = phone_number

    if profile_image:
        # Validate file extension
        ext = profile_image.filename.split(".")[-1].lower()
        if ext not in ["jpg", "jpeg", "png", "webp"]:
            raise HTTPException(status_code=400, detail="Invalid image format. Allowed: jpg, jpeg, png, webp")
        import base64
        
        # Read the file content and convert to base64
        content = await profile_image.read()
        b64_str = base64.b64encode(content).decode("utf-8")
        
        # Save as a data URI
        mime_type = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"
        current_user.profile_image_url = f"data:{mime_type};base64,{b64_str}"

    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    
    return current_user

@router.put(
    "/me/password",
    summary="Update Current User Password",
)
async def update_my_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.core.security import verify_password, hash_password
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect old password")
    
    current_user.password_hash = hash_password(req.new_password)
    db.add(current_user)
    await db.commit()
    
    return {"detail": "Password updated successfully"}

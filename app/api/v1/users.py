from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user, require_role, hash_password
from app.core.logging import logger
from app.models.models import User, Project, ProjectMember
from app.models.enums import UserRole
from app.schemas.schemas import UserCreate, UserUpdate, UserResponse, UserMeResponse, ProjectResponse

router = APIRouter(prefix="/users", tags=["Users"])

@router.get("/me", response_model=UserMeResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return current_user

@router.get("", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR))
):
    """List all users (Administrator only)."""
    result = await db.execute(select(User).order_by(User.created_date.desc()))
    return result.scalars().all()

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user by ID."""
    if current_user.role != UserRole.ADMINISTRATOR and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user

@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR))
):
    """Create new user (Administrator only)."""
    existing = await db.execute(select(User).where(User.email == user_data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        display_name=user_data.display_name,
        avatar_url=user_data.avatar_url,
        role=user_data.role,
        capacity_per_day=user_data.capacity_per_day
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    logger.info(f"User created: {user.email} by {current_user.email}")
    return user

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update user."""
    is_admin = current_user.role == UserRole.ADMINISTRATOR
    is_self = current_user.id == user_id

    if not is_admin and not is_self:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Non-admin can only update profile fields
    if not is_admin and (user_data.role or user_data.is_active is not None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify role or status")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = user_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)

    logger.info(f"User updated: {user.email} by {current_user.email}")
    return user

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR))
):
    """Deactivate user (Administrator only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    await db.flush()

    logger.info(f"User deactivated: {user.email} by {current_user.email}")

@router.get("/{user_id}/projects", response_model=List[ProjectResponse])
async def get_user_projects(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all projects for a specific user."""
    # Только админ или сам пользователь
    if current_user.role != UserRole.ADMINISTRATOR and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Проверяем существование пользователя
    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Получаем проекты через ProjectMember
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user_id, Project.is_active == True)
        .order_by(Project.created_date.desc())
    )
    projects = result.scalars().all()

    response = []
    for project in projects:
        count_result = await db.execute(
            select(func.count()).select_from(ProjectMember)
            .where(ProjectMember.project_id == project.id)
        )
        member_count = count_result.scalar()
        proj_dict = ProjectResponse.model_validate(project).model_dump()
        proj_dict["member_count"] = member_count
        response.append(ProjectResponse(**proj_dict))

    return response

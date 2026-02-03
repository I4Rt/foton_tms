from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import Holiday, NonWorkingDay, User
from app.models.enums import UserRole
from app.schemas.schemas import (
    HolidayCreate, HolidayResponse,
    NonWorkingDayCreate, NonWorkingDayResponse
)

router = APIRouter(tags=["Calendar"])

# ===== Holidays =====
@router.get("/holidays", response_model=List[HolidayResponse])
async def list_holidays(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all holidays."""
    result = await db.execute(select(Holiday).order_by(Holiday.date))
    return result.scalars().all()

@router.post("/holidays", response_model=HolidayResponse, status_code=status.HTTP_201_CREATED)
async def create_holiday(
    holiday_data: HolidayCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR))
):
    """Create holiday (Administrator only)."""
    # Check if already exists
    existing = await db.execute(
        select(Holiday).where(Holiday.date == holiday_data.date)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Holiday already exists for this date")
    
    holiday = Holiday(
        date=holiday_data.date,
        description=holiday_data.description
    )
    
    db.add(holiday)
    await db.flush()
    await db.refresh(holiday)
    
    logger.info(f"Holiday created: {holiday.date} by {current_user.email}")
    return holiday

@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR))
):
    """Delete holiday (Administrator only)."""
    result = await db.execute(select(Holiday).where(Holiday.id == holiday_id))
    holiday = result.scalar_one_or_none()
    if not holiday:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holiday not found")
    
    await db.delete(holiday)
    await db.flush()
    
    logger.info(f"Holiday deleted: {holiday.date} by {current_user.email}")

# ===== Non-Working Days =====
@router.get("/users/{user_id}/nonworkingdays", response_model=List[NonWorkingDayResponse])
async def list_user_non_working_days(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List user's non-working days."""
    # Only user themselves or admin can view
    if current_user.id != user_id and current_user.role != UserRole.ADMINISTRATOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    result = await db.execute(
        select(NonWorkingDay)
        .where(NonWorkingDay.user_id == user_id)
        .order_by(NonWorkingDay.date)
    )
    return result.scalars().all()

@router.post("/users/{user_id}/nonworkingdays", response_model=NonWorkingDayResponse, status_code=status.HTTP_201_CREATED)
async def create_non_working_day(
    user_id: UUID,
    day_data: NonWorkingDayCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create non-working day for user."""
    # Only user themselves or admin can create
    if current_user.id != user_id and current_user.role != UserRole.ADMINISTRATOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Check if already exists
    existing = await db.execute(
        select(NonWorkingDay).where(
            NonWorkingDay.user_id == user_id,
            NonWorkingDay.date == day_data.date
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Non-working day already exists for this date")
    
    non_working_day = NonWorkingDay(
        user_id=user_id,
        date=day_data.date,
        type=day_data.type,
        description=day_data.description
    )
    
    db.add(non_working_day)
    await db.flush()
    await db.refresh(non_working_day)
    
    logger.info(f"Non-working day created for user {user_id}: {non_working_day.date}")
    return non_working_day

@router.delete("/users/{user_id}/nonworkingdays/{day_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_non_working_day(
    user_id: UUID,
    day_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete non-working day."""
    # Only user themselves or admin can delete
    if current_user.id != user_id and current_user.role != UserRole.ADMINISTRATOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    result = await db.execute(
        select(NonWorkingDay).where(
            NonWorkingDay.id == day_id,
            NonWorkingDay.user_id == user_id
        )
    )
    day = result.scalar_one_or_none()
    if not day:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Non-working day not found")
    
    await db.delete(day)
    await db.flush()
    
    logger.info(f"Non-working day deleted for user {user_id}: {day.date}")

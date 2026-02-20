from typing import List
from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, Date

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import WorkSession, User
from app.schemas.schemas import WorkSessionResponse


router = APIRouter(prefix="/users/{user_id}/sessions", tags=["Work Sessions"])


@router.get("", response_model=List[WorkSessionResponse])
async def get_user_sessions(
    user_id: UUID,
    date_from: date = Query(..., description="Start date (inclusive)"),
    date_to: date = Query(..., description="End date (inclusive)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all work sessions for a user where started_at falls within the date range."""
    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date_from must be <= date_to"
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.execute(
        select(WorkSession)
        .where(
            WorkSession.user_id == user_id,
            cast(WorkSession.started_at, Date) >= date_from,
            cast(WorkSession.started_at, Date) <= date_to
        )
        .order_by(WorkSession.started_at)
    )

    return result.scalars().all()

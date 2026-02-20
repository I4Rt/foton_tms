from typing import List
from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import Iteration, Project, WorkItem, ProjectMember, User
from app.models.enums import UserRole
from app.schemas.schemas import IterationCreate, IterationUpdate, IterationResponse, WorkItemResponse

router = APIRouter(prefix="/projects/{project_id}/iterations", tags=["Iterations"])


async def validate_project_exists(project_id: UUID, db: AsyncSession):
    """Check that project exists."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


async def validate_dates(start_date: date, end_date: date):
    """Check that start_date < end_date."""
    if start_date >= end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be earlier than end_date"
        )


async def validate_working_days(working_days: list[date], start_date: date, end_date: date):
    """Check that all working_days fall within [start_date, end_date]."""
    for d in working_days:
        if d < start_date or d > end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"working_day {d.isoformat()} is outside iteration range [{start_date}, {end_date}]"
            )


async def validate_no_overlap(
    project_id: UUID, start_date: date, end_date: date, db: AsyncSession,
    exclude_iteration_id: UUID = None
):
    """Check that new iteration does not overlap with existing ones."""
    query = select(Iteration).where(
        Iteration.project_id == project_id,
        Iteration.start_date < end_date,
        Iteration.end_date > start_date
    )
    if exclude_iteration_id:
        query = query.where(Iteration.id != exclude_iteration_id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Iteration overlaps with existing iteration '{existing.name}' "
                   f"({existing.start_date} - {existing.end_date})"
        )


@router.get("", response_model=List[IterationResponse])
async def list_iterations(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List project iterations."""
    result = await db.execute(
        select(Iteration)
        .where(Iteration.project_id == project_id)
        .order_by(Iteration.start_date.desc())
    )
    return result.scalars().all()


@router.get("/{iteration_id}", response_model=IterationResponse)
async def get_iteration(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get iteration details."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    return iteration


@router.post("", response_model=IterationResponse, status_code=status.HTTP_201_CREATED)
async def create_iteration(
    project_id: UUID,
    iteration_data: IterationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Create new iteration."""
    # Баг 4: проверка существования проекта
    await validate_project_exists(project_id, db)

    # Баг 6.2: start_date < end_date
    await validate_dates(iteration_data.start_date, iteration_data.end_date)

    # Баг 6.3: working_days в диапазоне
    await validate_working_days(iteration_data.working_days, iteration_data.start_date, iteration_data.end_date)

    # Баг 5: проверка перекрытия
    await validate_no_overlap(project_id, iteration_data.start_date, iteration_data.end_date, db)

    iteration = Iteration(
        project_id=project_id,
        name=iteration_data.name,
        start_date=iteration_data.start_date,
        end_date=iteration_data.end_date,
        goal=iteration_data.goal,
        working_days=[d.isoformat() for d in iteration_data.working_days]
    )
    db.add(iteration)
    await db.flush()
    await db.refresh(iteration)
    logger.info(f"Iteration created: {iteration.name} by {current_user.email}")
    return iteration


@router.patch("/{iteration_id}", response_model=IterationResponse)
async def update_iteration(
    project_id: UUID,
    iteration_id: UUID,
    iteration_data: IterationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Update iteration."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")

    update_data = iteration_data.model_dump(exclude_unset=True)

    # Определяем итоговые даты (новые или существующие)
    start_date = update_data.get("start_date", iteration.start_date)
    end_date = update_data.get("end_date", iteration.end_date)

    # Баг 6.2: start_date < end_date
    if "start_date" in update_data or "end_date" in update_data:
        await validate_dates(start_date, end_date)

    # Баг 6.3: working_days в диапазоне
    if "working_days" in update_data and update_data["working_days"] is not None:
        await validate_working_days(update_data["working_days"], start_date, end_date)

    # Баг 5: проверка перекрытия (исключая текущую итерацию)
    if "start_date" in update_data or "end_date" in update_data:
        await validate_no_overlap(project_id, start_date, end_date, db, exclude_iteration_id=iteration_id)

    if "working_days" in update_data and update_data["working_days"] is not None:
        update_data["working_days"] = [d.isoformat() for d in update_data["working_days"]]

    for field, value in update_data.items():
        setattr(iteration, field, value)

    await db.flush()
    await db.refresh(iteration)
    logger.info(f"Iteration updated: {iteration.name} by {current_user.email}")
    return iteration


@router.delete("/{iteration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_iteration(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Delete iteration."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")

    await db.delete(iteration)
    await db.flush()
    logger.info(f"Iteration deleted: {iteration.name} by {current_user.email}")


@router.get("/{iteration_id}/workitems", response_model=List[WorkItemResponse])
async def get_iteration_work_items(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get work items assigned to iteration."""
    result = await db.execute(
        select(WorkItem).where(WorkItem.iteration_id == iteration_id)
    )
    return result.scalars().all()

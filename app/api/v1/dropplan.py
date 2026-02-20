from typing import List, Optional
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import Iteration, WorkItem, User, ProjectMember, WorkSession
from app.models.enums import UserRole, WorkItemType
from app.schemas.schemas import (
    DropPlanSprintResponse, DropPlanUserResponse, DropPlanTaskResponse,
    DropPlanMemberInfo, DropPlanTaskMove
)


router = APIRouter(
    prefix="/projects/{project_id}/iterations/{iteration_id}/dropplan",
    tags=["Drop Plan"]
)


async def _get_iteration_or_404(iteration_id: UUID, project_id: UUID, db: AsyncSession) -> Iteration:
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


async def _get_members(project_id: UUID, db: AsyncSession) -> list[tuple[ProjectMember, User]]:
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id, User.is_active == True)
    )
    return result.all()


async def _get_iteration_tasks(
    iteration_id: UUID, db: AsyncSession, user_id: UUID = None
) -> list[WorkItem]:
    query = select(WorkItem).where(
        WorkItem.iteration_id == iteration_id,
        WorkItem.type == WorkItemType.TASK
    )
    if user_id is not None:
        query = query.where(WorkItem.assigned_to == user_id)
    else:
        query = query.where(WorkItem.assigned_to.is_(None))
    result = await db.execute(query.order_by(WorkItem.start_date, WorkItem.title))
    return result.scalars().all()


async def _compute_hours(work_item_id: UUID, db: AsyncSession) -> tuple[Decimal, Optional[Decimal]]:
    """Compute completed_hours and remaining_hours from work sessions.
    Includes time from an open (not ended) session calculated as now - started_at.
    """
    # Сумма закрытых сессий
    closed_result = await db.execute(
        select(func.coalesce(func.sum(WorkSession.total_hours), 0))
        .where(
            WorkSession.work_item_id == work_item_id,
            WorkSession.ended_at.is_not(None)
        )
    )
    closed_hours = Decimal(str(closed_result.scalar()))

    # Проверяем открытую сессию
    open_result = await db.execute(
        select(WorkSession.started_at)
        .where(
            WorkSession.work_item_id == work_item_id,
            WorkSession.ended_at.is_(None)
        )
    )
    open_started = open_result.scalar_one_or_none()

    open_hours = Decimal(0)
    if open_started:
        now = datetime.now(timezone.utc)
        delta_seconds = (now - open_started).total_seconds()
        open_hours = Decimal(str(round(max(delta_seconds, 0) / 3600, 2)))

    completed = closed_hours + open_hours

    # remaining
    est_result = await db.execute(
        select(WorkItem.estimation_hours).where(WorkItem.id == work_item_id)
    )
    estimation = est_result.scalar()
    remaining = (estimation - completed) if estimation is not None else None

    return completed, remaining


async def _build_task_response(task: WorkItem, db: AsyncSession) -> DropPlanTaskResponse:
    parent_title = None
    if task.parent_id:
        parent_result = await db.execute(
            select(WorkItem.title).where(WorkItem.id == task.parent_id)
        )
        parent_title = parent_result.scalar_one_or_none()

    completed, remaining = await _compute_hours(task.id, db)

    return DropPlanTaskResponse(
        id=task.id,
        title=task.title,
        state=task.state,
        priority=task.priority,
        estimation_hours=task.estimation_hours,
        completed_hours=completed,
        remaining_hours=remaining,
        start_date=task.start_date,
        end_date=task.end_date,
        parent_id=task.parent_id,
        parent_title=parent_title,
        tags=task.tags
    )


def _parse_working_days(iteration: Iteration) -> list[date]:
    return [
        date.fromisoformat(d) if isinstance(d, str) else d
        for d in iteration.working_days
    ]


# ===== Endpoint 1: Sprint overview =====


@router.get("", response_model=DropPlanSprintResponse)
async def get_sprint_dropplan(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get full sprint drop plan overview: working days, members, totals."""
    iteration = await _get_iteration_or_404(iteration_id, project_id, db)
    members = await _get_members(project_id, db)
    tasks = await _get_iteration_tasks(iteration_id, db)
    working_days = _parse_working_days(iteration)

    total_estimation = sum(t.estimation_hours or Decimal(0) for t in tasks)
    total_completed = Decimal(0)
    for t in tasks:
        c, _ = await _compute_hours(t.id, db)
        total_completed += c

    members_info = [
        DropPlanMemberInfo(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            avatar_url=user.avatar_url,
            capacity_per_day=user.capacity_per_day
        )
        for _, user in members
    ]

    return DropPlanSprintResponse(
        iteration_id=iteration.id,
        iteration_name=iteration.name,
        state=iteration.state,
        start_date=iteration.start_date,
        end_date=iteration.end_date,
        working_days=working_days,
        members=members_info,
        total_tasks=len(tasks),
        total_estimation_hours=total_estimation,
        total_completed_hours=total_completed
    )


# ===== Endpoint 2: User tasks in sprint =====


@router.get("/tasks", response_model=DropPlanUserResponse)
async def get_user_dropplan(
    project_id: UUID,
    iteration_id: UUID,
    user_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get tasks in sprint. If user_id provided — tasks for that user. Otherwise — unassigned tasks."""
    iteration = await _get_iteration_or_404(iteration_id, project_id, db)
    working_days = _parse_working_days(iteration)

    member_info = None

    if user_id is not None:
        member_result = await db.execute(
            select(ProjectMember, User)
            .join(User, ProjectMember.user_id == User.id)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        )
        row = member_result.one_or_none()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not a project member")
        _, user = row

        member_info = DropPlanMemberInfo(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            avatar_url=user.avatar_url,
            capacity_per_day=user.capacity_per_day
        )

    tasks = await _get_iteration_tasks(iteration_id, db, user_id=user_id)
    task_responses = [await _build_task_response(t, db) for t in tasks]

    total_estimation = sum(t.estimation_hours or Decimal(0) for t in tasks)
    total_completed = Decimal(0)
    for t in tasks:
        c, _ = await _compute_hours(t.id, db)
        total_completed += c

    return DropPlanUserResponse(
        iteration_id=iteration.id,
        iteration_name=iteration.name,
        start_date=iteration.start_date,
        end_date=iteration.end_date,
        working_days=working_days,
        member=member_info,
        tasks=task_responses,
        total_estimation=total_estimation,
        total_completed=total_completed
    )


# ===== Endpoint 3: Move task within sprint =====


@router.patch("/tasks/{task_id}", response_model=DropPlanTaskResponse)
async def move_task_in_sprint(
    project_id: UUID,
    iteration_id: UUID,
    task_id: UUID,
    move_data: DropPlanTaskMove,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Move a task within the sprint by changing its start/end dates."""
    iteration = await _get_iteration_or_404(iteration_id, project_id, db)

    result = await db.execute(
        select(WorkItem).where(
            WorkItem.id == task_id,
            WorkItem.project_id == project_id
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.type != WorkItemType.TASK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only tasks can be moved in drop plan")

    if task.iteration_id != iteration_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task is not assigned to this iteration")

    if move_data.start_date < iteration.start_date or move_data.start_date > iteration.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"start_date must be within sprint range [{iteration.start_date}, {iteration.end_date}]"
        )

    if move_data.end_date < iteration.start_date or move_data.end_date > iteration.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"end_date must be within sprint range [{iteration.start_date}, {iteration.end_date}]"
        )

    if move_data.end_date < move_data.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be >= start_date"
        )

    task.start_date = move_data.start_date
    task.end_date = move_data.end_date
    await db.flush()
    await db.refresh(task)

    logger.info(f"Task {task_id} moved to {move_data.start_date} - {move_data.end_date} by {current_user.email}")
    return await _build_task_response(task, db)

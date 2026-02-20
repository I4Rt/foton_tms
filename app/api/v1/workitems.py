from typing import List, Optional
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import WorkItem, ProjectMember, User, Iteration, WorkSession
from app.models.enums import UserRole, WorkItemType, WorkItemState
from app.schemas.schemas import (
    WorkItemCreate, WorkItemUpdate, WorkItemResponse,
    WorkSessionCreate, WorkSessionUpdate, WorkSessionResponse
)
from datetime import datetime, timezone


router = APIRouter(prefix="/projects/{project_id}/workitems", tags=["Work Items"])


# ===== Helpers =====


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



async def _build_work_item_response(work_item: WorkItem, db: AsyncSession) -> WorkItemResponse:
    """Build WorkItemResponse with computed hours."""
    completed, remaining = await _compute_hours(work_item.id, db)
    resp = WorkItemResponse.model_validate(work_item)
    resp.completed_hours = completed
    resp.remaining_hours = remaining
    return resp


async def check_project_access(project_id: UUID, user: User, db: AsyncSession):
    """Check if user has access to project."""
    if user.role == UserRole.ADMINISTRATOR:
        return True
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to project")
    return True


async def validate_parent_in_project(parent_id: UUID, project_id: UUID, db: AsyncSession):
    """Check that parent belongs to the same project."""
    result = await db.execute(
        select(WorkItem).where(WorkItem.id == parent_id, WorkItem.project_id == project_id)
    )
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parent not found in this project")
    return parent


async def validate_assigned_to_in_project(user_id: UUID, project_id: UUID, db: AsyncSession):
    """Check that assigned user is a member of the project."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned user is not a member of this project")


async def validate_iteration_in_project(iteration_id: UUID, project_id: UUID, db: AsyncSession):
    """Check that iteration belongs to the same project."""
    result = await db.execute(
        select(Iteration).where(Iteration.id == iteration_id, Iteration.project_id == project_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Iteration not found in this project")


async def _set_removed_recursive(parent_id: UUID, project_id: UUID, db: AsyncSession):
    """Recursively set state=Removed for all descendants."""
    result = await db.execute(
        select(WorkItem).where(WorkItem.parent_id == parent_id, WorkItem.project_id == project_id)
    )
    children = result.scalars().all()
    for child in children:
        child.state = WorkItemState.REMOVED
        await _set_removed_recursive(child.id, project_id, db)


# ===== Work Item Endpoints =====


@router.get("", response_model=List[WorkItemResponse])
async def list_work_items(
    project_id: UUID,
    type: Optional[WorkItemType] = Query(None),
    state: Optional[WorkItemState] = Query(None),
    assigned_to: Optional[UUID] = Query(None),
    iteration_id: Optional[UUID] = Query(None),
    parent_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List work items with filters."""
    await check_project_access(project_id, current_user, db)

    query = select(WorkItem).where(WorkItem.project_id == project_id)
    if type:
        query = query.where(WorkItem.type == type)
    if state:
        query = query.where(WorkItem.state == state)
    if assigned_to:
        query = query.where(WorkItem.assigned_to == assigned_to)
    if iteration_id:
        query = query.where(WorkItem.iteration_id == iteration_id)
    if parent_id:
        query = query.where(WorkItem.parent_id == parent_id)

    result = await db.execute(query.order_by(WorkItem.created_date.desc()))
    items = result.scalars().all()
    return [await _build_work_item_response(wi, db) for wi in items]


@router.get("/{work_item_id}", response_model=WorkItemResponse)
async def get_work_item(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get work item details."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.project_id == project_id)
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    return await _build_work_item_response(work_item, db)


@router.get("/{work_item_id}/children", response_model=List[WorkItemResponse])
async def get_work_item_children(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get child work items."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkItem).where(WorkItem.parent_id == work_item_id, WorkItem.project_id == project_id)
    )
    items = result.scalars().all()
    return [await _build_work_item_response(wi, db) for wi in items]


@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
async def create_work_item(
    project_id: UUID,
    work_item_data: WorkItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Create new work item."""
    await check_project_access(project_id, current_user, db)

    if work_item_data.assigned_to is not None:
        await validate_assigned_to_in_project(work_item_data.assigned_to, project_id, db)
    if work_item_data.iteration_id is not None:
        await validate_iteration_in_project(work_item_data.iteration_id, project_id, db)

    if work_item_data.parent_id:
        parent = await validate_parent_in_project(work_item_data.parent_id, project_id, db)
        valid_parents = {
            WorkItemType.FEATURE: WorkItemType.EPIC,
            WorkItemType.USER_STORY: WorkItemType.FEATURE,
            WorkItemType.TASK: WorkItemType.USER_STORY
        }
        if work_item_data.type in valid_parents:
            if parent.type != valid_parents[work_item_data.type]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{work_item_data.type.value} must have {valid_parents[work_item_data.type].value} as parent"
                )
    elif work_item_data.type != WorkItemType.EPIC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{work_item_data.type.value} must have a parent"
        )

    work_item = WorkItem(
        project_id=project_id,
        type=work_item_data.type,
        title=work_item_data.title,
        description=work_item_data.description,
        priority=work_item_data.priority,
        parent_id=work_item_data.parent_id,
        assigned_to=work_item_data.assigned_to,
        iteration_id=work_item_data.iteration_id,
        estimation_hours=work_item_data.estimation_hours,
        tags=work_item_data.tags,
        start_date=work_item_data.start_date,
        end_date=work_item_data.end_date,
        created_by=current_user.id
    )

    if work_item.type == WorkItemType.TASK and work_item.iteration_id and not work_item.start_date:
        iter_result = await db.execute(select(Iteration).where(Iteration.id == work_item.iteration_id))
        iteration = iter_result.scalar_one_or_none()
        if iteration:
            work_item.start_date = iteration.start_date
            work_item.end_date = iteration.start_date

    db.add(work_item)
    await db.flush()
    await db.refresh(work_item)

    logger.info(f"Work item created: {work_item.title} by {current_user.email}")
    return await _build_work_item_response(work_item, db)


@router.patch("/{work_item_id}", response_model=WorkItemResponse)
async def update_work_item(
    project_id: UUID,
    work_item_id: UUID,
    work_item_data: WorkItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update work item."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.project_id == project_id)
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")

    update_data = work_item_data.model_dump(exclude_unset=True)

    if "parent_id" in update_data and update_data["parent_id"] is not None:
        if update_data["parent_id"] == work_item_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Work item cannot be its own parent")
        await validate_parent_in_project(update_data["parent_id"], project_id, db)

    if "assigned_to" in update_data and update_data["assigned_to"] is not None:
        await validate_assigned_to_in_project(update_data["assigned_to"], project_id, db)

    if "iteration_id" in update_data and update_data["iteration_id"] is not None:
        await validate_iteration_in_project(update_data["iteration_id"], project_id, db)

    for field, value in update_data.items():
        setattr(work_item, field, value)

    if (work_item.type == WorkItemType.TASK
            and "iteration_id" in update_data
            and update_data["iteration_id"] is not None
            and work_item.start_date is None):
        iter_result = await db.execute(select(Iteration).where(Iteration.id == update_data["iteration_id"]))
        iteration = iter_result.scalar_one_or_none()
        if iteration:
            work_item.start_date = iteration.start_date
            work_item.end_date = iteration.start_date

    if update_data.get("state") == WorkItemState.REMOVED:
        await _set_removed_recursive(work_item_id, project_id, db)

    await db.flush()
    await db.refresh(work_item)

    logger.info(f"Work item updated: {work_item.title} by {current_user.email}")
    return await _build_work_item_response(work_item, db)


@router.delete("/{work_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_item(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Delete work item."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.project_id == project_id)
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")

    await db.delete(work_item)
    await db.flush()
    logger.info(f"Work item deleted: {work_item.title} by {current_user.email}")


# ===== Work Session Endpoints =====


async def _get_task_or_404(work_item_id: UUID, project_id: UUID, db: AsyncSession) -> WorkItem:
    """Get work item, verify it exists and is a Task."""
    result = await db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.project_id == project_id)
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    if work_item.type != WorkItemType.TASK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Work sessions are only available for tasks")
    return work_item


@router.get("/{work_item_id}/sessions", response_model=List[WorkSessionResponse])
async def get_task_sessions(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all work sessions for a task."""
    await check_project_access(project_id, current_user, db)
    await _get_task_or_404(work_item_id, project_id, db)

    result = await db.execute(
        select(WorkSession)
        .where(WorkSession.work_item_id == work_item_id)
        .order_by(WorkSession.started_at.desc())
    )
    return result.scalars().all()


@router.post("/{work_item_id}/sessions", response_model=WorkSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_work_session(
    project_id: UUID,
    work_item_id: UUID,
    session_data: WorkSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a work session for a task."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.project_id == project_id)
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    if work_item.type != WorkItemType.TASK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Work sessions are only available for tasks")

    # Проверяем, нет ли незакрытой сессии у этой задачи
    open_session_result = await db.execute(
        select(WorkSession).where(
            WorkSession.work_item_id == work_item_id,
            WorkSession.ended_at.is_(None)
        )
    )
    if open_session_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot create a new session while a previous session is still open"
        )

    total_hours = None
    if session_data.ended_at:
        delta = session_data.ended_at - session_data.started_at
        total_hours = Decimal(str(round(delta.total_seconds() / 3600, 2)))

    session = WorkSession(
        work_item_id=work_item_id,
        user_id=current_user.id,
        description=session_data.description,
        started_at=session_data.started_at,
        ended_at=session_data.ended_at,
        total_hours=total_hours
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    logger.info(f"Work session created for task {work_item_id} by {current_user.email}")
    return session


@router.patch(
    "{work_item_id}/sessions/{session_id}",
    response_model=WorkSessionResponse,
)
async def update_work_session(
    project_id: UUID,
    work_item_id: UUID,
    session_id: UUID,
    session_data: WorkSessionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkSession).where(
            WorkSession.id == session_id,
            WorkSession.work_item_id == work_item_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work session not found",
        )

    update_data = session_data.model_dump(exclude_unset=True)

    # --- Определяем итоговые started_at / ended_at ---
    new_started = update_data.get("started_at", session.started_at)
    new_ended = update_data.get("ended_at", session.ended_at)

    # Валидация: started_at < ended_at (если обе даты известны)
    if new_started and new_ended and new_started >= new_ended:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="started_at must be before ended_at",
        )

    # --- Применяем поля ---
    for field, value in update_data.items():
        setattr(session, field, value)

    # --- Пересчитываем total_hours ---
    if session.started_at and session.ended_at:
        delta = (session.ended_at - session.started_at).total_seconds()
        session.total_hours = round(Decimal(delta) / 3600, 2)
    else:
        session.total_hours = None

    await db.flush()
    await db.refresh(session)

    logger.info(
        f"Work session updated {session.id} by {current_user.email}"
    )
    return session


@router.delete("/{work_item_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_session(
    project_id: UUID,
    work_item_id: UUID,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a work session."""
    await check_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkSession).where(
            WorkSession.id == session_id,
            WorkSession.work_item_id == work_item_id
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work session not found")

    await db.delete(session)
    await db.flush()
    logger.info(f"Work session {session_id} deleted by {current_user.email}")

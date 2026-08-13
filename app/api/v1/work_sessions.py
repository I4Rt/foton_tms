from datetime import date, datetime
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, Date, func, literal

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import WorkSession, WorkItem, ProjectMember, User, Project, Iteration
from app.models.enums import UserRole
from app.schemas.schemas import (
    WorkSessionResponse,
    ProjectSessionsAnalyticsResponse,
    SessionAnalyticsByUser,
    SessionAnalyticsItem,
)


router = APIRouter(tags=["Work Sessions"])


def _has_full_project_access(user: User) -> bool:
    return user.role == UserRole.ADMINISTRATOR


async def _get_accessible_project_ids(user: User, db: AsyncSession) -> set[UUID]:
    if _has_full_project_access(user):
        return set()

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    return set(result.scalars().all())


async def _get_project_member_user_ids(project_ids: set[UUID], db: AsyncSession) -> set[UUID]:
    if not project_ids:
        return set()

    result = await db.execute(
        select(ProjectMember.user_id).where(ProjectMember.project_id.in_(project_ids))
    )
    return set(result.scalars().all())


@router.get("/users/{user_id}/sessions", response_model=List[WorkSessionResponse])
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


@router.get("/sessions", response_model=List[WorkSessionResponse])
async def list_sessions_with_filters(
    dt_from: datetime = Query(..., alias="from", description="Start datetime (inclusive)"),
    dt_to: datetime = Query(..., alias="to", description="End datetime (inclusive)"),
    project_ids: List[UUID] | None = Query(None, alias="projects", description="Project IDs filter"),
    user_ids: List[UUID] | None = Query(None, alias="users", description="User IDs filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List work sessions by interval with access-aware filter sanitization."""
    if dt_from > dt_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from must be <= to"
        )

    full_project_access = _has_full_project_access(current_user)
    accessible_project_ids = await _get_accessible_project_ids(current_user, db)

    requested_project_ids = set(project_ids or [])
    if requested_project_ids:
        allowed_project_ids = (
            requested_project_ids
            if full_project_access
            else requested_project_ids & accessible_project_ids
        )
    else:
        allowed_project_ids = set() if full_project_access else accessible_project_ids

    requested_user_ids = set(user_ids or [])
    if current_user.role in (UserRole.ADMINISTRATOR, UserRole.MANAGER):
        allowed_user_ids = requested_user_ids
    elif current_user.role == UserRole.VIEWER:
        project_member_user_ids = await _get_project_member_user_ids(accessible_project_ids, db)
        allowed_user_ids = (
            requested_user_ids & project_member_user_ids
            if requested_user_ids
            else project_member_user_ids
        )
    else:
        own_user_id = {current_user.id}
        allowed_user_ids = (requested_user_ids & own_user_id) if requested_user_ids else own_user_id

    if not full_project_access and not allowed_project_ids:
        return []
    if requested_user_ids and not allowed_user_ids:
        return []

    query = (
        select(WorkSession)
        .join(WorkItem, WorkItem.id == WorkSession.work_item_id)
        .where(
            WorkSession.started_at >= dt_from,
            WorkSession.started_at <= dt_to,
        )
        .order_by(WorkSession.started_at)
    )

    if full_project_access:
        if requested_project_ids:
            query = query.where(WorkItem.project_id.in_(allowed_project_ids))
    else:
        query = query.where(WorkItem.project_id.in_(allowed_project_ids))

    if current_user.role in (UserRole.ADMINISTRATOR, UserRole.MANAGER):
        if requested_user_ids:
            query = query.where(WorkSession.user_id.in_(allowed_user_ids))
    else:
        query = query.where(WorkSession.user_id.in_(allowed_user_ids))

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/projects/{project_id}/sessions/analytics", response_model=ProjectSessionsAnalyticsResponse)
async def get_project_sessions_analytics(
    project_id: UUID,
    dt_from: datetime = Query(..., alias="from", description="Start datetime (inclusive)"),
    dt_to: datetime = Query(..., alias="to", description="End datetime (inclusive)"),
    user_ids: List[UUID] | None = Query(None, alias="user_id", description="Users filter"),
    iteration_ids: List[UUID] | None = Query(None, alias="iteration_id", description="Iterations filter"),
    task_ids: List[UUID] | None = Query(None, alias="task_id", description="Tasks filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Project sessions analytics with strict access and strict filter validation."""
    if dt_from > dt_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from must be <= to"
        )

    requested_user_ids = set(user_ids or [])
    requested_iteration_ids = set(iteration_ids or [])
    requested_task_ids = set(task_ids or [])

    is_admin = current_user.role == UserRole.ADMINISTRATOR
    is_executor = current_user.role == UserRole.EXECUTOR

    if is_executor and requested_user_ids and requested_user_ids != {current_user.id}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Executors can request only their own sessions"
        )

    membership_exists = (
        select(ProjectMember.project_id)
        .where(
            ProjectMember.project_id == Project.id,
            ProjectMember.user_id == current_user.id,
        )
        .exists()
    )

    iteration_count_subquery = (
        select(func.count(Iteration.id))
        .where(
            Iteration.project_id == project_id,
            Iteration.id.in_(requested_iteration_ids),
        )
        .scalar_subquery()
        if requested_iteration_ids
        else literal(0)
    )
    task_count_subquery = (
        select(func.count(WorkItem.id))
        .where(
            WorkItem.project_id == project_id,
            WorkItem.id.in_(requested_task_ids),
        )
        .scalar_subquery()
        if requested_task_ids
        else literal(0)
    )

    precheck_result = await db.execute(
        select(
            Project.id.label("project_id"),
            membership_exists.label("has_membership"),
            iteration_count_subquery.label("iteration_count"),
            task_count_subquery.label("task_count"),
        ).where(Project.id == project_id)
    )
    precheck = precheck_result.one_or_none()

    if not precheck:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if not is_admin and not precheck.has_membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to project")

    if requested_iteration_ids and precheck.iteration_count != len(requested_iteration_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more iterations not found in this project",
        )

    if requested_task_ids and precheck.task_count != len(requested_task_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more tasks not found in this project",
        )

    effective_user_ids = (
        {current_user.id}
        if is_executor
        else requested_user_ids
    )

    query = (
        select(
            WorkSession.id.label("session_id"),
            WorkSession.started_at,
            WorkSession.ended_at,
            WorkSession.description,
            WorkSession.total_hours,
            User.id.label("user_id"),
            User.display_name.label("user_name"),
            WorkItem.id.label("task_id"),
            WorkItem.title.label("task_title"),
            Iteration.id.label("iteration_id"),
            Iteration.name.label("iteration_name"),
        )
        .join(User, User.id == WorkSession.user_id)
        .join(WorkItem, WorkItem.id == WorkSession.work_item_id)
        .outerjoin(Iteration, Iteration.id == WorkItem.iteration_id)
        .where(
            WorkItem.project_id == project_id,
            WorkSession.started_at >= dt_from,
            WorkSession.started_at <= dt_to,
        )
        .order_by(User.display_name, WorkSession.started_at)
    )

    if effective_user_ids:
        query = query.where(WorkSession.user_id.in_(effective_user_ids))
    if requested_iteration_ids:
        query = query.where(WorkItem.iteration_id.in_(requested_iteration_ids))
    if requested_task_ids:
        query = query.where(WorkItem.id.in_(requested_task_ids))

    rows = (await db.execute(query)).all()

    total_hours = Decimal("0")
    users_map: dict[UUID, SessionAnalyticsByUser] = {}

    for row in rows:
        session_hours = row.total_hours or Decimal("0")
        total_hours += session_hours

        if row.user_id not in users_map:
            users_map[row.user_id] = SessionAnalyticsByUser(
                user_id=row.user_id,
                user_name=row.user_name,
                session_count=0,
                total_hours=Decimal("0"),
                sessions=[],
            )

        user_bucket = users_map[row.user_id]
        user_bucket.session_count += 1
        user_bucket.total_hours += session_hours
        user_bucket.sessions.append(
            SessionAnalyticsItem(
                id=row.session_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
                description=row.description,
                total_hours=row.total_hours,
                user_id=row.user_id,
                user_name=row.user_name,
                iteration_id=row.iteration_id,
                iteration_name=row.iteration_name,
                task_id=row.task_id,
                task_title=row.task_title,
            )
        )

    users_stats = list(users_map.values())
    return ProjectSessionsAnalyticsResponse(
        total_hours=total_hours,
        total_sessions=len(rows),
        total_users=len(users_stats),
        users=users_stats,
    )

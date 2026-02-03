from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import Project, ProjectMember, User
from app.models.enums import UserRole
from app.schemas.schemas import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    ProjectMemberAdd, ProjectMemberResponse
)

router = APIRouter(prefix="/projects", tags=["Projects"])

async def get_user_projects_query(user: User, db: AsyncSession):
    """Get projects accessible to user."""
    if user.role == UserRole.ADMINISTRATOR:
        return select(Project).where(Project.is_active == True)

    return (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id, Project.is_active == True)
    )

@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List projects accessible to current user."""
    query = await get_user_projects_query(current_user, db)
    result = await db.execute(query.order_by(Project.created_date.desc()))
    projects = result.scalars().all()

    # Add member count
    response = []
    for project in projects:
        count_result = await db.execute(
            select(func.count()).select_from(ProjectMember).where(ProjectMember.project_id == project.id)
        )
        member_count = count_result.scalar()
        proj_dict = ProjectResponse.model_validate(project).model_dump()
        proj_dict["member_count"] = member_count
        response.append(ProjectResponse(**proj_dict))

    return response

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get project details."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Check access
    if current_user.role != UserRole.ADMINISTRATOR:
        member_check = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id
            )
        )
        if not member_check.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return project

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Create new project."""
    project = Project(
        name=project_data.name,
        description=project_data.description,
        created_by=current_user.id
    )
    db.add(project)
    await db.flush()

    # Add creator as member
    member = ProjectMember(
        project_id=project.id,
        user_id=current_user.id,
        added_by=current_user.id
    )
    db.add(member)
    await db.flush()
    await db.refresh(project)

    logger.info(f"Project created: {project.name} by {current_user.email}")
    return project

@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Check permissions (creator or admin)
    if current_user.role != UserRole.ADMINISTRATOR and project.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    update_data = project_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)

    logger.info(f"Project updated: {project.name} by {current_user.email}")
    return project

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if current_user.role != UserRole.ADMINISTRATOR and project.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await db.delete(project)
    await db.flush()

    logger.info(f"Project deleted: {project.name} by {current_user.email}")

# ===== Project Members =====
@router.get("/{project_id}/members", response_model=List[ProjectMemberResponse])
async def list_project_members(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List project members."""
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    )
    members = result.all()

    return [
        ProjectMemberResponse(
            user_id=member.user_id,
            display_name=user.display_name,
            email=user.email,
            role=user.role,
            avatar_url=user.avatar_url,
            capacity_per_day=user.capacity_per_day,
            added_date=member.added_date
        )
        for member, user in members
    ]

@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_project_member(
    project_id: UUID,
    member_data: ProjectMemberAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Add member to project."""
    # Check if user exists
    user_result = await db.execute(select(User).where(User.id == member_data.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check if already member
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == member_data.user_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already a member")

    member = ProjectMember(
        project_id=project_id,
        user_id=member_data.user_id,
        added_by=current_user.id
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)

    logger.info(f"Member {user.email} added to project {project_id} by {current_user.email}")

    return ProjectMemberResponse(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        avatar_url=user.avatar_url,
        capacity_per_day=user.capacity_per_day,
        added_date=member.added_date
    )

@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_member(
    project_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Remove member from project."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(member)
    await db.flush()

    logger.info(f"Member {user_id} removed from project {project_id} by {current_user.email}")
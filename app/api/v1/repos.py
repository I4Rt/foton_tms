# routers/repositories.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.gitea import GiteaService, get_gitea_service, GiteaRepoCreated
from app.core.security import require_role
from app.core.logging import logger
from app.models.models import User, Project, Repository
from app.models.enums import UserRole
from app.schemas.schemas import RepositoryCreate, RepositoryResponse
from app.services.gitea import ROLE_PERMISSION_MAP
from app.core.gitea import RepoPermission
from app.models.models import ProjectMember
from app.services.gitea import *

from loguru import logger

router = APIRouter(prefix="/projects/{project_id}/repositories", tags=["repositories"])


# ── POST /projects/{project_id}/repositories ──────────────────────────────────
@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
async def create_repository(
    project_id: UUID,
    repo_data: RepositoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR)),
    gitea: GiteaService = Depends(get_gitea_service),
):
    project = await db.get(Project, project_id)
    if not project or not project.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    existing = await db.execute(
        select(Repository).where(
            Repository.project_id == project_id,
            Repository.gitea_name == repo_data.gitea_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository already exists in this project")

    repo = Repository(project_id=project_id)
    db.add(repo)

    repo_gitea = gitea.create_repository(        # ← переименовано
        repo_name=repo_data.gitea_name,
        description=repo_data.description,
        private=repo_data.private,
    )
    repo.gitea_id   = repo_gitea.gitea_id
    repo.gitea_name = repo_gitea.gitea_name

    users_result = await db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    )
    users = users_result.scalars().all()

    for user in users:
        if not user.is_gitea_synced:
            logger.info(f"User {user.email} not synced with Gitea, provisioning...")
            user_gitea = await provision_gitea_user(user.id, UserCreate(  # ← переименовано
                email=user.email,
                display_name=user.display_name,
                avatar_url=user.avatar_url,
                capacity_per_day=user.capacity_per_day,
                role=user.role,
                password=build_gitea_initial_password(user.id),
            ), gitea)
            user.gitea_id       = user_gitea.gitea_id
            user.gitea_username = user_gitea.gitea_username
            user.gitea_token    = user_gitea.gitea_token
            logger.info(f"Provisioned Gitea account for {user.email}: {user.gitea_username}")

        permission = ROLE_PERMISSION_MAP.get(user.role, RepoPermission.READ)
        gitea.add_user_to_repo(
            username=user.gitea_username,
            repo_name=repo.gitea_name,
            permission=permission,
        )
        logger.info(f"Added {user.gitea_username} to repo {repo.gitea_name} with permission={permission.value}")

    await db.flush()   # ← единственная точка записи в БД
    await db.refresh(repo)
    logger.info(f"Repository created: {repo.gitea_name} (gitea_id={repo.gitea_id}) in project={project_id} by {current_user.email}")
    return repo


# ── DELETE /projects/{project_id}/repositories/{repo_id} ─────────────────────

@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    project_id: UUID,
    repo_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR)),
    gitea: GiteaService = Depends(get_gitea_service),
):
    repo = await db.execute(
        select(Repository).where(
            Repository.id == repo_id,
            Repository.project_id == project_id,
        )
    )
    repo = repo.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    # Сначала Gitea — если упадёт, запись в БД сохранится
    if repo.gitea_name:
        gitea.delete_repository(repo.gitea_name)

    await db.delete(repo)

    logger.info(
        "Repository deleted: %s (gitea_id=%s) from project=%s by %s",
        repo.gitea_name, repo.gitea_id, project_id, current_user.email,
    )

@router.get("", response_model=list[RepositoryResponse])
async def get_project_repositories(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(
        UserRole.VIEWER,
        UserRole.EXECUTOR,
        UserRole.MANAGER,
        UserRole.ADMINISTRATOR,
    )),
):
    project = await db.get(Project, project_id)
    if not project or not project.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    result = await db.execute(
        select(Repository).where(Repository.project_id == project_id)
    )
    repositories = result.scalars().all()

    logger.info(
        "Repositories fetched: project=%s count=%d by %s",
        project_id, len(repositories), current_user.email,
    )
    return repositories
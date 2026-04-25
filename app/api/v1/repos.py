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
    # Проверяем что проект существует и активен
    project = await db.get(Project, project_id)
    if not project or not project.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Уникальность gitea_name в рамках проекта
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
    await db.flush()

    gitea_data: GiteaRepoCreated = gitea.create_repository(
        repo_name=repo_data.gitea_name,
        description=repo_data.description,
        private=repo_data.private,
    )
    repo.gitea_id   = gitea_data.gitea_id
    repo.gitea_name = gitea_data.gitea_name

    await db.flush()
    await db.refresh(repo)

    logger.info(
        f"Repository created: {repo.gitea_name} (gitea_id={repo.gitea_id}) in project={project_id} by {current_user.email}"
    )
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
            Repository.id == project_id,
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

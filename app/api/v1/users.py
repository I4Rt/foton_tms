from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user, require_role, hash_password
from app.core.logging import logger
from app.models.models import User, Project, ProjectMember, Repository
from app.models.enums import UserRole
from app.schemas.schemas import UserCreate, UserUpdate, UserResponse, UserMeResponse, ProjectResponse

from app.services.gitea import *
from app.core.gitea import get_gitea_service, ROLE_PERMISSION_MAP

router = APIRouter(prefix="/users", tags=["Users"])
@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR)),
):
    result = await db.execute(select(User))
    return result.scalars().all()


# ── GET /users/me ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserMeResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    gitea: GiteaService = Depends(get_gitea_service),
):
    # Пользователь есть в портале, но ещё не синкнут с Gitea
    if not current_user.is_gitea_synced:
        # UserCreate не подходит — пароль уже захеширован.
        # Собираем только то, что нужно provision-функции
        user_data = UserCreate(
            email=current_user.email,
            display_name=current_user.display_name,
            avatar_url=current_user.avatar_url,
            capacity_per_day=current_user.capacity_per_day,
            role=current_user.role,
            password=build_gitea_initial_password(current_user.id),  # временный, только для Gitea
        )

        gitea_data = await provision_gitea_user(current_user.id, user_data, gitea)
        current_user.gitea_id       = gitea_data.gitea_id
        current_user.gitea_username = gitea_data.gitea_username
        current_user.gitea_token    = gitea_data.gitea_token

        db.add(current_user)
        await db.flush()
        await db.refresh(current_user)

        logger.info("Lazy Gitea provisioning for existing user: %s", current_user.email)

    return current_user



# ── GET /users/{user_id} ──────────────────────────────────────────────────────

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR)),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# ── POST /users ───────────────────────────────────────────────────────────────

@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR)),
    gitea: GiteaService = Depends(get_gitea_service),
):
    existing = await db.execute(select(User).where(User.email == user_data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        display_name=user_data.display_name,
        avatar_url=user_data.avatar_url,
        role=user_data.role,
        capacity_per_day=user_data.capacity_per_day,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    gitea_data = await provision_gitea_user(user.id, user_data, gitea)
    user.gitea_id       = gitea_data.gitea_id
    user.gitea_username = gitea_data.gitea_username
    user.gitea_token    = gitea_data.gitea_token

    logger.info("User created: %s (gitea: %s) by %s", user.email, user.gitea_username, current_user.email)
    return user


# ── PATCH /users/{user_id} ────────────────────────────────────────────────────

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR)),
    gitea: GiteaService = Depends(get_gitea_service),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_changed = user_data.role is not None and user_data.role != user.role

    for field, value in user_data.model_dump(exclude_none=True).items():
        setattr(user, field, value)

    await db.flush()

    if role_changed and user.is_gitea_synced:
        new_permission = ROLE_PERMISSION_MAP.get(user_data.role, RepoPermission.READ)
        gitea.update_user_permission(user.gitea_username, new_permission)


        # 3. Находим все репозитории проектов, в которых состоит пользователь
        repos_result = await db.execute(
            select(Repository)
            .join(Project, Repository.project_id == Project.id)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                ProjectMember.user_id == user.id,
                Project.is_active == True,
            )
        )
        repos = repos_result.scalars().all()

        # 4. Обновляем permission в каждом репозитории
        for repo in repos:
            try:
                gitea.add_user_to_repo(
                    username=user.gitea_username,
                    repo_name=repo.gitea_name,
                    permission=new_permission,
                )
                logger.info(
                    f"Updated repo permission for {user.gitea_username} "
                    f"in {repo.gitea_name}: {new_permission.value}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to update repo permission for {user.gitea_username} "
                    f"in {repo.gitea_name}: {e}"
                )

    await db.refresh(user)
    logger.info("User updated: %s by %s", user.email, current_user.email)
    return user

# ── DELETE /users/{user_id} ───────────────────────────────────────────────────

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR)),
    gitea: GiteaService = Depends(get_gitea_service),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete yourself")

    # Сначала Gitea — если упадёт, транзакция откатится и запись в БД сохранится
    if user.is_gitea_synced:
        gitea.delete_user(user.gitea_username)

    await db.delete(user)
    logger.info("User deleted: %s by %s", user.email, current_user.email)

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

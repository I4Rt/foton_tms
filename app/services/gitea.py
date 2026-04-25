# services/gitea_provisioning.py
from __future__ import annotations

import re
import unicodedata
import logging
from uuid import UUID

from app.schemas.schemas import UserCreate, UserRole
from app.core.gitea import GiteaService, GiteaUserCreated, RepoPermission
from app.models.models import User as UserModel

logger = logging.getLogger(__name__)


# ── Маппинг ролей портала → права в Gitea ────────────────────────────────────

_ROLE_PERMISSION_MAP: dict[UserRole, RepoPermission] = {
    UserRole.ADMINISTRATOR: RepoPermission.ADMIN,
    UserRole.MANAGER:       RepoPermission.WRITE,
    UserRole.EXECUTOR:     RepoPermission.WRITE,
    UserRole.VIEWER:        RepoPermission.READ,
}


# ── Генерация имени пользователя ─────────────────────────────────────────────

def build_gitea_username(email: str) -> str:
    """
    Строит Gitea-совместимый username из email.
    Правила Gitea: только [a-zA-Z0-9._-], не начинается/заканчивается на точку,
    максимум 40 символов.

    Примеры:
        ivan.petrov@company.com  →  ivan.petrov
        Иван.Петров@company.com  →  ivan.petrov
        user+tag@example.com     →  user-tag
    """
    local_part = email.split("@")[0]
    normalized = unicodedata.normalize("NFKD", local_part)
    ascii_part = normalized.encode("ascii", errors="ignore").decode("ascii")
    sanitized  = re.sub(r"[^a-zA-Z0-9._-]", "-", ascii_part)
    sanitized  = re.sub(r"[-_.]{2,}", "-", sanitized).strip("-.")
    return f"{sanitized[:40].lower()}_user"


def build_gitea_initial_password(user_id: UUID) -> str:
    """Временный пароль: portal-<первые 8 символов uuid>."""
    return f"Portal1!-{str(user_id)[:8]}"


# ── Основная функция провижининга ─────────────────────────────────────────────


async def provision_gitea_user(
    user_id: UUID,
    user_data: UserCreate | UserModel,   # принимаем оба варианта
    gitea_service: GiteaService,
) -> GiteaUserCreated:
    """
    Создать пользователя в Gitea.
    Принимает UserCreate (новый пользователь) или ORM User (ленивый провижининг).
    """
    email      = user_data.email
    role       = user_data.role
    username   = build_gitea_username(email)
    password   = build_gitea_initial_password(user_id)
    permission = _ROLE_PERMISSION_MAP.get(role, RepoPermission.READ)

    logger.info(
        "Provisioning Gitea user | portal_id=%s email=%s username=%s permission=%s",
        user_id, email, username, permission.value,
    )

    result = gitea_service.create_user(
        username=username,
        email=email,
        password=password,
        permission=permission,
    )

    logger.info("Gitea user provisioned | gitea_id=%s username=%s", result.gitea_id, result.gitea_username)
    return result

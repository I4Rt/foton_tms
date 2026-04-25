from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
from gitea import Gitea, Organization, Repository, User
from gitea.exceptions import NotFoundException, Unprocessable

from loguru import logger
from .config import get_settings


# ── Permission model ──────────────────────────────────────────────────────────

class RepoPermission(str, Enum):
    READ  = "read"
    WRITE = "write"
    ADMIN = "admin"


_TEAM_NAMES: dict[RepoPermission, str] = {
    RepoPermission.READ:  "portal-read",
    RepoPermission.WRITE: "portal-write",
    RepoPermission.ADMIN: "portal-admin",
}

_TEAM_UNITS = ["repo.code", "repo.issues", "repo.pulls", "repo.releases"]

_USER_TOKEN_SCOPES = [
    "write:repository",
    "read:user",
    "write:issue",
    "read:organization",
    "read:notification",
]


# ── DTOs ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GiteaUserCreated:
    gitea_id: int
    gitea_username: str
    gitea_token: str


@dataclass(frozen=True)
class GiteaRepoCreated:
    gitea_id: int
    gitea_name: str


# ── Service ───────────────────────────────────────────────────────────────────

class GiteaService:

    def __init__(self, base_url: str, admin_token: str, org_name: str) -> None:
        self._org_name = org_name
        self._client   = Gitea(base_url, token_text=admin_token, verify=True)
        self._http     = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            headers={"Authorization": f"token {admin_token}"},
            timeout=10.0,
        )

    # ── Users ─────────────────────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        email: str,
        password: str,
        permission: RepoPermission = RepoPermission.READ,
    ) -> GiteaUserCreated:
        try:
            user = self._client.create_user(username, email, password, change_pw=False)
        except Unprocessable:
            logger.error("Gitea create_user failed for username=%s email=%s", username, email)
            raise

        token   = self._create_user_token(username, password)
        team    = self._get_or_create_team(permission)
        self._add_user_to_team(team["id"], username)

        return GiteaUserCreated(gitea_id=user.id, gitea_username=username, gitea_token=token)

    def update_user_permission(self, username: str, new_permission: RepoPermission) -> None:
        """Убрать из всех portal-команд, добавить в новую."""
        resp = self._http.get(f"/orgs/{self._org_name}/teams")
        resp.raise_for_status()

        portal_names = set(_TEAM_NAMES.values())
        for team in resp.json():
            if team["name"] in portal_names:
                self._remove_user_from_team(team["id"], username)

        new_team = self._get_or_create_team(new_permission)
        self._add_user_to_team(new_team["id"], username)

    def delete_user(self, username: str) -> None:
        User.request(self._client, username).delete()

    # ── Repo-level access ─────────────────────────────────────────────────────

    def check_repo_access(self, username: str, repo_name: str) -> Optional[RepoPermission]:
        resp = self._http.get(
            f"/repos/{self._org_name}/{repo_name}/collaborators/{username}/permission"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        raw = resp.json().get("permission", "none")
        return RepoPermission(raw) if raw in RepoPermission._value2member_map_ else None

    def add_user_to_repo(self, username: str, repo_name: str, permission: RepoPermission) -> None:
        resp = self._http.put(
            f"/repos/{self._org_name}/{repo_name}/collaborators/{username}",
            json={"permission": permission.value},
        )
        resp.raise_for_status()

    def remove_user_from_repo(self, username: str, repo_name: str) -> None:
        resp = self._http.delete(
            f"/repos/{self._org_name}/{repo_name}/collaborators/{username}"
        )
        resp.raise_for_status()

    # ── Repositories ──────────────────────────────────────────────────────────

    def create_repository(
        self,
        repo_name: str,
        description: str = "",
        private: bool = True,
    ) -> GiteaRepoCreated:
        resp = self._http.post(
            f"/orgs/{self._org_name}/repos",
            json={
                "name": repo_name,
                "description": description,
                "private": private,
                "auto_init": True,
                "default_branch": "main",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug(data)
        return GiteaRepoCreated(gitea_id=data["id"], gitea_name=data["name"])


    def delete_repository(self, repo_name: str) -> None:
        resp = self._http.delete(f"/repos/{self._org_name}/{repo_name}")
        logger.debug(resp.json())
        if resp.status_code != 404:   # 404 — уже удалён, не ошибка
            resp.raise_for_status()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _create_user_token(self, username: str, password: str) -> str:
        basic = base64.b64encode(f"{username}:{password}".encode()).decode()
        auth  = {"Authorization": f"Basic {basic}"}

        resp = self._http.post(
            f"/users/{username}/tokens",
            json={"name": "portal-token", "scopes": _USER_TOKEN_SCOPES},
            headers=auth,
        )

        if resp.status_code == 422:
            logger.warning("Token 'portal-token' already exists for %s, recreating", username)
            self._http.delete(f"/users/{username}/tokens/portal-token", headers=auth)
            resp = self._http.post(
                f"/users/{username}/tokens",
                json={"name": "portal-token", "scopes": _USER_TOKEN_SCOPES},
                headers=auth,
            )

        resp.raise_for_status()
        return resp.json()["sha1"]

    def _get_or_create_team(self, permission: RepoPermission) -> dict:
        """py-gitea не имеет create_team — используем httpx напрямую."""
        name = _TEAM_NAMES[permission]

        resp = self._http.get(f"/orgs/{self._org_name}/teams")
        resp.raise_for_status()
        for team in resp.json():
            if team["name"] == name:
                return team

        resp = self._http.post(
            f"/orgs/{self._org_name}/teams",
            json={
                "name": name,
                "permission": permission.value,
                "units": _TEAM_UNITS,
                "can_create_org_repo": (permission == RepoPermission.ADMIN),
                "includes_all_repositories": False,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def _add_user_to_team(self, team_id: int, username: str) -> None:
        resp = self._http.put(f"/teams/{team_id}/members/{username}")
        resp.raise_for_status()

    def _remove_user_from_team(self, team_id: int, username: str) -> None:
        resp = self._http.delete(f"/teams/{team_id}/members/{username}")
        if resp.status_code != 404:
            resp.raise_for_status()


# ── Singleton ─────────────────────────────────────────────────────────────────

__settings = get_settings()
__gitea = GiteaService(
    base_url    = __settings.GITEA_BASE_URL,
    admin_token = __settings.GITEA_ADMIN_TOKEN,
    org_name    = __settings.GITEA_ORG_NAME,
)

def get_gitea_service() -> GiteaService:
    return __gitea

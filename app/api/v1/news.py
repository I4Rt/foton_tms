import re
import unicodedata
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import logger
from app.core.security import require_role
from app.models.enums import UserRole
from app.models.models import News, NewsTeamMember, User
from app.schemas.schemas import NewsCreate, NewsListItemResponse, NewsResponse, NewsUpdate


router = APIRouter(prefix="/news", tags=["News"])


def _get_news_available_field_name() -> Optional[str]:
    if hasattr(News, "available"):
        return "available"
    if hasattr(News, "avaliable"):
        return "avaliable"
    return None


NEWS_AVAILABLE_FIELD = _get_news_available_field_name()


def _set_news_available_payload(payload: dict, value: bool) -> None:
    if NEWS_AVAILABLE_FIELD is not None:
        payload[NEWS_AVAILABLE_FIELD] = value


def _get_news_available_value(news: News) -> bool:
    if hasattr(news, "available"):
        value = getattr(news, "available")
        if value is not None:
            return value
    if hasattr(news, "avaliable"):
        value = getattr(news, "avaliable")
        if value is not None:
            return value
    return True


def _slugify(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "news"


async def _build_unique_slug(
    title: str,
    db: AsyncSession,
    exclude_news_id: Optional[UUID] = None,
) -> str:
    base_slug = _slugify(title)
    candidate = base_slug
    suffix = 2

    while True:
        result = await db.execute(select(News.id).where(News.slug == candidate))
        existing_id = result.scalar_one_or_none()
        if existing_id is None or existing_id == exclude_news_id:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1


async def _validate_team_member_ids(team_member_ids: List[UUID], db: AsyncSession) -> List[UUID]:
    if not team_member_ids:
        return []

    unique_ids = list(dict.fromkeys(team_member_ids))
    result = await db.execute(select(User.id).where(User.id.in_(unique_ids)))
    existing_ids = {row[0] for row in result.all()}

    missing_ids = [user_id for user_id in unique_ids if user_id not in existing_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Users not found: {[str(user_id) for user_id in missing_ids]}",
        )

    return unique_ids


async def _get_team_members_for_news_ids(news_ids: List[UUID], db: AsyncSession) -> dict[UUID, List[UUID]]:
    if not news_ids:
        return {}

    result = await db.execute(
        select(NewsTeamMember.news_id, NewsTeamMember.user_id)
        .where(NewsTeamMember.news_id.in_(news_ids))
    )

    mapping: dict[UUID, List[UUID]] = {news_id: [] for news_id in news_ids}
    for news_id, user_id in result.all():
        mapping.setdefault(news_id, []).append(user_id)

    return mapping


def _normalize_pagination_bounds(from_index: Optional[int], to_index: Optional[int]) -> tuple[int, Optional[int]]:
    start = 1 if from_index is None else max(from_index, 1)

    if to_index is None:
        return start, None

    end = max(to_index, 1)
    if end < start:
        return start, 0

    return start, end - start + 1


@router.post("", response_model=NewsResponse, status_code=status.HTTP_201_CREATED)
async def create_news(
    news_data: NewsCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR, UserRole.MANAGER)),
):
    team_member_ids = await _validate_team_member_ids(news_data.team_member_ids, db)
    slug = await _build_unique_slug(news_data.title, db)

    news_payload = {
        "slug": slug,
        "title": news_data.title,
        "image_url": news_data.image_url,
        "content": news_data.content,
        "created_by": current_user.id,
    }
    _set_news_available_payload(news_payload, news_data.avaliable)
    news = News(**news_payload)
    db.add(news)
    await db.flush()

    if team_member_ids:
        db.add_all(
            [NewsTeamMember(news_id=news.id, user_id=user_id) for user_id in team_member_ids]
        )
        await db.flush()

    await db.refresh(news)

    logger.info("News created: %s by %s", news.slug, current_user.email)
    return NewsResponse(
        id=news.id,
        slug=news.slug,
        image_url=news.image_url,
        title=news.title,
        content=news.content,
        created_date=news.created_date,
        team_member_ids=team_member_ids,
        avaliable=_get_news_available_value(news),
    )


@router.patch("/{news_id}", response_model=NewsResponse)
async def update_news(
    news_id: UUID,
    news_data: NewsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR, UserRole.MANAGER)),
):
    news = await db.get(News, news_id)
    if not news:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="News not found")

    update_data = news_data.model_dump(exclude_unset=True, exclude={"team_member_ids"})

    if "title" in update_data:
        update_data["slug"] = await _build_unique_slug(
            update_data["title"],
            db,
            exclude_news_id=news.id,
        )

    if "avaliable" in update_data:
        avaliable_value = update_data.pop("avaliable")
        _set_news_available_payload(update_data, avaliable_value)

    for field, value in update_data.items():
        setattr(news, field, value)

    if news_data.team_member_ids is not None:
        validated_ids = await _validate_team_member_ids(news_data.team_member_ids, db)
        await db.execute(
            NewsTeamMember.__table__.delete().where(NewsTeamMember.news_id == news.id)
        )
        if validated_ids:
            db.add_all(
                [NewsTeamMember(news_id=news.id, user_id=user_id) for user_id in validated_ids]
            )
        team_member_ids = validated_ids
    else:
        team_members_map = await _get_team_members_for_news_ids([news.id], db)
        team_member_ids = team_members_map.get(news.id, [])

    await db.flush()
    await db.refresh(news)

    logger.info("News updated: %s by %s", news.slug, current_user.email)
    return NewsResponse(
        id=news.id,
        slug=news.slug,
        image_url=news.image_url,
        title=news.title,
        content=news.content,
        created_date=news.created_date,
        team_member_ids=team_member_ids,
        avaliable=_get_news_available_value(news),
    )


@router.delete("/{news_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_news(
    news_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMINISTRATOR, UserRole.MANAGER)),
):
    news = await db.get(News, news_id)
    if not news:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="News not found")

    await db.delete(news)
    await db.flush()

    logger.info("News deleted: %s by %s", news.slug, current_user.email)


@router.get("", response_model=List[NewsListItemResponse])
async def list_news(
    from_index: Optional[int] = Query(None, alias="from"),
    to_index: Optional[int] = Query(None, alias="to"),
    avaliable: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    start, limit = _normalize_pagination_bounds(from_index, to_index)
    if limit == 0:
        return []

    query = select(News).order_by(News.created_date.desc(), News.id.desc()).offset(start - 1)
    if avaliable is not None and NEWS_AVAILABLE_FIELD is not None:
        query = query.where(getattr(News, NEWS_AVAILABLE_FIELD) == avaliable)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    news_items = result.scalars().all()
    team_members_map = await _get_team_members_for_news_ids([item.id for item in news_items], db)

    return [
        NewsListItemResponse(
            id=item.id,
            slug=item.slug,
            image_url=item.image_url,
            title=item.title,
            created_date=item.created_date,
            team_member_ids=team_members_map.get(item.id, []),
            avaliable=_get_news_available_value(item),
        )
        for item in news_items
    ]


@router.get("/{news_slug}", response_model=NewsResponse)
async def get_news(news_slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(News).where(News.slug == news_slug))
    news = result.scalar_one_or_none()
    if not news:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="News not found")

    team_members_map = await _get_team_members_for_news_ids([news.id], db)
    return NewsResponse(
        id=news.id,
        slug=news.slug,
        image_url=news.image_url,
        title=news.title,
        content=news.content,
        created_date=news.created_date,
        team_member_ids=team_members_map.get(news.id, []),
        avaliable=_get_news_available_value(news),
    )

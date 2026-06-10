import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.logging import logger
from app.models.enums import IterationState
from app.models.models import Iteration


def _resolve_state_for_today(start_date, end_date, today):
    if end_date < today:
        return IterationState.PAST
    if start_date > today:
        return IterationState.FUTURE
    return IterationState.CURRENT


async def sync_iteration_states() -> int:
    """Recalculate iteration states based on server local date."""
    today = datetime.now().date()
    updated_count = 0

    async with async_session_maker() as session:
        result = await session.execute(select(Iteration))
        iterations = result.scalars().all()

        for iteration in iterations:
            new_state = _resolve_state_for_today(
                start_date=iteration.start_date,
                end_date=iteration.end_date,
                today=today,
            )
            if iteration.state != new_state:
                iteration.state = new_state
                updated_count += 1

        await session.commit()

    logger.info(
        f"Iteration states sync completed for {today.isoformat()}: "
        f"{updated_count} updated"
    )
    return updated_count


async def run_daily_iteration_state_sync():
    """Run iteration state sync once at startup and then every midnight."""
    try:
        await sync_iteration_states()
    except Exception as exc:
        logger.exception(f"Initial iteration state sync failed: {exc}")

    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_seconds = max((next_midnight - now).total_seconds(), 1)

        logger.debug(
            f"Next iteration state sync scheduled at {next_midnight.isoformat()}"
        )
        await asyncio.sleep(sleep_seconds)
        try:
            await sync_iteration_states()
        except Exception as exc:
            logger.exception(f"Iteration state sync failed: {exc}")

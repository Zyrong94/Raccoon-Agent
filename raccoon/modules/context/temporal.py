"""Date-only turn context and on-demand clock, following OpenClaw's time rules."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tzlocal import get_localzone_name


def resolve_user_timezone(configured: str | None = None) -> str:
    if configured:
        try:
            return ZoneInfo(configured.strip()).key
        except (ValueError, ZoneInfoNotFoundError):
            pass
    try:
        host = get_localzone_name()
        if host:
            return ZoneInfo(host).key
    except (OSError, ValueError, ZoneInfoNotFoundError):
        pass
    return 'UTC'


def current_time() -> datetime:
    return datetime.now(UTC)


def build_temporal_context(timezone: str, *, now: datetime | None = None) -> str:
    now = current_time() if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Temporal context requires a timezone-aware timestamp')
    local = now.astimezone(ZoneInfo(timezone))
    return ('## Temporal Context\n'
            f'Current date: {local.date().isoformat()}\n'
            f'Time zone: {timezone}\n'
            'For the exact current time, use `session_status`.')

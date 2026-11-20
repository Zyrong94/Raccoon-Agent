"""Timezone-aware schedule calculations, independent of storage and real clocks."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def zone_info(zone):
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise ValueError('未知时区，请使用 Asia/Tokyo 等 IANA 时区名称') from None


def parse_time(value: str, zone: str) -> float:
    tz = zone_info(zone)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError('一次性时间必须为 ISO 日期时间文本')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        local = parsed.replace(tzinfo=tz, fold=0)
        if local.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != parsed:
            raise ValueError('该本地时间因夏令时不存在，请选择其他时间')
        parsed = local
    return parsed.timestamp()


def next_time(kind: str, spec: dict, zone: str, now: float) -> float:
    tz = zone_info(zone)
    if not isinstance(spec, dict):
        raise ValueError('时间规则必须为对象')
    if kind == 'once':
        result = parse_time(spec.get('at'), zone)
        if result <= now:
            raise ValueError('一次性任务时间必须在未来')
        return result
    if kind == 'interval':
        seconds = spec.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 60 <= seconds <= 30 * 86400:
            raise ValueError('间隔必须为 60 秒到 30 天之间的整数秒')
        return now + seconds
    if kind == 'cron':
        expression = spec.get('cron', '')
        if not isinstance(expression, str) or len(expression) > 256 or len(expression.split()) != 5:
            raise ValueError('Cron 必须为五字段表达式')
        base = datetime.fromtimestamp(now, tz)
        result = croniter(expression, base, max_years_between_matches=8).get_next(datetime).timestamp()
        if result <= now:
            raise ValueError('无法计算下次时间')
        return result
    raise ValueError('时间类型必须为 once、interval 或 cron')


def advance(kind: str, spec: dict, zone: str, previous: float, now: float) -> float | None:
    if kind == 'once':
        return None
    if kind == 'interval':
        seconds = spec['seconds']
        return previous + (int((now - previous) // seconds) + 1) * seconds
    return next_time(kind, spec, zone, now)

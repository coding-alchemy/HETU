from contextlib import suppress
from datetime import UTC, date, datetime, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

AvailabilityStatus = Literal["available", "after-as-of", "date-only-same-day-unknown"]


class AvailabilityDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AvailabilityStatus
    admissible: bool
    as_of: str
    published_at: str
    source_timezone: str
    reason: str


def _parse_datetime(value: str, field_name: str) -> datetime:
    parsed: datetime | None = None
    with suppress(ValueError):
        parsed = datetime.fromisoformat(value)
    if parsed is None:
        raise ValueError(f"{field_name} is invalid")
    return parsed


def _parse_date(value: str, field_name: str) -> date:
    parsed: date | None = None
    with suppress(ValueError):
        parsed = date.fromisoformat(value)
    if parsed is None:
        raise ValueError(f"{field_name} is invalid")
    return parsed


def _convert_timezone(value: datetime, zone: tzinfo, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")

    offset_checked = False
    offset = None
    with suppress(OverflowError, ValueError):
        offset = value.utcoffset()
        offset_checked = True
    if not offset_checked:
        raise ValueError(
            f"{field_name} is outside the supported timezone conversion range"
        )
    if offset is None:
        raise ValueError(f"{field_name} must be timezone-aware")

    converted: datetime | None = None
    with suppress(OverflowError, ValueError):
        converted = value.astimezone(zone)
    if converted is None:
        raise ValueError(
            f"{field_name} is outside the supported timezone conversion range"
        )
    return converted


def evaluate_availability(
    *,
    as_of: datetime,
    published_at: datetime | date,
    source_timezone: str,
) -> AvailabilityDecision:
    try:
        source_zone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"source_timezone is unknown: {source_timezone}") from exc

    normalized_as_of_value = _convert_timezone(as_of, UTC, "as_of")
    normalized_as_of = normalized_as_of_value.isoformat()
    if isinstance(published_at, datetime):
        normalized_published_at_value = _convert_timezone(
            published_at, UTC, "published_at"
        )
        normalized_published_at = normalized_published_at_value.isoformat()
        admissible = normalized_published_at_value <= normalized_as_of_value
        if admissible:
            status: AvailabilityStatus = "available"
            reason = "The publication timestamp is at or before the as-of timestamp."
        else:
            status = "after-as-of"
            reason = "The publication timestamp is after the as-of timestamp."
    else:
        normalized_published_at = published_at.isoformat()
        local_as_of = _convert_timezone(as_of, source_zone, "as_of").date()
        if published_at < local_as_of:
            status = "available"
            admissible = True
            reason = "The date-only publication date is before the as-of local date."
        elif published_at > local_as_of:
            status = "after-as-of"
            admissible = False
            reason = "The date-only publication date is after the as-of local date."
        else:
            status = "date-only-same-day-unknown"
            admissible = False
            reason = (
                "The date-only publication date matches the as-of local date, "
                "and its time is unknown."
            )

    return AvailabilityDecision(
        status=status,
        admissible=admissible,
        as_of=normalized_as_of,
        published_at=normalized_published_at,
        source_timezone=source_zone.key,
        reason=reason,
    )


def evaluate_availability_json(
    *,
    as_of: str,
    published_at: str,
    date_only: bool,
    source_timezone: str,
) -> str:
    parsed_as_of = _parse_datetime(as_of, "as_of")

    parsed_published_at: datetime | date
    if date_only:
        parsed_published_at = _parse_date(published_at, "published_at")
    else:
        date_candidate: date | None = None
        with suppress(ValueError):
            date_candidate = date.fromisoformat(published_at)
        if date_candidate is not None:
            raise ValueError("published_at is date-only; pass --date-only")
        parsed_published_at = _parse_datetime(published_at, "published_at")

    return evaluate_availability(
        as_of=parsed_as_of,
        published_at=parsed_published_at,
        source_timezone=source_timezone,
    ).model_dump_json()

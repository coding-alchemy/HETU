import json
from datetime import date, datetime, tzinfo

import pytest

from hetu_stock.helpers.time import evaluate_availability, evaluate_availability_json

AS_OF = datetime.fromisoformat("2026-07-27T10:04:35+08:00")


class NoOffsetTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None


class RaisingTimezone(tzinfo):
    def __init__(self, error_type: type[Exception]) -> None:
        self.error_type = error_type

    def utcoffset(self, dt: datetime | None) -> None:
        raise self.error_type("CANARY-UTC-OFFSET")


def _helper_traceback_values(exception: BaseException) -> tuple[str, ...]:
    values: list[str] = []
    traceback = exception.__traceback__
    while traceback is not None:
        filename = traceback.tb_frame.f_code.co_filename
        if "/src/hetu_stock/helpers/" in filename:
            values.append(repr(traceback.tb_frame.f_locals))
        traceback = traceback.tb_next
    return tuple(values)


def test_timestamp_before_as_of_is_available() -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
        source_timezone="Asia/Shanghai",
    )
    assert result.status == "available"
    assert result.admissible is True


def test_timestamp_decision_normalizes_fields_and_reason() -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
        source_timezone="Asia/Shanghai",
    )

    assert result.as_of == "2026-07-27T02:04:35+00:00"
    assert result.published_at == "2026-07-27T01:30:00+00:00"
    assert result.source_timezone == "Asia/Shanghai"
    assert result.reason == "The publication timestamp is at or before the as-of timestamp."


def test_timestamp_after_as_of_is_rejected_after_timezone_conversion() -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=datetime.fromisoformat("2026-07-27T02:05:00+00:00"),
        source_timezone="Asia/Shanghai",
    )
    assert result.status == "after-as-of"
    assert result.admissible is False


def test_timestamp_equal_to_as_of_is_available() -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=AS_OF,
        source_timezone="Asia/Shanghai",
    )
    assert result.status == "available"
    assert result.admissible is True


def test_date_only_same_local_day_is_not_assumed_available() -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=date(2026, 7, 27),
        source_timezone="Asia/Shanghai",
    )
    assert result.status == "date-only-same-day-unknown"
    assert result.admissible is False


def test_date_only_compares_against_source_timezone_local_day() -> None:
    result = evaluate_availability(
        as_of=datetime.fromisoformat("2026-07-26T18:00:00+00:00"),
        published_at=date(2026, 7, 27),
        source_timezone="Asia/Shanghai",
    )

    assert result.status == "date-only-same-day-unknown"
    assert result.admissible is False
    assert result.reason == (
        "The date-only publication date matches the as-of local date, and its time is unknown."
    )


@pytest.mark.parametrize(
    ("published_at", "status", "admissible"),
    [
        (date(2026, 7, 26), "available", True),
        (date(2026, 7, 28), "after-as-of", False),
    ],
)
def test_date_only_before_and_after_local_day(
    published_at: date,
    status: str,
    admissible: bool,
) -> None:
    result = evaluate_availability(
        as_of=AS_OF,
        published_at=published_at,
        source_timezone="Asia/Shanghai",
    )
    assert result.status == status
    assert result.admissible is admissible


@pytest.mark.parametrize("value", [datetime(2026, 7, 27, 9), datetime(2026, 7, 27, 10)])
def test_naive_datetime_is_invalid(value: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_availability(as_of=value, published_at=value, source_timezone="Asia/Shanghai")


def test_as_of_with_no_utc_offset_is_invalid() -> None:
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        evaluate_availability(
            as_of=datetime(2026, 7, 27, 10, tzinfo=NoOffsetTimezone()),
            published_at=AS_OF,
            source_timezone="Asia/Shanghai",
        )


def test_published_at_with_no_utc_offset_is_invalid() -> None:
    with pytest.raises(ValueError, match="published_at must be timezone-aware"):
        evaluate_availability(
            as_of=AS_OF,
            published_at=datetime(2026, 7, 27, 9, tzinfo=NoOffsetTimezone()),
            source_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize("error_type", [ValueError, OverflowError])
@pytest.mark.parametrize("field", ["as_of", "published_at"])
def test_raising_utc_offset_is_normalized_without_exception_text(
    error_type: type[Exception],
    field: str,
) -> None:
    raising_value = datetime(2026, 7, 27, 10, tzinfo=RaisingTimezone(error_type))
    arguments = {
        "as_of": raising_value if field == "as_of" else AS_OF,
        "published_at": raising_value if field == "published_at" else AS_OF,
        "source_timezone": "Asia/Shanghai",
    }

    with pytest.raises(
        ValueError,
        match=rf"^{field} is outside the supported timezone conversion range$",
    ) as caught:
        evaluate_availability(**arguments)

    assert all(
        "CANARY-UTC-OFFSET" not in value
        for value in (
            str(caught.value),
            repr(caught.value),
            *_helper_traceback_values(caught.value),
        )
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_unknown_source_timezone_is_invalid() -> None:
    with pytest.raises(ValueError, match="source_timezone"):
        evaluate_availability(
            as_of=AS_OF,
            published_at=date(2026, 7, 26),
            source_timezone="Invalid/Phase2",
        )


def test_json_adapter_serializes_normalized_decision_fields() -> None:
    result = json.loads(
        evaluate_availability_json(
            as_of="2026-07-27T10:04:35+08:00",
            published_at="2026-07-27T09:30:00+08:00",
            date_only=False,
            source_timezone="Asia/Shanghai",
        )
    )

    assert result == {
        "status": "available",
        "admissible": True,
        "as_of": "2026-07-27T02:04:35+00:00",
        "published_at": "2026-07-27T01:30:00+00:00",
        "source_timezone": "Asia/Shanghai",
        "reason": "The publication timestamp is at or before the as-of timestamp.",
    }


@pytest.mark.parametrize(
    ("as_of", "published_at", "date_only", "expected_message", "canary"),
    [
        (
            "CANARY-AS-OF",
            "2026-07-27T09:30:00+08:00",
            False,
            "as_of is invalid",
            "CANARY-AS-OF",
        ),
        (
            "2026-07-27T10:04:35+08:00",
            "CANARY-PUBLISHED-AT",
            False,
            "published_at is invalid",
            "CANARY-PUBLISHED-AT",
        ),
        (
            "2026-07-27T10:04:35+08:00",
            "CANARY-PUBLISHED-DATE",
            True,
            "published_at is invalid",
            "CANARY-PUBLISHED-DATE",
        ),
    ],
)
def test_json_adapter_parse_errors_are_fixed_and_do_not_echo_input(
    as_of: str,
    published_at: str,
    date_only: bool,
    expected_message: str,
    canary: str,
) -> None:
    with pytest.raises(ValueError, match=rf"^{expected_message}$") as caught:
        evaluate_availability_json(
            as_of=as_of,
            published_at=published_at,
            date_only=date_only,
            source_timezone="Asia/Shanghai",
        )

    assert canary not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_json_adapter_date_without_date_only_flag_has_actionable_error() -> None:
    with pytest.raises(
        ValueError,
        match=r"^published_at is date-only; pass --date-only$",
    ):
        evaluate_availability_json(
            as_of="2026-07-27T10:04:35+08:00",
            published_at="2026-07-27",
            date_only=False,
            source_timezone="Asia/Shanghai",
        )


@pytest.mark.parametrize(
    ("field", "as_of", "published_at"),
    [
        ("as_of", "0001-01-01T00:00:00+14:00", "2026-07-27T09:30:00+08:00"),
        ("as_of", "9999-12-31T23:59:59-14:00", "2026-07-27T09:30:00+08:00"),
        ("published_at", "2026-07-27T10:04:35+08:00", "0001-01-01T00:00:00+14:00"),
        ("published_at", "2026-07-27T10:04:35+08:00", "9999-12-31T23:59:59-14:00"),
    ],
)
def test_json_adapter_rejects_timezone_conversion_overflow(
    field: str,
    as_of: str,
    published_at: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{field} is outside the supported timezone conversion range$",
    ):
        evaluate_availability_json(
            as_of=as_of,
            published_at=published_at,
            date_only=False,
            source_timezone="Asia/Shanghai",
        )

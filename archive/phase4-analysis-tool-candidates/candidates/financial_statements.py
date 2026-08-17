"""Normalize already-fetched financial statements without source-side guesses."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def normalize_finance_report(
    payload: dict[str, Any],
    *,
    limit: int = 9,
) -> list[dict[str, str]]:
    """Return newest-first statement rows while preserving source values."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    try:
        report_list = payload["result"]["data"]["report_list"]
    except (KeyError, TypeError) as exc:
        raise ValueError("payload must contain result.data.report_list") from exc
    if not isinstance(report_list, dict):
        raise ValueError("report_list must be an object")

    normalized: list[tuple[datetime, dict[str, str]]] = []
    for raw_period, raw_report in report_list.items():
        try:
            period = datetime.strptime(raw_period, "%Y%m%d")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid report period: {raw_period!r}") from exc
        if not isinstance(raw_report, dict) or not isinstance(
            raw_report.get("data"), list
        ):
            raise ValueError(f"report {raw_period} must contain a data list")

        row = {"报告期": period.date().isoformat()}
        for item in raw_report["data"]:
            if not isinstance(item, dict):
                raise ValueError(f"report {raw_period} contains a non-object item")
            title = item.get("item_title")
            value = item.get("item_value")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"report {raw_period} contains an invalid item_title")
            if not isinstance(value, str):
                raise ValueError(f"report {raw_period} item {title!r} lacks a string value")
            title = title.strip()
            if title in row:
                raise ValueError(f"report {raw_period} contains duplicate item {title!r}")
            row[title] = value

            year_over_year = item.get("item_tongbi")
            if year_over_year is not None:
                if not isinstance(year_over_year, str):
                    raise ValueError(
                        f"report {raw_period} item {title!r} has invalid item_tongbi"
                    )
                row[f"{title}_同比"] = year_over_year
        normalized.append((period, row))

    normalized.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in normalized[:limit]]

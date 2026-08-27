"""Normalize saved Sina finance report payloads without source-side guesses.

The normalizer only accepts the Sina statement structure: a
``result.data.report_list`` object keyed by report period, whose entries hold a
``data`` list of ``item_title``/``item_value`` items (optional ``item_tongbi``
year-over-year text). Payloads with any other shape — including EastMoney
list-shaped ``report_list`` responses with ``f``-code fields — are formally
rejected; source adapters that own those payloads must decode them against
their own field dictionaries.

CLI: ``financial_statements.py --input IN --output OUT [--limit N]``. Exit 0
writes a success envelope; exit 1 writes a failure envelope for unparseable
input or a rejected payload (including invalid input values such as
``--limit 0``); exit 2 covers usage errors (missing or misnamed arguments),
unreadable input, an output path equal to the input path, or an existing
output file.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import _artifact_io

DEFAULT_LIMIT = 9

# Report period keys must be strict 8-digit YYYYMMDD strings: strptime alone
# would also accept lenient forms like "202681".
_PERIOD_KEY_PATTERN = re.compile(r"\d{8}")


def normalize_finance_report(
    payload: dict[str, Any],
    *,
    limit: int = DEFAULT_LIMIT,
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
        if not isinstance(raw_period, str) or not _PERIOD_KEY_PATTERN.fullmatch(
            raw_period
        ):
            raise ValueError(f"invalid report period: {raw_period!r}")
        try:
            period = datetime.strptime(raw_period, "%Y%m%d")
        except ValueError as exc:
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
                tongbi_key = f"{title}_同比"
                if tongbi_key in row:
                    raise ValueError(
                        f"report {raw_period} contains duplicate item {tongbi_key!r}"
                    )
                row[tongbi_key] = year_over_year
        normalized.append((period, row))

    normalized.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in normalized[:limit]]


def _transform(limit: int) -> _artifact_io.Transform:
    def transform(payload: dict[str, Any]) -> object:
        return {"limit": limit, "rows": normalize_finance_report(payload, limit=limit)}

    return transform


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize a saved Sina finance report payload."
    )
    parser.add_argument("--input", required=True, help="saved response JSON path")
    parser.add_argument("--output", required=True, help="envelope JSON path to create")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        print("output_path must differ from input_path", file=sys.stderr)
        return 2
    if output_path.exists():
        print("output_path already exists", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        _artifact_io.run_transform(
            tool_name="financial_statements",
            input_path=input_path,
            output_path=output_path,
            transform=_transform(args.limit),
        )
    except _artifact_io.OutputConflictError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except _artifact_io.InputUnreadableError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent numerical checks of all additional named presets."""

import json
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
from factor_assertions import checked
from factor_cases import example_query
from hetu_quant.factors import TeachingSource, close_cutoff, compute_factor
from hetu_quant.factors.catalog import get_factor

PRESETS = (
    ("price_momentum", "3自然月", 0.2),
    ("price_momentum", "6自然月", 0.5),
    ("relative_strength", "3自然月", 0.1),
    ("relative_strength", "6自然月", 0.125),
    ("realized_volatility", "原价", 0.5 * (126**0.5)),
    ("max_drawdown", "半年", 0.4),
    ("traded_amount", "20日均值", 2),
    ("traded_amount", "20日中位数", 1),
    ("traded_amount", "20日总额", 40),
    ("breadth_above_ma_share", "200日", 0.6),
    ("etf_dual_momentum", "长期均线", [100, 0]),
)


def daily_interval(document, *, start, end, values):
    dates = pd.date_range(start, end)
    calendar = [
        dict(date=str(day.date()), is_trading_day=day.weekday() < 5, available_at="2023-01-01T09:00:00+08:00")
        for day in dates
    ]
    trading_days = [row["date"] for row in calendar if row["is_trading_day"]]
    bars = []
    for code, lookup in values.items():
        last = lookup["base"]
        for day in trading_days:
            last = lookup.get(day, last)
            bars.append(
                dict(
                    code=code,
                    date=day,
                    close=last,
                    close_raw=last,
                    available_at=f"{day}T15:00:00+08:00",
                    source_ref="合成交易日行情",
                )
            )
    document["tables"].update(calendar=calendar, daily_bars=bars)


@pytest.mark.parametrize(("factor_id", "preset", "expected"), PRESETS)
def test_named_presets(tmp_path, request, factor_evidence_root, factor_id, preset, expected):
    source, doc, query = example_query(factor_id)
    parameters = get_factor(factor_id).presets[preset]
    if "自然月" in preset or preset == "半年":
        if preset == "半年":
            values = {"A": {"base": 10, "2024-08-27": 10, "2024-08-28": 15, "2024-08-29": 9, "2024-08-30": 12}}
        else:
            values = {
                "A": {"base": 8, "2024-05-29": 9, "2024-05-30": 10, "2024-08-30": 12},
                "B": {"base": 80, "2024-05-29": 85, "2024-05-30": 100, "2024-08-30": 110},
            }
        daily_interval(doc, start="2024-02-01", end="2024-08-30", values=values)
        day = date(2024, 8, 30)
        query = replace(query, trade_date=day, cutoff=close_cutoff(day))
    elif preset == "原价":
        for row, value in zip(doc["tables"]["daily_bars"], (10, 5, 5), strict=True):
            row.update(close_raw=value, close=10)
    elif preset == "200日":
        values = {
            code: {"base": 10, "2024-01-31": close} for code, close in zip("ABCDE", (12, 11, 11, 10, 9), strict=True)
        }
        daily_interval(doc, start="2023-04-03", end="2024-01-31", values=values)
    query = replace(query, parameters=parameters)
    path = tmp_path / f"{source.case_id}.json"
    path.write_text(json.dumps(doc))
    source = TeachingSource(source.case_id, tmp_path)
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        factor_id,
        "positive",
        expected,
        parameters=parameters,
        replay=(query, doc),
    ) as actual:
        result = compute_factor(None, source, query)
        assert result.rows, result.gaps
        value = [row["value"] for row in result.rows] if isinstance(expected, list) else result.rows[0]["value"]
        actual.update(value=value, components=[row["components"] for row in result.rows])
        assert value == pytest.approx(expected)
        if preset == "长期均线":
            assert [row["components"]["passed"] for row in result.rows] == [True, False]
    if preset == "长期均线":
        doc["tables"]["membership_coverage"] = []
        field, subject, period = "memberships", "ETF", str(query.trade_date)
    elif preset in ("20日均值", "20日中位数", "20日总额"):
        field, subject, period = "amount_cny", query.subjects[0], doc["tables"]["daily_bars"][1]["date"]
        del doc["tables"]["daily_bars"][1]
    elif factor_id == "breadth_above_ma_share":
        doc["tables"]["membership_coverage"] = []
        field, subject, period = "memberships", query.subjects[0], str(query.trade_date)
    elif factor_id in ("price_momentum", "relative_strength"):
        anchor = "2024-05-30" if "3" in preset else "2024-02-29"
        target = "B" if factor_id == "relative_strength" else "A"
        field, subject, period = "close_raw", target, anchor
        doc["tables"]["daily_bars"] = [
            row for row in doc["tables"]["daily_bars"] if (row["code"], row["date"]) != (target, anchor)
        ]
    elif preset == "原价":
        doc["tables"]["daily_bars"][-1].pop("close_raw")
        field, subject, period = "close_raw", query.subjects[0], str(query.trade_date)
    else:
        doc["tables"]["calendar"] = doc["tables"]["calendar"][-10:]
        field, subject, period = "calendar", query.subjects[0], str(query.trade_date)
    path.write_text(json.dumps(doc))
    coordinates = dict(field=field, subject=subject, period=period)
    with checked(
        factor_evidence_root,
        request.node.nodeid,
        factor_id,
        "formula:preset_boundary",
        coordinates,
        parameters=parameters,
        replay=(query, doc),
        inputs=dict(case=source.case_id, preset=preset, day=str(query.trade_date), mutation=coordinates),
    ) as actual:
        result = compute_factor(None, source, query)
        actual["gaps"] = result.gaps
        assert result.status in ("partial", "unavailable")
        assert any(all(gap[key] == value for key, value in coordinates.items()) for gap in result.gaps), result.gaps
        if preset == "200日":
            assert result.rows[0]["value"] == pytest.approx(0.6)
            assert result.rows[0]["components"]["denominator"] == 5
            assert result.rows[0]["components"]["full_scope"] is False

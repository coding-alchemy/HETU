import json
from dataclasses import replace

import pytest

from factor_cases import example_query
from hetu_quant.factors import TeachingSource, compute_factor

JAN_26, JAN_29, JAN_30, JAN_31 = (f"2024-01-{day}" for day in (26, 29, 30, 31))


@pytest.mark.parametrize(
    "factor_id, window, expected, matched",
    [
        ("market_beta", 3, 2.30, 1),
        ("residual_volatility", 3, 0.0388844441904472, 1),
        ("relative_strength", 1, 0.028, 0),
    ],
)
@pytest.mark.parametrize("sz_closed", [False, True])
def test_cross_market_returns_match_both_interval_endpoints(tmp_path, factor_id, window, expected, matched, sz_closed):
    _, document, _ = example_query("market_beta")
    _, _, query = example_query(factor_id)
    tables = document["tables"]
    for row in tables["daily_bars"]:
        row["code"] += ".SH" if row["code"] == "A" else ".SZ"
    if sz_closed:
        tables["daily_bars"] = [row for row in tables["daily_bars"] if (row["code"], row["date"]) != ("B.SZ", JAN_30)]
        tables["calendar"].append(dict(date=JAN_30, market="SZ", is_trading_day=False,
                                       product_id="cn_sz_archive_calendar", available_at="2024-01-01T09:00:00+08:00"))
    source = TeachingSource(factor_id, tmp_path)
    (tmp_path / f"{factor_id}.json").write_text(json.dumps(document))
    query = replace(query, subjects=("A.SH",), parameters={"window": window, "benchmark": "B.SZ"})

    result = compute_factor(None, source, query)

    if sz_closed:
        assert result.status == "unavailable"
        assert not result.rows
        gap, = result.gaps
        assert gap["field"] == "return_interval"
        assert gap["subject"] == "A.SH, B.SZ"
        assert gap["period"] == JAN_31
        assert f"需要 {window} 个，已有 {matched} 个" in gap["reason"]
        assert f"{JAN_30} → {JAN_31}" in gap["reason"]
        assert f"{JAN_29} → {JAN_31}" in gap["reason"]
        return

    row, = result.rows
    assert result.status == "available"
    assert row["value"] == pytest.approx(expected)
    dates = [JAN_26, JAN_29, JAN_30, JAN_31][-window - 1:]
    assert [(entry["code"], entry["date"]) for entry in row["inputs"]] == [
        (code, day) for code in ("A.SH", "B.SZ") for day in dates
    ]
    components = row["components"]
    if factor_id == "relative_strength":
        assert (components["anchor"], components["end"]) == (JAN_30, JAN_31)
    else:
        assert components["observations"] == window
        assert components["return_intervals"] == [
            dict(start=start, end=end) for start, end in zip(dates[:-1], dates[1:], strict=True)
        ]

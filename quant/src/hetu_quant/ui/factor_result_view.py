"""Render the currently permitted projection of one factor result."""

import json

import pandas as pd
import streamlit as st

from hetu_quant.factors.catalog import get_factor
from hetu_quant.factors.workbench_inputs import visible_evidence
from hetu_quant.ui.factor_navigation import open_factor_evidence

_DATA_STATES = {"available": "可算", "partial": "部分可算", "unavailable": "缺证"}


_RATIO_UNITS = {"比例", "年化比例", "月度比例"}


def _input_dates(value):
    dates, periods, years, effective, shares = set(), set(), set(), set(), set()

    def visit(item):
        if isinstance(item, dict):
            for key in ("date", "ex_date", "effective_date"):
                if item.get(key):
                    dates.add(str(item[key]))
            for key in ("report_period", "period"):
                period = item.get(key)
                if isinstance(period, str) and period:
                    periods.add(period)
                elif type(period) is int:
                    years.add(str(period))
            if item.get("effective_date"):
                effective.add(str(item["effective_date"]))
            if item.get("date") and ("total_shares" in item or "float_shares" in item):
                shares.add(str(item["date"]))
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return dates, periods, years, effective, shares


def _sample_size(row, definition):
    components = row.get("components", {})
    if isinstance(components.get("eligible"), (list, tuple)):
        return len(components["eligible"])
    if definition and definition.output_shape == "截面" and type(components.get("denominator")) is int:
        return components["denominator"]
    for key in ("sample_count", "member_count", "observations", "sample_codes"):
        value = components.get(key)
        if type(value) is int:
            return value
        if isinstance(value, (list, tuple)):
            return len(value)
    if definition and definition.factor_id == "traded_amount":
        return components.get("count")
    return None


def _date_range(values):
    return "未知" if not values else min(values) if len(values) == 1 else f"{min(values)} 至 {max(values)}"


_HISTORY_SERIES = {
    "market_amount_percentile": ("历史成交额", "人民币元"),
    "market_turnover_percentile": ("历史换手率", "比例"),
    "sector_turnover_percentile": ("历史换手率", "比例"),
    "sector_valuation_percentile": ("历史估值", "倍"),
}


def _render_series(result):
    factor_id = result.get("factor_id")
    measurement, unit = _HISTORY_SERIES.get(factor_id, ("z", "标准分"))
    points = []
    for row in result["rows"]:
        components = row["components"]
        if factor_id in _HISTORY_SERIES:
            dates = components.get("dates")
            values = components.get("values")
            if dates is None:
                dates = [entry["date"] for entry in components.get("coverage", ()) if "date" in entry]
                values = components.get("history")
            series = list(zip(dates, values, strict=True)) if values is not None and len(dates) == len(values) else []
        else:
            series = [(point["date"], point["z"]) for point in components.get("scores", ())
                      if isinstance(point, dict) and "date" in point and "z" in point]
        points.extend({"date": day, measurement: value, "对象": str(row["subject"]), "单位": unit}
                      for day, value in series)
    if points:
        table = pd.DataFrame(points)
        st.caption(f"已计算的日期序列 · {measurement if measurement != 'z' else unit}（{unit}）")
        st.line_chart(table, x="date", y=measurement, color="对象")
        st.dataframe(
            table, hide_index=True,
            column_config={measurement: st.column_config.NumberColumn(format="percent")} if unit == "比例" else None,
        )


def render_result(result, attempt_id=None, *, archive=False):
    key_suffix = f"_{attempt_id}" if archive else ""
    st.subheader(f"选定输入状态 · {_DATA_STATES[result['status']]}")
    st.caption(
        f"请求日 {result['trade_date']}　信息截止 {result['cutoff']}　"
        f"{result['source']['nature']} · {result['source']['id']}"
    )
    st.json(dict(subjects=result["subjects"], parameters=result["parameters"], source=result["source"]), expanded=False)
    if result["rows"]:
        definition = get_factor(result["factor_id"]) if result.get("factor_id") else None
        rows = []
        for row in result["rows"]:
            value = row["value"]
            if isinstance(value, bool):
                value = "是" if value else "否"
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            dates, periods, years, effective, shares = _input_dates(row.get("inputs"))
            parts = row.get("components", {})
            rows.append(
                {
                    "对象": " / ".join(row["subject"]) if isinstance(row["subject"], tuple) else row["subject"],
                    "读数": value,
                    "单位": row["unit"],
                    "实际行情／事件日期": _date_range(dates),
                    "报告期": "、".join(sorted(periods | years)) if periods or years else "未知",
                    **({"用于判断的财年": "、".join(sorted(years))} if years else {}),
                    **({"事件生效日": _date_range(effective)} if effective else {}),
                    **({"股本日期": _date_range(shares)} if shares else {}),
                    "有效样本": _sample_size(row, definition),
                    "命中数": len(parts["matches"]) if isinstance(parts.get("matches"), (list, tuple)) else None,
                }
            )
        table = pd.DataFrame(rows)
        ratio = all(row["unit"] in _RATIO_UNITS for row in result["rows"])
        numeric = all(value is None or type(value) in (int, float) for value in table["读数"])
        if not numeric:
            table["读数"] = table["读数"].map(lambda value: "未知" if value is None else str(value))
        st.dataframe(
            table,
            hide_index=True,
            column_config={"读数": st.column_config.NumberColumn(format="percent")} if ratio and numeric else None,
        )
        if any(row["有效样本"] is None for row in rows):
            st.caption("未给出有效样本数的输出，样本数待证。")
        summaries = []
        for row in result["rows"]:
            for field, value in row["components"].items():
                if isinstance(value, (str, bool, int, float)) or value is None:
                    summaries.append(
                        {
                            "对象": str(row["subject"]),
                            "中间量／分量": field,
                            "值": str(value) if value is not None else "未知",
                        }
                    )
        if summaries:
            with st.expander("主要中间量与分量"):
                st.dataframe(pd.DataFrame(summaries), hide_index=True)
        with st.expander("复算中间量、原始输入与出处"):
            st.json(result["rows"], expanded=False)
            if attempt_id:
                for row in result["rows"]:
                    index = row["row_index"]
                    st.button(
                        f"查看 {row['subject']} 的输入",
                        key=f"factor_evidence_row_{index}{key_suffix}",
                        on_click=open_factor_evidence,
                        args=(attempt_id,),
                        kwargs={"row_index": index},
                    )
    _render_series(result)
    if result["gaps"]:
        st.dataframe(
            pd.DataFrame(
                [{field: value for field, value in gap.items() if field != "evidence"} for gap in result["gaps"]]
            ),
            hide_index=True,
        )
        if any(gap.get("evidence") for gap in result["gaps"]):
            with st.expander("缺口与冲突原件"):
                st.json([{k: v for k, v in gap.items() if k != "gap_index"} for gap in result["gaps"]], expanded=False)
        if attempt_id:
            for gap in result["gaps"]:
                if (index := gap.get("gap_index")) is not None:
                    st.button(
                        f"查看缺口 · {gap['field']}",
                        key=f"factor_evidence_gap_{index}{key_suffix}",
                        on_click=open_factor_evidence,
                        args=(attempt_id,),
                        kwargs={"gap_index": index},
                    )


def render_attempt(store, attempt, *, archive=False):
    try:
        projected = visible_evidence(store, attempt.trial, attempt.result)
    except (ValueError, OSError, PermissionError):
        st.warning("当前无法核对这次试算的来源或许可。")
        return
    st.caption(
        f"因子定义 {attempt.trial.factor_id} v{attempt.trial.definition_version} · 计算完成 {attempt.finished_at}"
    )
    render_result(projected, attempt.id, archive=archive)

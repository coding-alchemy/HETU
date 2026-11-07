"""03.3 研究卡：确定性证据摘要（不依赖 LLM）。

每张卡回答：入选依据（原值／贡献引用）、反方解释、缺口与限制、下一项核验。
景气、政策、估值、资金、事件五类线索未取得时明确“未核验”，不生成肯定事实；
量能只作成交活动代理，不推断资金身份；市场半年下跌时提示“相对优先但整体环境不利”。
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

GATE_STATE = "观察／待人工判断"
CLUE_TYPES = "景气=未核验；政策=未核验；估值=未核验；资金=未核验；事件=未核验"
VOLUME_PROXY_NOTE = "上涨成交额占比仅为成交活动代理，不推断资金身份"
MARKET_DOWN_NOTE = "相对优先但整体环境不利"
PRICE_BASIS_NOTE = (
    "价格收益口径：连续价消除送转/拆并机械跳变，保留现金除息影响；"
    "不同收益口径不混排"
)
NO_HIST_WIDTH_NOTE = (
    "无历史成员，宽度为当前合格成员口径，不生成历史宽度或主题组合收益"
)
CONCENTRATION_NOTE = "等权正收益集中代理，分母为零则不可计算，不称实际指数贡献"

CARD_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "group_id",
    "rationale",
    "counter_evidence",
    "limitations",
    "next_check",
    "clue_types",
    "gate_state",
)


def _fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "不可计算"
    return f"{float(value):.2%}"


def _fmt_score(value: object) -> str:
    if value is None or pd.isna(value):
        return "不可计算"
    return f"{float(value):.4f}"


def _build_card(
    entity_id: str,
    group_id: str,
    mrow: pd.Series,
    aux_row: pd.Series | None,
    bench_6m: float | None,
    coverage: float | None,
) -> dict[str, object]:
    trend = mrow["trend_pct"]
    drawdown_pct = mrow["drawdown_pct"]
    volume_pct = mrow["volume_pct"]
    risk_pct = mrow["risk_pct"]
    rationale = (
        f"入选依据（原值→贡献）：三月相对收益 {_fmt_pct(mrow['excess_3m'])}、"
        f"半年相对收益 {_fmt_pct(mrow['excess_6m'])}"
        f" → 趋势贡献 {trend:.1f}/100；"
        f"半年最大回撤 {_fmt_pct(mrow['max_drawdown_6m'])}"
        f" → 回撤贡献 {drawdown_pct:.1f}；"
        f"60 日上涨成交额占比 {_fmt_pct(mrow['up_amount_60d'])}"
        f"（{VOLUME_PROXY_NOTE}）→ 量能贡献 {volume_pct:.1f}；"
        f"60 日波动率 {_fmt_pct(mrow['volatility_60d'])} → 风险贡献 {risk_pct:.1f}。"
        f"四维等权（各 25%）总分 {_fmt_score(mrow['score'])}。"
    )
    counters: list[str] = []
    excess_6m = mrow["excess_6m"]
    if (
        bench_6m is not None
        and bench_6m < 0
        and excess_6m is not None
        and not pd.isna(excess_6m)
        and float(excess_6m) > 0
    ):
        counters.append(
            f"{MARKET_DOWN_NOTE}：共同基准半年收益 {_fmt_pct(bench_6m)} 为负，"
            "百分位不是上涨概率"
        )
    if aux_row is not None:
        if bool(aux_row.get("overheated")):
            counters.append(
                f"短期过热提示：20 日收益 {_fmt_pct(aux_row['ret_20d'])}"
                f"（同组百分位 {float(aux_row['ret_20d_pct']):.1f} ≥ 90），"
                "提示不改变四维权重"
            )
        if risk_pct is not None and not pd.isna(risk_pct) and float(risk_pct) < 50:
            counters.append(f"60 日波动率高于组内中位（风险贡献 {risk_pct:.1f}）")
        if (
            drawdown_pct is not None
            and not pd.isna(drawdown_pct)
            and float(drawdown_pct) < 50
        ):
            counters.append(f"半年回撤高于组内中位（回撤贡献 {drawdown_pct:.1f}）")
    counter_evidence = (
        "；".join(counters) if counters else "暂无量价反证，仍需五类线索核验"
    )

    limitations = [PRICE_BASIS_NOTE]
    if aux_row is not None:
        if bool(aux_row.get("short_history")):
            limitations.append(
                f"短历史：实际起点 {aux_row['history_start']}，不足三年，"
                "三年图未补造"
            )
        width = aux_row.get("width_60d_ma")
        if width is not None and not pd.isna(width):
            limitations.append(
                f"当前宽度 {float(width):.2f}"
                f"（合格成员站上 60 日均线比例，当前成员口径）；{NO_HIST_WIDTH_NOTE}"
            )
        concentration = aux_row.get("concentration_proxy")
        if concentration is not None and not pd.isna(concentration):
            limitations.append(
                f"集中度 {float(concentration):.4f}（{CONCENTRATION_NOTE}）"
            )
        range_position = aux_row.get("range_position")
        if range_position is None or pd.isna(range_position):
            limitations.append("当前区间位置不可计算（常数序列或单点）")
    if coverage is not None and coverage < 1:
        limitations.append(f"成员覆盖率 {coverage:.2f}（分母含被排除成员）")

    next_check = "补景气、政策、估值、资金身份、事件五类线索后由人工判断行业 Gate"
    if aux_row is not None and bool(aux_row.get("overheated")):
        next_check += "；先核验短期过热是否由可解释事件驱动"
    return {
        "entity_id": entity_id,
        "group_id": group_id,
        "rationale": rationale,
        "counter_evidence": counter_evidence,
        "limitations": "；".join(limitations),
        "next_check": next_check,
        "clue_types": CLUE_TYPES,
        "gate_state": GATE_STATE,
    }


def make_cards(result_tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """由已发布结果表生成确定性研究卡；输入表不足时明确缺口，不生成肯定事实。"""
    metrics = result_tables.get("metrics")
    if metrics is None or not len(metrics):
        return pd.DataFrame(columns=CARD_COLUMNS)
    bench_values = metrics["benchmark_return_6m"].dropna()
    bench_6m = float(bench_values.iloc[0]) if len(bench_values) else None

    aux = result_tables.get("aux")
    boards = result_tables.get("boards")
    slots = result_tables.get("slots")

    aux_index: dict[tuple[str, str], pd.Series] = {}
    if aux is not None and len(aux):
        for _, row in aux.iterrows():
            aux_index[(str(row["entity_kind"]), str(row["entity_id"]))] = row

    board_coverage: dict[str, float] = {}
    selected_boards: list[str] = []
    if boards is not None and len(boards):
        for _, row in boards.iterrows():
            board_coverage[str(row["board_id"])] = float(row["coverage"])
            if bool(row["selected"]):
                selected_boards.append(str(row["board_id"]))

    cards: list[dict[str, object]] = []
    board_metrics = metrics[
        (metrics["entity_kind"] == "board") & (metrics["group_id"] == "")
    ]
    for _, mrow in board_metrics.iterrows():
        board_id = str(mrow["entity_id"])
        if selected_boards and board_id not in selected_boards:
            continue
        cards.append(
            _build_card(
                board_id,
                "",
                mrow,
                aux_index.get(("board", board_id)),
                bench_6m,
                board_coverage.get(board_id),
            )
        )
    if slots is not None and len(slots):
        stock_metrics = metrics[metrics["entity_kind"] == "stock"]
        for _, slot in slots.iterrows():
            board_id = str(slot["board_id"])
            code = str(slot["code"])
            hit = stock_metrics[
                (stock_metrics["group_id"] == board_id)
                & (stock_metrics["entity_id"] == code)
            ]
            if not len(hit):
                continue
            cards.append(
                _build_card(
                    code,
                    board_id,
                    hit.iloc[0],
                    aux_index.get(("stock", code)),
                    bench_6m,
                    None,
                )
            )
    if not cards:
        return pd.DataFrame(columns=CARD_COLUMNS)
    return pd.DataFrame(cards, columns=CARD_COLUMNS)

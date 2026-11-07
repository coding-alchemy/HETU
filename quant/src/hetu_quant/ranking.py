"""03.2 独立两榜、席位与重复。

流程：校验请求与快照一致、免费准入与用途资格（DataStore.qualify）
→ 按 as_of/cutoff 选择可得成员 → 原始指标（metrics.compute_metrics）
→ 同组百分位（四维等权）→ 先选前五板块再取各五只股票 → 重复关联与发布。

主题用合格当前成员各原始特征的中位数聚合，不用主题指数替代；
股票在所属板块全部合格成员中独立排名，分数不计入板块分数；
输入不足保存 limited，不伪装 complete。
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from hetu_quant.data import (
    AccessDenied,
    DataQualityError,
    DataStore,
    UnsupportedActionError,
    assert_single_return_basis,
    build_price_series,
)
from hetu_quant.metrics import compute_metrics
from hetu_quant.models import (
    DataRequest,
    RankingDefinition,
    ResultRef,
    SnapshotRef,
    ranking_definition_to_dict,
)

CN = ZoneInfo("Asia/Shanghai")

CORE_METRICS: tuple[str, ...] = (
    "excess_3m",
    "excess_6m",
    "max_drawdown_6m",
    "up_amount_60d",
    "volatility_60d",
)

TABLE_NAMES: tuple[str, ...] = (
    "boards",
    "slots",
    "stocks",
    "metrics",
    "exclusions",
    "series",
    "cards",
    "overlap",
    "aux",
)

# 九张结果表的固定 schema（列集合与 dtype，03 计划 §2）：发布时为空表补固定
# 空壳，导出 ZIP 与 Notebook 读取路径对零席位结果同样健壮（B4）。
TABLE_SCHEMAS: dict[str, dict[str, str]] = {
    "boards": {
        "board_id": "object",
        "board_kind": "object",
        "name": "object",
        "rank": "int64",
        "score": "float64",
        "eligible_members": "int64",
        "total_members": "int64",
        "coverage": "float64",
        "selected": "bool",
        "limitations": "object",
    },
    "slots": {
        "board_id": "object",
        "code": "object",
        "rank": "int64",
        "score": "float64",
        "duplicate_count": "int64",
    },
    "stocks": {
        "code": "object",
        "name": "object",
        "market": "object",
        "history_start": "object",
        "risk_flags": "object",
    },
    "metrics": {
        "entity_id": "object",
        "entity_kind": "object",
        "group_id": "object",
        "excess_3m": "float64",
        "excess_6m": "float64",
        "max_drawdown_6m": "float64",
        "up_amount_60d": "float64",
        "volatility_60d": "float64",
        "trend_pct": "float64",
        "drawdown_pct": "float64",
        "volume_pct": "float64",
        "risk_pct": "float64",
        "score": "float64",
        "return_3m": "float64",
        "return_6m": "float64",
        "benchmark_return_3m": "float64",
        "benchmark_return_6m": "float64",
        "n_sessions_6m": "int64",
        "n_returns_60d": "int64",
        "history_start": "object",
        "short_history": "bool",
        "source_ref": "object",
    },
    "exclusions": {
        "entity_id": "object",
        "scope": "object",
        "reason": "object",
        "field": "object",
        "period": "object",
        "source_ref": "object",
    },
    "series": {
        "entity_id": "object",
        "series_kind": "object",
        "date": "object",
        "value": "float64",
        "return_basis": "object",
        "source_ref": "object",
    },
    "cards": {
        "entity_id": "object",
        "group_id": "object",
        "rationale": "object",
        "counter_evidence": "object",
        "limitations": "object",
        "next_check": "object",
        "clue_types": "object",
        "gate_state": "object",
    },
    "overlap": {
        "board_a": "object",
        "board_b": "object",
        "intersection_count": "int64",
        "union_count": "int64",
        "jaccard": "float64",
    },
    "aux": {
        "entity_id": "object",
        "entity_kind": "object",
        "group_id": "object",
        "history_start": "object",
        "short_history": "bool",
        "width_60d_ma": "float64",
        "concentration_proxy": "float64",
        "ret_20d": "float64",
        "ret_20d_pct": "float64",
        "overheated": "bool",
        "range_position": "float64",
    },
}


def _finalize_table(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """按固定 schema 补齐缺列并统一 dtype；空表也保留固定列集合。"""
    schema = TABLE_SCHEMAS[name]
    out = frame.reindex(columns=list(schema))
    for column, dtype in schema.items():
        out[column] = out[column].astype(dtype)
    return out


_TRUE_FLAGS = ("True", "1", "true")


def _default_parameters() -> dict[str, object]:
    path = Path(__file__).resolve().parents[2] / "config" / "ranking-defaults.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_parameters(
    base: Mapping[str, object], override: Mapping[str, object]
) -> dict[str, object]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merge_parameters(out[key], value)
        else:
            out[key] = value
    return out


def resolve_ranking_parameters(
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """当时完整有效协议 = 当前默认配置 merge 覆盖参数。"""
    return _merge_parameters(_default_parameters(), overrides or {})


def freeze_ranking_protocol(definition: RankingDefinition) -> RankingDefinition:
    """把保存时点的完整有效协议固化进定义（B2 修复）。

    冻结后的定义携带 ``frozen_protocol``，run_ranking 只消费冻结值；
    之后修改 ranking-defaults.json 不影响已保存定义的结果。
    """
    return RankingDefinition(
        as_of=definition.as_of,
        cutoff=definition.cutoff,
        snapshot_id=definition.snapshot_id,
        benchmark_id=definition.benchmark_id,
        parameters=dict(definition.parameters),
        mode=definition.mode,
        market=definition.market,
        frozen_protocol=resolve_ranking_parameters(definition.parameters),
    )


def _anchor(trading_days: list[str], boundary: date) -> str | None:
    candidates = [day for day in trading_days if day <= boundary.isoformat()]
    return candidates[-1] if candidates else None


def _percentile(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(百分位, 平均名次)：percentile = 100×(average_rank-1)/(n-1)，同值平均名次。

    仅一个对象时百分位置 50（由调用方标记样本不足）。
    """
    if len(values) == 1:
        one = pd.Series([50.0], index=values.index)
        return one, pd.Series([1.0], index=values.index)
    ranks = values.rank(method="average")
    pct = (ranks - 1) / (len(values) - 1) * 100
    return pct, ranks


def _score_table(
    raw: pd.DataFrame, weights: Mapping[str, float]
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """由原始指标计算四维贡献、总分（未舍入）与名次（同分同名次）。"""
    pct_e3, _ = _percentile(raw["excess_3m"])
    pct_e6, _ = _percentile(raw["excess_6m"])
    pct_dd, _ = _percentile(raw["max_drawdown_6m"])
    pct_up, _ = _percentile(raw["up_amount_60d"])
    pct_vol, _ = _percentile(raw["volatility_60d"])
    contributions = pd.DataFrame(
        {
            "trend_pct": (pct_e3 + pct_e6) / 2,
            "drawdown_pct": 100 - pct_dd,
            "volume_pct": pct_up,
            "risk_pct": 100 - pct_vol,
        }
    )
    total_weight = float(sum(weights.values()))
    score = (
        sum(contributions[f"{name}_pct"] * weight for name, weight in weights.items())
        / total_weight
    )
    _, rank = _percentile(-score)
    return contributions, score, rank


def _select_seat5(ordered: list[dict], per_board: int) -> tuple[list[dict], list[dict]]:
    """先选前五；第 5 席并列按代码截取，返回 (入选, 边界并列未入选)。"""
    selected = ordered[:per_board]
    if len(selected) < per_board:
        return selected, []
    boundary_score = selected[-1]["score"]
    boundary = [item for item in ordered[per_board:] if item["score"] == boundary_score]
    return selected, boundary


def _exclusion(
    entity_id: str, scope: str, reason: str, field: str, period: str, source_ref: str
) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "scope": scope,
        "reason": reason,
        "field": field,
        "period": period,
        "source_ref": source_ref,
    }


def _price_frame(frame: pd.DataFrame, close_col: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": frame["date"].astype(str),
            "close": pd.to_numeric(frame[close_col], errors="coerce"),
            "amount_cny": pd.to_numeric(frame["amount_cny"], errors="coerce"),
        }
    )


def run_ranking(
    definition: RankingDefinition,
    data: SnapshotRef,
    output_root: Path,
) -> ResultRef:
    """执行一次两榜研究并发布结构化结果到 ``results/<result_id>/``。"""
    if definition.mode != "free":
        raise AccessDenied("paid mode is not enabled (paid)")
    if definition.snapshot_id != data.snapshot_id:
        raise ValueError(
            f"snapshot mismatch: definition {definition.snapshot_id}"
            f" vs data {data.snapshot_id}"
        )
    output_root = Path(output_root)
    store = DataStore(data.root)
    if definition.frozen_protocol is not None:
        # 已保存定义：只消费保存时冻结的完整协议，不读取当前默认配置
        # （freeze_mapping 已把嵌套结构规范为普通 dict，顶层转 plain dict 供序列化）
        parameters = dict(definition.frozen_protocol)
    else:
        # 直接内存构造的定义：在运行入口按当时默认配置解析（保存路径在
        # ExperimentStore.save_definition 冻结，见 freeze_ranking_protocol）
        parameters = resolve_ranking_parameters(definition.parameters)
    ranking_cfg = parameters["ranking"]
    coverage_cfg = parameters["membership_coverage"]
    liquidity_cfg = parameters["status_liquidity"]
    boundaries = parameters["calendar_boundaries"]
    benchmark_cfg = parameters["benchmark"]
    purpose = str(parameters.get("purpose", "ranking_current"))
    per_board = int(ranking_cfg["boards_per_kind"])
    per_stock = int(ranking_cfg["stocks_per_board"])
    max_seats = int(ranking_cfg["max_seats"])
    weights = dict(ranking_cfg["dimension_weights"])

    manifest = store.load_manifest(data)

    # ---- 公司行动覆盖资格（B1/R1）：按对象+期间消费覆盖证据 ----
    # 口径见 ranking-defaults.json corporate_actions：code 分区产品的 manifest
    # table_files 分区条目携带 coverage [start, end]（该代码的查询期间）。
    # 目标代码分区存在、coverage 覆盖所需期间 [anchor_6m, cutoff] 且提供
    # 产品未列入 failures/gaps → 该对象覆盖已核验（表内该代码无记录 = 已核验
    # 无事件）；分区缺失、缺 coverage 元数据或覆盖不足一律按未核验处理
    # （旧快照无元数据不做宽容推导），按 unknown_coverage=exclude 资格排除，
    # 排除原因记录到代码与期间粒度。整表"已取齐"不能推导目标代码已覆盖。
    actions_cfg = parameters.get("corporate_actions", {})
    if str(actions_cfg.get("unknown_coverage", "exclude")) != "exclude":
        raise ValueError(
            f"corporate_actions unknown_coverage="
            f"{actions_cfg['unknown_coverage']} 未支持："
            "覆盖未核验对象不得参与正常排名"
        )
    actions_products = {
        str(item["product_id"])
        for item in manifest.get("items", [])
        if item.get("table") == "corporate_actions"
    }
    failed_products = {
        str(entry["product_id"]) for entry in manifest.get("failures", [])
    }
    gapped_products = {str(pid) for pid in manifest.get("gaps", {})}
    actions_providers_ok = bool(actions_products) and not (
        actions_products & (failed_products | gapped_products)
    )
    actions_coverage: dict[str, list[str]] = {}
    for entry in manifest.get("table_files", []):
        if entry.get("table") != "corporate_actions":
            continue
        partition = str(entry.get("partition", ""))
        if not partition.startswith("code="):
            continue  # 非代码分区（如整表导入条目）无对象+期间证据 → 未核验
        code = partition[len("code="):].split("|", 1)[0]
        coverage = entry.get("coverage")
        if not (isinstance(coverage, (list, tuple)) and len(coverage) == 2):
            continue  # 缺 coverage 元数据 → 未核验
        span = [str(coverage[0]), str(coverage[1])]
        prior = actions_coverage.get(code)
        actions_coverage[code] = (
            [min(prior[0], span[0]), max(prior[1], span[1])] if prior else span
        )

    calendar = store.read_table(data, "calendar")
    trading_days = sorted(
        str(day)
        for day, flag in zip(calendar["date"], calendar["is_trading_day"], strict=False)
        if str(flag) in _TRUE_FLAGS
    )
    cutoff_str = definition.cutoff.isoformat()
    if cutoff_str not in trading_days:
        raise ValueError(f"cutoff {cutoff_str} 非已收盘交易日，不能回退或平移")
    anchor_6m = _anchor(
        trading_days,
        definition.cutoff - pd.DateOffset(months=int(boundaries["half_year_months"])),
    )
    anchor_3y = _anchor(
        trading_days,
        definition.cutoff - pd.DateOffset(years=int(boundaries["background_years"])),
    )
    if anchor_6m is None:
        raise ValueError("市场日不足半年锚点，拒绝计算")
    window_days = [d for d in trading_days if anchor_6m <= d <= cutoff_str]
    # 窗口与流动性检查一律以 cutoff 为锚：日历可能含 cutoff 之后的交易日，
    # 不得把未来日历日当作"应有行情"
    days_to_cutoff = [d for d in trading_days if d <= cutoff_str]
    tail_days = days_to_cutoff[-61:]
    recent20_days = days_to_cutoff[-20:]

    def actions_unverified_reason(code: str) -> str:
        """公司行动覆盖未核验原因（空串 = 已核验）；所需期间即排名窗口
        [anchor_6m, cutoff]，覆盖证据按对象+期间消费（R1）。"""
        if not actions_providers_ok:
            return "行动提供产品未取齐（failures/gaps），全对象覆盖未核验"
        coverage = actions_coverage.get(code)
        if coverage is None:
            return "无该代码行动分区或分区缺 coverage 元数据，覆盖未核验"
        if coverage[0] > anchor_6m or coverage[1] < cutoff_str:
            return (
                f"该代码行动覆盖 {coverage[0]}..{coverage[1]} 未含所需期间"
                f" {anchor_6m}..{cutoff_str}"
            )
        return ""

    # ---- 数据表与口径 ----
    bars = store.read_table(data, "daily_bars")
    status = store.read_table(data, "security_status")
    actions = store.read_table(data, "corporate_actions")
    boards_tbl = store.read_table(data, "boards")
    members_tbl = store.read_table(data, "memberships")
    index_bars = store.read_table(data, "index_bars")
    basis = assert_single_return_basis([index_bars])
    if basis != benchmark_cfg["return_basis"]:
        raise ValueError(
            f"return_basis 冲突：行情为 {basis}，冻结口径为"
            f" {benchmark_cfg['return_basis']}，拒绝混排"
        )
    bench_code = str(benchmark_cfg["code"])
    benchmark_tbl = index_bars[index_bars["code"] == bench_code]
    if not len(benchmark_tbl):
        raise ValueError(f"benchmark {bench_code} missing from snapshot")
    bench_frame = pd.DataFrame(
        {
            "date": benchmark_tbl["date"].astype(str),
            "close": pd.to_numeric(benchmark_tbl["close"], errors="coerce"),
            "return_basis": benchmark_tbl["return_basis"],
        }
    )
    bench_close = bench_frame.set_index("date")["close"]

    metric_params = {
        "half_year_months": int(boundaries["half_year_months"]),
        "quarter_months": int(boundaries["quarter_months"]),
        "background_years": int(boundaries["background_years"]),
        "recent_trading_days": 60,
    }

    # ---- 用途资格与免费权限（DataStore.qualify 统一准入）----
    stock_codes = sorted(
        {str(c) for c in bars["code"]} | {str(c) for c in members_tbl["code"]}
    )
    product_ids = tuple(
        sorted({str(item["product_id"]) for item in manifest.get("items", [])})
    )
    request = DataRequest(
        product_ids=product_ids,
        codes=tuple(stock_codes),
        start=date.fromisoformat(anchor_6m),
        end=definition.cutoff,
        as_of=definition.as_of,
        purpose=purpose,
        mode="free",
    )
    report = store.qualify(request, data)

    exclusions: list[dict[str, object]] = []
    as_of_str = definition.as_of.isoformat()

    # ---- 股票资格：资格排除 → 停牌观察 → 指标缺口 → 流动性 ----
    eligible_stocks: dict[str, dict[str, object]] = {}
    for code in stock_codes:
        qual_reasons = report.excluded.get(code, ())
        if qual_reasons:
            exclusions.append(
                _exclusion(
                    code,
                    "eligibility",
                    ";".join(qual_reasons),
                    "qualify",
                    "window",
                    "quality_report",
                )
            )
            continue
        own_status = status[status["code"] == code]
        if not len(own_status):
            exclusions.append(
                _exclusion(
                    code,
                    "observation",
                    "unknown_status: 状态未知，不得当正常",
                    "security_status",
                    "half_year",
                    "security_status",
                )
            )
            continue
        suspended = set(
            own_status[own_status["is_suspended"].astype(str).isin(_TRUE_FLAGS)][
                "date"
            ].astype(str)
        )
        if suspended & set(window_days):
            days = sorted(suspended & set(window_days))
            exclusions.append(
                _exclusion(
                    code,
                    "observation",
                    f"suspended_observation: 半年必要窗口明确停牌"
                    f" {days[0]}..{days[-1]}，保守列观察，"
                    "停牌与采集缺失原因分列",
                    "is_suspended",
                    "half_year",
                    "security_status",
                )
            )
            continue
        own_bars = bars[bars["code"] == code].sort_values("date")
        close_col = (
            "close_price_return"
            if "close_price_return" in own_bars.columns
            and len(own_bars)
            and own_bars["close_price_return"].notna().all()
            else "close_raw"
        )
        if close_col == "close_raw" and len(own_bars):
            # 冻结口径（01 §2 / 03 §1）：股票收益用消除送转机械跳变、保留现金
            # 除息影响的连续价格收益列；标准化层未填该列时按 01 的
            # build_price_series 以原始收盘+公司行动构建，不静默退回未复权价。
            # 构建前必须按对象+期间证明行动覆盖：该代码分区缺失、缺 coverage
            # 元数据或覆盖不足时按资格排除并记录代码与期间粒度原因，不把空
            # 事件当"没有行动"（B1/R1）。
            coverage_reason = actions_unverified_reason(code)
            if coverage_reason:
                exclusions.append(
                    _exclusion(
                        code,
                        "eligibility",
                        "corporate_actions_coverage: 复权口径需公司行动记录，"
                        f"{code} {coverage_reason}；空事件不得当无行动，"
                        "未复权价格不参与正常排名",
                        "corporate_actions",
                        "window",
                        "manifest",
                    )
                )
                continue
            try:
                own_bars = build_price_series(own_bars, actions)
                close_col = "close_price_return"
            except (UnsupportedActionError, DataQualityError) as error:
                exclusions.append(
                    _exclusion(
                        code,
                        "eligibility",
                        f"return_basis_unverified: {error}",
                        "close_price_return",
                        "window",
                        "daily_bars+corporate_actions",
                    )
                )
                continue
        frame = _price_frame(own_bars, close_col)
        metrics = compute_metrics(frame, bench_frame, definition.cutoff, metric_params)
        missing = [key for key in CORE_METRICS if metrics[key] is None]
        if missing:
            gaps = ";".join(metrics["gaps"])
            exclusions.append(
                _exclusion(
                    code,
                    "observation",
                    f"data_gap: 必需指标缺口 {','.join(missing)}；{gaps}",
                    ",".join(missing),
                    "window",
                    "daily_bars",
                )
            )
            continue
        by_day = frame.set_index("date")
        if any(day not in by_day.index for day in recent20_days):
            exclusions.append(
                _exclusion(
                    code,
                    "observation",
                    "data_gap: 最近 20 个交易日成交额不齐",
                    "amount_cny",
                    "20d",
                    "daily_bars",
                )
            )
            continue
        median20 = float(
            np.median([by_day.loc[day, "amount_cny"] for day in recent20_days])
        )
        if median20 < float(liquidity_cfg["amount_median_20d_min_cny"]):
            exclusions.append(
                _exclusion(
                    code,
                    "eligibility",
                    f"liquidity: 最近 20 日成交额中位数 {median20:.0f} 元"
                    f" < {liquidity_cfg['amount_median_20d_min_cny']:.0f} 元",
                    "amount_cny",
                    "20d",
                    "daily_bars",
                )
            )
            continue
        amount60 = float(by_day.loc[tail_days, "amount_cny"].sum())
        min_amount60 = float(liquidity_cfg["amount_sum_60d_min_cny_exclusive"])
        if amount60 <= min_amount60:
            exclusions.append(
                _exclusion(
                    code,
                    "eligibility",
                    "liquidity: 必要 60 日成交额总和不大于 0",
                    "amount_cny",
                    "60d",
                    "daily_bars",
                )
            )
            continue
        eligible_stocks[code] = {
            "metrics": metrics,
            "frame": frame,
            "history_start": str(frame["date"].min()),
        }

    # ---- 板块资格与聚合 ----
    def active_members(board_id: str) -> list[str]:
        own = members_tbl[members_tbl["board_id"] == board_id]
        out = set()
        for row in own.itertuples(index=False):
            available = str(row.available_at) if pd.notna(row.available_at) else ""
            eff_from = str(row.effective_from) if pd.notna(row.effective_from) else ""
            eff_to = str(row.effective_to) if pd.notna(row.effective_to) else ""
            if available and available > as_of_str:
                continue
            if eff_from and eff_from > cutoff_str:
                continue
            if eff_to and eff_to <= cutoff_str:
                continue
            out.add(str(row.code))
        return sorted(out)

    def index_frame(board_id: str) -> pd.DataFrame | None:
        direct = index_bars[index_bars["code"] == board_id]
        if not len(direct):
            candidates = [f"{board_id}.{suffix}" for suffix in ("SH", "SZ", "SI")]
            direct = index_bars[index_bars["code"].isin(candidates)]
        if not len(direct):
            return None
        return pd.DataFrame(
            {
                "date": direct["date"].astype(str),
                "close": pd.to_numeric(direct["close"], errors="coerce"),
                "amount_cny": pd.to_numeric(direct["amount_cny"], errors="coerce"),
                "return_basis": direct["return_basis"],
            }
        )

    series_start = anchor_3y or trading_days[0]
    series_days = [d for d in trading_days if series_start <= d <= cutoff_str]

    def board_series(board_id: str) -> pd.Series | None:
        """板块代表序列：行业用指数，主题用合格成员中位数（不用主题指数替代）。"""
        idx = index_frame(board_id)
        if idx is not None:
            return pd.Series(
                {
                    d: float(v)
                    for d, v in zip(idx["date"], idx["close"], strict=False)
                    if d <= cutoff_str
                }
            )
        series = []
        for code in members_of.get(board_id, []):
            frame = eligible_stocks[code]["frame"].set_index("date")["close"]
            available = pd.Series(
                {d: float(frame[d]) for d in series_days if d in frame.index}
            )
            if len(available) == len(series_days):
                series.append(available)
        if not series:
            return None
        return pd.concat(series, axis=1).median(axis=1)

    # ---- 主题分类依据（冻结协议 category.theme_basis，规则 2026-10-15 冻结）----
    # v2（em-themelib-20261015-v2）：R1 名称与东财题材库（theme_library 表）
    # 精确一致且逐条核验分类 audited_boards[board_id]=industrial_theme 才入
    # 主题榜——名称命中不等于产业属性；R3 核验为 style/listing_board/
    # margin/index_membership/regional 标签 → 排除并引用证据；R4 释义不可得
    # （unverified）→ 保守排除；命中但不在核验覆盖内 → 保守排除；R2 未命中
    # → unknown 排除并留存。核验表缺失或为空（含 v1 协议、被剥离核验表的
    # v2 协议）→ 主题无产业分类依据，受限不降级放行（全部主题排除并留存）；
    # 已生成的旧结果产物不受影响，重新执行须升级为含核验表的协议版本。
    # 快照缺 theme_library 表 → 全部主题按无依据排除（保持旧行为语义）；
    # classification_version 后缀不参与本门判断。
    theme_basis_cfg = (parameters.get("category") or {}).get("theme_basis")
    theme_basis_names: dict[str, str] = {}
    if theme_basis_cfg is not None:
        try:
            theme_library_tbl = store.read_table(data, "theme_library")
        except Exception:  # noqa: BLE001
            theme_library_tbl = None
        if theme_library_tbl is not None and len(theme_library_tbl):
            theme_basis_names = {
                str(row.theme_name): str(row.theme_code)
                for row in theme_library_tbl.itertuples(index=False)
            }
    # 核验表（board_id → 逐条核验分类结论）：主题入榜的唯一放行依据
    theme_audited = (
        theme_basis_cfg.get("audited_boards") or {} if theme_basis_cfg is not None else {}
    )
    theme_pass_category = str(
        theme_basis_cfg.get("audited_category_pass", "industrial_theme")
        if theme_basis_cfg is not None
        else "industrial_theme"
    )
    if theme_basis_cfg is None:
        theme_gate_reason = (
            "theme_basis: 冻结协议无 theme_basis 分类依据配置（旧定义），"
            "主题无分类依据材料（unknown），不进入主题榜，排除依据留存"
        )
    elif not theme_basis_names:
        theme_gate_reason = (
            "theme_basis: 快照无 theme_library 分类依据表或表为空"
            "（旧快照缺表），主题无分类依据材料（unknown），"
            "不进入主题榜，排除依据留存"
        )
    elif not theme_audited:
        theme_gate_reason = (
            "theme_basis: 协议 "
            f"{theme_basis_cfg.get('rule_version', '')}"
            " 无逐条核验表（audited_boards 缺失或为空），"
            "主题无产业分类依据，受限不降级放行，不进入主题榜，"
            "排除依据留存；重新执行须升级为含核验表的协议版本"
        )
    else:
        theme_gate_reason = ""

    boards_raw: dict[str, list[dict[str, object]]] = {"industry": [], "theme": []}
    members_of: dict[str, list[str]] = {}
    board_meta: dict[str, dict[str, object]] = {}
    for row in boards_tbl.itertuples(index=False):
        board_id = str(row.board_id)
        kind = str(row.board_kind)
        # 概念目录只提供待核验候选；限定来源后送入同一个双条件分类门。
        # 其他 unclassified 对象仍按未知排除，不用分类版本后缀推断资格。
        if kind == "unclassified" and str(row.product_id) == "em_theme_catalog":
            kind = "theme"
        if kind not in ("industry", "theme"):
            exclusions.append(
                _exclusion(
                    board_id,
                    "classification",
                    f"board_kind={kind} 非已证实的行业/主题分类"
                    "（风格、标签类或分类依据缺失），不进入行业或主题榜，"
                    "排除依据留存",
                    "board_kind",
                    "as_of",
                    "boards",
                )
            )
            continue
        if kind == "theme" and theme_gate_reason:
            exclusions.append(
                _exclusion(
                    board_id,
                    "classification",
                    theme_gate_reason,
                    "theme_basis",
                    "as_of",
                    "theme_library",
                )
            )
            continue
        if kind == "theme":
            board_name = str(row.name)
            if board_name not in theme_basis_names:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "classification",
                        "theme_basis: 名称未命中分类依据材料"
                        f"（{theme_basis_cfg.get('rule_version', '')}"
                        " 名称精确匹配 unknown），不按名称猜测纳入，"
                        "不进入主题榜，排除依据留存",
                        "theme_basis",
                        "as_of",
                        "theme_library",
                    )
                )
                continue
            audit = theme_audited.get(board_id)
            if audit is None:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "classification",
                        "theme_basis: 名称命中题材库但不在逐条核验附件"
                        f"覆盖内（{theme_basis_cfg.get('rule_version', '')}"
                        " audited_boards），保守排除，排除依据留存",
                        "theme_basis",
                        "as_of",
                        "theme_library",
                    )
                )
                continue
            audit_category = str(audit.get("category", ""))
            if audit_category != theme_pass_category:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "classification",
                        "theme_basis: 名称命中题材库但发布者释义逐条核验为"
                        f"{audit_category}（证据 "
                        f"{audit.get('evidence', '')}），"
                        "非产业主题，不进入主题榜，排除依据留存",
                        "theme_basis",
                        "as_of",
                        "theme_library",
                    )
                )
                continue
        known = active_members(board_id)
        eligible = [c for c in known if c in eligible_stocks]
        members_of[board_id] = eligible
        coverage = len(eligible) / len(known) if known else 0.0
        meta = {
            "name": str(row.name),
            "board_kind": kind,
            "total_members": len(known),
            "coverage": coverage,
            "history_start": cutoff_str,
        }
        if kind == "theme":
            # 分类依据可追查：规则版本 + 命中题材代码 + 核验分类
            # （boards 表 limitations 展示）
            meta["theme_basis_note"] = (
                f"theme_basis: {theme_basis_cfg.get('rule_version', '')}"
                f"（名称精确命中题材库 {theme_basis_names[board_name]}，"
                "发布者释义逐条核验 "
                f"{theme_audited.get(board_id, {}).get('category', '')}，"
                f"证据 {theme_audited.get(board_id, {}).get('evidence', '')}）"
            )
        board_meta[board_id] = meta
        if kind == "industry":
            idx = index_frame(board_id)
            if idx is None:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "eligibility",
                        "missing_index: 无合格行业指数行情",
                        "index_bars",
                        "window",
                        "index_bars",
                    )
                )
                continue
            metrics = compute_metrics(
                idx, bench_frame, definition.cutoff, metric_params
            )
            missing = [key for key in CORE_METRICS if metrics[key] is None]
            if missing:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "eligibility",
                        f"data_gap: 行业指数指标缺口 {','.join(missing)}",
                        ",".join(missing),
                        "window",
                        "index_bars",
                    )
                )
                continue
            min_stocks = int(coverage_cfg["industry_min_eligible_stocks"])
            if len(eligible) < min_stocks:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "eligibility",
                        f"insufficient_eligible_stocks: {len(eligible)} < {min_stocks}",
                        "memberships",
                        "as_of",
                        "memberships",
                    )
                )
                continue
            meta["history_start"] = str(idx["date"].min())
            boards_raw[kind].append({"board_id": board_id, "metrics": metrics})
        else:
            min_eligible = int(coverage_cfg["theme_min_eligible"])
            min_coverage = float(coverage_cfg["theme_min_coverage"])
            if len(eligible) < min_eligible or coverage < min_coverage:
                exclusions.append(
                    _exclusion(
                        board_id,
                        "eligibility",
                        f"coverage: 合格成员 {len(eligible)}/{len(known)}，"
                        f"覆盖率 {coverage:.4f} < {min_coverage:.2f}"
                        f" 或不足 {min_eligible} 只",
                        "memberships",
                        "as_of",
                        "memberships",
                    )
                )
                continue
            member_metrics = pd.DataFrame(
                [eligible_stocks[c]["metrics"] for c in eligible]
            )
            metrics = {key: float(member_metrics[key].median()) for key in CORE_METRICS}
            metrics["history_days"] = float(member_metrics["history_days"].median())
            metrics["short_history"] = float(member_metrics["short_history"].max())
            metrics["gaps"] = ()
            meta["history_start"] = min(
                eligible_stocks[c]["history_start"] for c in eligible
            )
            boards_raw[kind].append({"board_id": board_id, "metrics": metrics})

    # ---- 同组百分位与总分：先选前五板块 ----
    boards_out: list[dict[str, object]] = []
    selected_boards: dict[str, list[dict[str, object]]] = {}
    for kind, items in boards_raw.items():
        if not items:
            selected_boards[kind] = []
            continue
        raw = pd.DataFrame(
            [item["metrics"] for item in items],
            index=[item["board_id"] for item in items],
        )
        contributions, score, rank = _score_table(raw, weights)
        ordered = sorted(
            items,
            key=lambda item: (
                -float(score[item["board_id"]]),
                item["board_id"],
            ),
        )
        for item in ordered:
            board_id = item["board_id"]
            item["trend_pct"] = float(contributions.loc[board_id, "trend_pct"])
            item["drawdown_pct"] = float(contributions.loc[board_id, "drawdown_pct"])
            item["volume_pct"] = float(contributions.loc[board_id, "volume_pct"])
            item["risk_pct"] = float(contributions.loc[board_id, "risk_pct"])
            item["score"] = float(score[board_id])
            item["rank"] = int(rank[board_id])
        selected, boundary = _select_seat5(ordered, per_board)
        selected_boards[kind] = selected
        for item in ordered:
            board_id = item["board_id"]
            limitation = str(board_meta[board_id].get("theme_basis_note") or "")
            if len(ordered) == 1:
                limitation += ("；" if limitation else "") + (
                    "样本不足：仅 1 个合格对象，百分位置 50，"
                    "不能通过正常五个验收"
                )
            if any(item is other for other in boundary):
                names = ", ".join(
                    [item["board_id"]] + [other["board_id"] for other in boundary]
                )
                limitation += ("；" if limitation else "") + (
                    f"第 5 席并列（总分 {item['score']:.4f}）：并列对象 {names}，"
                    "按代码截取，截取不代表更优，不突破 50 席位、不补位"
                )
            boards_out.append(
                {
                    "board_id": board_id,
                    "board_kind": kind,
                    "name": board_meta[board_id]["name"],
                    "rank": item["rank"],
                    "score": round(item["score"], 4),
                    "eligible_members": len(members_of[board_id]),
                    "total_members": board_meta[board_id]["total_members"],
                    "coverage": round(float(board_meta[board_id]["coverage"]), 4),
                    "selected": any(item is sel for sel in selected),
                    "limitations": limitation,
                }
            )
        for item in boundary:
            exclusions.append(
                _exclusion(
                    item["board_id"],
                    "board_boundary_tie",
                    f"board tie: 第 5 席并列按代码截取，{item['board_id']} 未入选，"
                    "不补位",
                    "score",
                    "as_of",
                    "boards",
                )
            )

    # ---- 股票同组百分位（板块全部合格成员内独立排名）----
    stock_rows: list[dict[str, object]] = []
    stock_score: dict[tuple[str, str], dict[str, object]] = {}
    for kind in ("industry", "theme"):
        for board_item in selected_boards[kind]:
            board_id = board_item["board_id"]
            members = members_of[board_id]
            raw = pd.DataFrame(
                [eligible_stocks[c]["metrics"] for c in members], index=members
            )
            contributions, score, rank = _score_table(raw, weights)
            ordered = sorted(members, key=lambda c: (-float(score[c]), c))
            chosen, boundary = _select_seat5(
                [{"code": c, "score": float(score[c])} for c in ordered], per_stock
            )
            chosen_codes = {item["code"] for item in chosen}
            if boundary:
                tied = ", ".join(sorted(item["code"] for item in boundary))
                exclusions.append(
                    _exclusion(
                        board_id,
                        "slot_boundary_tie",
                        f"第 5 席并列：{tied} 与入选股票并列，按代码截取，不补位",
                        "score",
                        "as_of",
                        "metrics",
                    )
                )
            for code in members:
                metrics = eligible_stocks[code]["metrics"]
                stock_score[(board_id, code)] = {
                    "score": float(score[code]),
                    "rank": int(rank[code]),
                    "selected": code in chosen_codes,
                }
                stock_rows.append(
                    {
                        "entity_id": code,
                        "entity_kind": "stock",
                        "group_id": board_id,
                        "excess_3m": metrics["excess_3m"],
                        "excess_6m": metrics["excess_6m"],
                        "max_drawdown_6m": metrics["max_drawdown_6m"],
                        "up_amount_60d": metrics["up_amount_60d"],
                        "volatility_60d": metrics["volatility_60d"],
                        "trend_pct": float(contributions.loc[code, "trend_pct"]),
                        "drawdown_pct": float(contributions.loc[code, "drawdown_pct"]),
                        "volume_pct": float(contributions.loc[code, "volume_pct"]),
                        "risk_pct": float(contributions.loc[code, "risk_pct"]),
                        "score": float(score[code]),
                        "return_3m": metrics["return_3m"],
                        "return_6m": metrics["return_6m"],
                        "benchmark_return_3m": metrics["benchmark_return_3m"],
                        "benchmark_return_6m": metrics["benchmark_return_6m"],
                        "n_sessions_6m": len(window_days),
                        "n_returns_60d": 60,
                        "history_start": eligible_stocks[code]["history_start"],
                        "short_history": bool(metrics["short_history"]),
                        "source_ref": "daily_bars",
                    }
                )

    # ---- 席位与重复（重复股记录 duplicate_count，不占额外席位）----
    slot_rows: list[dict[str, object]] = []
    for kind in ("industry", "theme"):
        for board_item in selected_boards[kind]:
            board_id = board_item["board_id"]
            members = sorted(
                members_of[board_id],
                key=lambda c: (-float(stock_score[(board_id, c)]["score"]), c),
            )
            for code in members[:per_stock]:
                info = stock_score[(board_id, code)]
                slot_rows.append(
                    {
                        "board_id": board_id,
                        "code": code,
                        "rank": info["rank"],
                        "score": round(float(info["score"]), 4),
                        "duplicate_count": 0,
                    }
                )
    slot_rows = slot_rows[:max_seats]
    code_counts = pd.Series([row["code"] for row in slot_rows]).value_counts()
    for row in slot_rows:
        row["duplicate_count"] = int(code_counts[row["code"]]) - 1

    slotted_codes = sorted({row["code"] for row in slot_rows})
    stocks_out = [
        {
            "code": code,
            "name": code,
            "market": code.rsplit(".", 1)[-1].upper(),
            "history_start": eligible_stocks[code]["history_start"],
            "risk_flags": (
                "short_history"
                if eligible_stocks[code]["metrics"]["short_history"]
                else ""
            ),
        }
        for code in slotted_codes
    ]

    # ---- 指标表（板块行 + 股票行；另存绝对收益、样本数与来源行引用）----
    metric_rows: list[dict[str, object]] = []
    for kind, items in boards_raw.items():
        for item in items:
            metrics = item["metrics"]
            source_ref = "index_bars" if kind == "industry" else "members_median"
            metric_rows.append(
                {
                    "entity_id": item["board_id"],
                    "entity_kind": "board",
                    "group_id": "",
                    "excess_3m": metrics["excess_3m"],
                    "excess_6m": metrics["excess_6m"],
                    "max_drawdown_6m": metrics["max_drawdown_6m"],
                    "up_amount_60d": metrics["up_amount_60d"],
                    "volatility_60d": metrics["volatility_60d"],
                    "trend_pct": item.get("trend_pct"),
                    "drawdown_pct": item.get("drawdown_pct"),
                    "volume_pct": item.get("volume_pct"),
                    "risk_pct": item.get("risk_pct"),
                    "score": item.get("score"),
                    "return_3m": metrics.get("return_3m"),
                    "return_6m": metrics.get("return_6m"),
                    "benchmark_return_3m": metrics.get("benchmark_return_3m"),
                    "benchmark_return_6m": metrics.get("benchmark_return_6m"),
                    "n_sessions_6m": len(window_days),
                    "n_returns_60d": 60,
                    "history_start": board_meta[item["board_id"]]["history_start"],
                    "short_history": bool(metrics.get("short_history")),
                    "source_ref": source_ref,
                }
            )
    metric_rows.extend(stock_rows)

    # ---- 序列：实际历史起点、起点 100 归一化、相对基准、回撤 ----
    series_rows: list[dict[str, object]] = []

    def add_series(entity_id: str, closes: pd.Series) -> None:
        values = closes.astype(float).sort_index()
        first = float(values.iloc[0])
        indexed = values / first * 100 if first else values * float("nan")
        common = [d for d in values.index if d in bench_close.index]
        if common and first:
            bench_first = float(bench_close[common[0]])
            relative = pd.Series(
                {
                    d: (float(values[d]) / first)
                    / (float(bench_close[d]) / bench_first)
                    * 100
                    for d in common
                }
            )
        else:
            relative = pd.Series(dtype="float64")
        drawdown = 1 - values / values.cummax()
        for day in values.index:
            series_rows.append(
                {
                    "entity_id": entity_id,
                    "series_kind": "price_indexed",
                    "date": day,
                    "value": float(indexed[day]),
                    "return_basis": basis,
                    "source_ref": "series",
                }
            )
        for day, value in relative.items():
            series_rows.append(
                {
                    "entity_id": entity_id,
                    "series_kind": "relative_bench",
                    "date": day,
                    "value": float(value),
                    "return_basis": basis,
                    "source_ref": "series",
                }
            )
        for day in values.index:
            series_rows.append(
                {
                    "entity_id": entity_id,
                    "series_kind": "drawdown",
                    "date": day,
                    "value": float(drawdown[day]),
                    "return_basis": basis,
                    "source_ref": "series",
                }
            )

    bench_series = pd.Series(
        {d: float(bench_close[d]) for d in series_days if d in bench_close.index}
    )
    if len(bench_series):
        add_series(bench_code, bench_series)

    selected_ids = {
        item["board_id"]
        for kind in ("industry", "theme")
        for item in selected_boards[kind]
    }
    for board_id in sorted(selected_ids):
        closes = board_series(board_id)
        if closes is not None and len(closes):
            add_series(board_id, closes)
    for code in slotted_codes:
        frame = eligible_stocks[code]["frame"].set_index("date")["close"]
        closes = pd.Series(
            {d: float(frame[d]) for d in series_days if d in frame.index}
        )
        if len(closes):
            add_series(code, closes)

    # ---- 辅助诊断：宽度 / 集中度 / 20 日过热 / 区间位置 ----
    def ma60_above(code: str) -> float | None:
        # R2：均线口径 = 最近 60 个交易日收盘；tail_days 的 61 日含为计算
        # 60 个日收益多取的首日，不得混入均线（主题与行业成员同口径）。
        frame = eligible_stocks[code]["frame"].set_index("date")["close"]
        window = [d for d in tail_days[-60:] if d in frame.index]
        if len(window) < 60:
            return None
        return 1.0 if float(frame[cutoff_str]) > float(frame[window].mean()) else 0.0

    aux_rows: list[dict[str, object]] = []
    for kind in ("industry", "theme"):
        raw20: list[tuple[str, float | None]] = []
        for item in boards_raw[kind]:
            series = board_series(item["board_id"])
            ret20 = None
            if series is not None and len(series) >= 21:
                days_present = sorted(d for d in series.index if d <= cutoff_str)
                last = float(series[days_present[-1]])
                ret20 = last / float(series[days_present[-21]]) - 1
            raw20.append((item["board_id"], ret20))
        values = pd.Series({b: r for b, r in raw20 if r is not None})
        pct_map = _percentile(values)[0] if len(values) else pd.Series(dtype="float64")
        for board_id, ret20 in raw20:
            series = board_series(board_id)
            range_position = None
            if series is not None and len(series) > 1:
                span = float(series.max()) - float(series.min())
                if span > 0:
                    last = float(series.iloc[-1])
                    range_position = (last - float(series.min())) / span
            width = None
            concentration = None
            # 行业与主题同口径（03 计划 §2）：宽度 = 合格当前成员站上
            # 60 日均线的比例；集中度代理 = 正收益成员前 5 占正收益总和
            # 比例，不用行业指数自身 0/1 或空值替代（B3）。
            flags = [
                flag
                for flag in (ma60_above(c) for c in members_of[board_id])
                if flag is not None
            ]
            width = float(np.mean(flags)) if flags else None
            rets = [
                eligible_stocks[c]["metrics"]["return_6m"]
                for c in members_of[board_id]
            ]
            positives = sorted(
                [r for r in rets if r is not None and r > 0], reverse=True
            )
            if positives and sum(positives) > 0:
                concentration = float(sum(positives[:5]) / sum(positives))
            aux_rows.append(
                {
                    "entity_id": board_id,
                    "entity_kind": "board",
                    "group_id": "",
                    "history_start": board_meta[board_id]["history_start"],
                    "short_history": False,
                    "width_60d_ma": width,
                    "concentration_proxy": concentration,
                    "ret_20d": ret20,
                    "ret_20d_pct": (
                        float(pct_map[board_id]) if board_id in pct_map.index else None
                    ),
                    "overheated": (
                        bool(pct_map[board_id] >= 90)
                        if board_id in pct_map.index
                        else False
                    ),
                    "range_position": range_position,
                }
            )
    for code in slotted_codes:
        frame = eligible_stocks[code]["frame"].set_index("date")["close"]
        days_present = sorted(d for d in frame.index if d <= cutoff_str)
        ret20 = None
        if len(days_present) >= 21:
            last = float(frame[days_present[-1]])
            ret20 = last / float(frame[days_present[-21]]) - 1
        values = pd.Series(
            {d: float(frame[d]) for d in series_days if d in frame.index}
        )
        range_position = None
        if len(values) > 1:
            span = float(values.max()) - float(values.min())
            if span > 0:
                last = float(values.iloc[-1])
                range_position = (last - float(values.min())) / span
        short_flag = bool(eligible_stocks[code]["metrics"]["short_history"])
        aux_rows.append(
            {
                "entity_id": code,
                "entity_kind": "stock",
                "group_id": "",
                "history_start": eligible_stocks[code]["history_start"],
                "short_history": short_flag,
                "width_60d_ma": ma60_above(code),
                "concentration_proxy": None,
                "ret_20d": ret20,
                "ret_20d_pct": None,
                "overheated": False,
                "range_position": range_position,
            }
        )
    for kind in ("industry", "theme"):
        for board_item in selected_boards[kind]:
            board_id = board_item["board_id"]
            values: dict[str, float] = {}
            for code in members_of[board_id]:
                frame = eligible_stocks[code]["frame"].set_index("date")["close"]
                days_present = sorted(d for d in frame.index if d <= cutoff_str)
                if len(days_present) >= 21:
                    last = float(frame[days_present[-1]])
                    values[code] = last / float(frame[days_present[-21]]) - 1
            if len(values) > 1:
                pct_map = _percentile(pd.Series(values))[0]
                for code, value in values.items():
                    match = [
                        r
                        for r in aux_rows
                        if r["entity_id"] == code and r["entity_kind"] == "stock"
                    ]
                    if not match:
                        continue
                    hit = match[0]
                    hit["ret_20d"] = value
                    hit["ret_20d_pct"] = float(pct_map[code])
                    hit["overheated"] = bool(pct_map[code] >= 90)

    # ---- 同一时点成员重叠及席位重复关系 ----
    overlap_rows: list[dict[str, object]] = []
    selected_list = sorted(selected_ids)
    for i, board_a in enumerate(selected_list):
        for board_b in selected_list[i + 1:]:
            set_a = set(members_of.get(board_a, []))
            set_b = set(members_of.get(board_b, []))
            union = set_a | set_b
            overlap_rows.append(
                {
                    "board_a": board_a,
                    "board_b": board_b,
                    "intersection_count": len(set_a & set_b),
                    "union_count": len(union),
                    "jaccard": len(set_a & set_b) / len(union) if union else 0.0,
                }
            )

    # ---- 状态与发布 ----
    limitations = list(report.limitations)
    if any(
        item["scope"] == "eligibility" and "corporate_actions_coverage" in str(
            item["reason"]
        )
        for item in exclusions
    ):
        limitations.append(
            "corporate_actions 覆盖未核验：需现场复权的对象已按资格排除"
            "（未把空事件当无行动，未复权价格不参与正常排名）"
        )
    for kind in ("industry", "theme"):
        eligible_count = len(boards_raw[kind])
        if eligible_count < per_board:
            limitations.append(
                f"{kind} boards eligible {eligible_count} < {per_board}："
                "数量不足，结果受限，不自行降门槛"
            )
    n_slots = len(slot_rows)
    both_full = all(
        len(selected_boards[kind]) == per_board for kind in ("industry", "theme")
    )
    complete = report.can_run and both_full and n_slots == max_seats
    status = "complete" if complete else "limited"

    tables_out: dict[str, pd.DataFrame] = {
        "boards": pd.DataFrame(boards_out),
        "slots": pd.DataFrame(slot_rows),
        "stocks": pd.DataFrame(stocks_out),
        "metrics": pd.DataFrame(metric_rows),
        "exclusions": pd.DataFrame(exclusions),
        "series": pd.DataFrame(series_rows),
        "overlap": pd.DataFrame(overlap_rows),
        "aux": pd.DataFrame(aux_rows),
    }

    from hetu_quant.evidence import make_cards

    tables_out["cards"] = make_cards(tables_out)

    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    result_id = f"res-{stamp}-{uuid.uuid4().hex[:8]}"
    final_dir = output_root / "results" / result_id
    tmp_dir = output_root / "results" / f".tmp-{result_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for name in TABLE_NAMES:
        _finalize_table(name, tables_out[name]).to_parquet(
            tmp_dir / f"{name}.parquet", index=False
        )
    duplicate_stocks = int((code_counts > 1).sum()) if len(code_counts) else 0
    manifest_out = {
        "result_id": result_id,
        "created_at": datetime.now(CN).isoformat(),
        "status": status,
        "definition": ranking_definition_to_dict(definition),
        "parameters": parameters,
        "snapshot_id": data.snapshot_id,
        "benchmark": dict(benchmark_cfg),
        "sources": [
            {"product_id": item["product_id"], "source": item.get("source", {})}
            for item in manifest.get("items", [])
        ],
        "limitations": limitations,
        "tables": list(TABLE_NAMES),
        "counts": {
            "slots": n_slots,
            "distinct_stocks": len(slotted_codes),
            "duplicate_stocks": duplicate_stocks,
        },
    }
    (tmp_dir / "result.json").write_text(
        json.dumps(manifest_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if final_dir.exists():
        shutil.rmtree(tmp_dir)
        raise RuntimeError(f"result already published: {result_id}")
    tmp_dir.rename(final_dir)
    return ResultRef(result_id=result_id, root=output_root, status=status)

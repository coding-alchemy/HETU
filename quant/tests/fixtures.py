"""小型事实构造器：返回事实、不附评分；未知选择报错。

评分与预期答案保留在各测试文件断言侧。`price_series` 复用
tests/product/skill/test_market_series_metrics_tool.py 已核对手算序列的原始事实。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

# v2 主题分类门（category.theme_basis.audited_boards）的合成核验条目：
# THM1–THM6 逐条核验为产业主题（虚构证据文件）。合成题材库名称命中后
# 还须核验表放行，依赖主题进入主题榜的测试经定义参数注入本表（与真实
# 默认协议的 267 条核验表深合并）；测试材料复用方法论见
# docs/engineering/test-fixtures-methodology.md。
SYNTHETIC_THEME_AUDIT = {
    f"THM{i}": {
        "category": "industrial_theme",
        "evidence": f"synthetic-audit/THM{i}.txt",
    }
    for i in range(1, 7)
}


def make_case(case: str, variant: str = "base") -> dict:
    if case == "price_series":
        return _price_series(variant)
    if case == "product_config":
        return _product_config(variant)
    if case == "import_manifest":
        return _import_manifest(variant)
    if case == "product_config_full":
        return _product_config_full(variant)
    if case == "snapshot_csvs":
        return _snapshot_csvs(variant)
    if case == "ranking_universe":
        return _ranking_universe(variant)
    if case == "mini_boards":
        return _mini_boards(variant)
    if case == "board_width":
        return _board_width(variant)
    if case == "ma60_window":
        return _ma60_window(variant)
    raise KeyError(f"unknown case: {case!r}")


_RANK_START = "2021-06-01"
_RANK_CUTOFF = "2024-06-28"
_RANK_ANCHOR_6M = "2023-12-28"
_RANK_ANCHOR_3M = "2024-03-28"
_NORMAL_AMOUNT = "30000000"
_LOW_AMOUNT = "1000000"
_INDEX_AMOUNT = "1000000000"
_TIER_DT = (0.03, 0.02, 0.01, 0.0, -0.01, -0.02)


def annotate_actions_coverage(
    store,
    ref,
    start: str,
    end: str,
    codes: Sequence[str] | None = None,
    only: Mapping[str, Sequence[str]] | None = None,
    query_basis: str | None = None,
) -> None:
    """登记已核验 corporate_actions 覆盖（排名侧按对象+期间消费，R1）。

    import 快照的整表条目不带分区/覆盖元数据，一律按未核验；本 helper
    以 ``code=<代码>`` 分区登记 ``coverage: [start, end]``，与取数侧
    code 分区形状一致。codes 限定登记范围（默认快照日线全部代码）；
    only 为个别代码改登记期间（覆盖不足反例）。未登记的代码保持未核验。
    query_basis 可显式登记合成输入的查询日期口径，不推断历史供应商口径。
    """
    manifest_path = (
        Path(store.root) / "snapshots" / ref.snapshot_id / "manifest.json"
    )
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_entries = [
        entry
        for entry in doc.get("table_files", [])
        if entry.get("table") == "corporate_actions"
    ]
    if not base_entries:
        return
    if codes is None:
        bars = store.read_table(ref, "daily_bars")
        codes = sorted({str(c) for c in bars["code"]})
    coverage = {str(code): [str(start), str(end)] for code in codes}
    for code, span in (only or {}).items():
        coverage[str(code)] = [str(span[0]), str(span[1])]
    for code, span in sorted(coverage.items()):
        for base in base_entries:
            entry = dict(base)
            entry["partition"] = f"code={code}"
            entry["coverage"] = list(span)
            if query_basis is not None:
                entry["query_basis"] = query_basis
            doc["table_files"].append(entry)
    manifest_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _bdates(start: str, end: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, end)]


def _window_path(
    days: list[str],
    r6: float,
    r3: float,
    dip: float | None = None,
) -> list[float]:
    """6m 窗口路径：D0=100，D1=100×(1+r3)，尾部 61 天两段线性到 100×(1+r6)。

    尾部用算术中点分两段，使日收益非恒定（波动非零）；r6 > r3 时全程单调，
    dip 在中段加 V 型回撤（只改回撤维）。事实只含价格，评分在测试侧。
    """
    idx1 = days.index(_RANK_ANCHOR_3M)
    tail0 = len(days) - 61
    base_price = 100.0 * (1 + r3)
    closes: list[float] = []
    for i in range(len(days)):
        if i <= idx1:
            frac = i / idx1
            closes.append(100.0 * (1 + frac * r3))
        elif i < tail0:
            frac = (i - idx1) / (tail0 - idx1)
            value = base_price
            if dip is not None:
                value -= dip * 4.0 * frac * (1 - frac)
            closes.append(value)
        elif i == tail0:
            closes.append(base_price)
    end_price = 100.0 * (1 + r6)
    mid = (base_price + end_price) / 2
    seg_a = [base_price + (mid - base_price) * (k + 1) / 30 for k in range(30)]
    seg_b = [mid + (end_price - mid) * (k + 1) / 30 for k in range(30)]
    return closes + seg_a + seg_b


def _ranking_case(
    days_all: list[str],
    days_win: list[str],
    add_stocks,
    board_rows: list[str],
    member_rows: list[str],
    index_rows: list[str],
) -> dict:
    bar_rows: list[str] = []
    status_rows: list[str] = []

    def add_stock(
        code: str,
        base: float,
        tier: int,
        amount: str = _NORMAL_AMOUNT,
        start: str | None = None,
        st_day: str | None = None,
        miss_day: str | None = None,
        susp_days: tuple[str, ...] = (),
        dip: float | None = None,
        closes: list[float] | None = None,
    ) -> None:
        if closes is None:
            r3 = base + _TIER_DT[tier - 1]
            r6 = 1.5 * r3
            closes = [100.0] * (len(days_all) - len(days_win)) + _window_path(
                days_win, r6, r3, dip=dip
            )
        else:
            assert len(closes) == len(days_all), (
                f"{code}: 显式收盘序列长度 {len(closes)} != 日历长度 {len(days_all)}"
            )
        for day, close in zip(days_all, closes, strict=False):
            if start and day < start:
                continue
            if miss_day and day == miss_day:
                continue
            if day in susp_days:
                continue
            bar_rows.append(f"{day},{code},{close:.6f},1000000,{amount}")
        for day in days_win:
            if start and day < start:
                continue
            st = "True" if st_day and day == st_day else "False"
            susp = "True" if day in susp_days else "False"
            status_rows.append(f"{day},{code},{st},{susp},True")

    add_stocks(add_stock)

    files = {
        "calendar.csv": "date,is_trading_day\n"
        + "\n".join(f"{day},True" for day in days_all),
        "daily_bars.csv": "date,code,close_raw,volume_shares,amount_cny\n"
        + "\n".join(bar_rows),
        "security_status.csv": "date,code,is_st,is_suspended,is_listed\n"
        + "\n".join(status_rows),
        "boards.csv": "board_id,board_kind,name,provider,classification_version\n"
        + "\n".join(board_rows),
        "memberships.csv": (
            "board_id,code,observed_at,available_at,effective_from,effective_to,"
            "membership_kind\n" + "\n".join(member_rows)
        ),
        "index_bars.csv": "code,date,close,amount_cny,return_basis\n"
        + "\n".join(index_rows),
        # 空表 = 已核验无公司行动（覆盖已取齐、该对象无记录即无事件）；
        # 需要"覆盖未取齐"反例的测试自行从 manifest/files 中移除本表。
        "corporate_actions.csv": (
            "code,ex_date,action_type,m,d,terms_raw,available_at\n"
        ),
    }
    items = [
        ("test_calendar", "calendar", "calendar.csv"),
        ("test_daily", "daily_bars", "daily_bars.csv"),
        ("test_status", "security_status", "security_status.csv"),
        ("test_boards", "boards", "boards.csv"),
        ("test_members", "memberships", "memberships.csv"),
        ("test_index", "index_bars", "index_bars.csv"),
        ("test_actions", "corporate_actions", "corporate_actions.csv"),
    ]
    # 主题分类依据材料（category.theme_basis 消费）：由主题板块事实派生的
    # 合成题材库（虚构题材代码，不用真实东财数据）；需要"快照缺表"反例的
    # 测试自行从 manifest/files 中移除本表。
    theme_names = [
        parts[2]
        for line in board_rows
        for parts in [line.split(",")]
        if parts[1] == "theme"
    ]
    if theme_names:
        files["theme_library.csv"] = (
            "theme_code,theme_name,parent_code,parent_name\n"
            + "\n".join(
                f"SYN{i + 1:03d},{name},," for i, name in enumerate(theme_names)
            )
        )
        items.append(("test_themelib", "theme_library", "theme_library.csv"))
    source = {
        "name": "合成两榜材料",
        "fee_class": "free",
        "permission_basis": "合成测试材料，无第三方权利",
        "source_url": "",
        "obtained_at": "2024-06-01T09:00:00+08:00",
    }
    manifest = {
        "mode": "free",
        "items": [
            {
                "product_id": pid,
                "table": table,
                "files": [fname],
                "source": dict(source),
            }
            for pid, table, fname in items
        ],
    }
    # v2 分类门（audited_boards）合成核验条目：THM1–THM6 逐条核验为产业主题
    # （虚构证据文件）。测试定义参数经此覆盖注入，与真实默认协议的核验表
    # 深合并；写盘文件列表不含本条目。
    return {
        "manifest": manifest,
        "files": files,
        "synthetic_theme_audit": SYNTHETIC_THEME_AUDIT,
    }


def _ranking_universe(variant: str) -> dict:
    """两榜合成宇宙：6 行业 × 6 股、6 主题（含重复股、边界并列、被排除者）。

    窗口 [2023-12-28, 2024-06-28]，cutoff 2024-06-28，as_of 2024-07-01T09:00+08:00。
    只含事实：答案（选中的板块、席位、并列处理）全部在各测试断言侧。
    """
    if variant not in (
        "base", "market_down", "coverage79", "coverage80",
        "future_member", "missing_last_day", "short_history",
    ):
        raise KeyError(f"unknown ranking_universe variant: {variant!r}")
    days_all = _bdates(_RANK_START, _RANK_CUTOFF)
    days_win = [d for d in days_all if d >= _RANK_ANCHOR_6M]
    board_rows: list[str] = []
    member_pairs: list[tuple[str, str]] = []
    index_rows: list[str] = []

    ind_codes = [f"6000{i}.SH" for i in range(11, 47)]
    ind_params = [(0.28, 0.15), (0.22, 0.12), (0.16, 0.08),
                  (0.10, 0.05), (0.04, 0.02), (-0.06, -0.03)]

    def add_stocks(add_stock) -> None:
        for k in range(6):
            for j, code in enumerate(ind_codes[6 * k: 6 * k + 6]):
                extra = {}
                if variant == "missing_last_day" and code == "600011.SH":
                    extra = {"miss_day": _RANK_CUTOFF}
                if variant == "short_history" and code == "600012.SH":
                    extra = {"start": _RANK_ANCHOR_6M}
                add_stock(code, 0.0, j + 1, **extra)
        add_stock("600047.SH", 0.0, 1, susp_days=("2024-06-27", _RANK_CUTOFF))
        themes = [
            (0.29, ["600901.SH", "600902.SH", "600903.SH", "600904.SH",
                    "600905.SH", "600906.SH"], [1, 2, 3, 4, 5, 5]),
            (0.24, ["600912.SH", "600913.SH", "600914.SH", "600915.SH",
                    "600916.SH"], [2, 3, 4, 5, 5]),
            (0.19, ["600921.SH", "600922.SH", "600923.SH", "600924.SH",
                    "600925.SH", "600926.SH"], [1, 2, 3, 4, 5, 6]),
            (0.14, ["600931.SH", "600932.SH", "600933.SH", "600934.SH",
                    "600935.SH", "600936.SH"], [1, 2, 3, 4, 5, 6]),
            (0.07, ["600941.SH", "600942.SH", "600943.SH", "600944.SH",
                    "600945.SH", "600946.SH"], [1, 2, 3, 4, 5, 5]),
            (0.07, ["600951.SH", "600952.SH", "600953.SH", "600954.SH",
                    "600955.SH", "600956.SH"], [1, 2, 3, 4, 5, 5]),
        ]
        for base, codes, tiers in themes:
            for code, tier in zip(codes, tiers, strict=False):
                add_stock(code, base, tier)
        # 被排除者：ST、低流动性、缺窗口日、短历史（合格但标记）
        add_stock("600907.SH", 0.0, 1, st_day=_RANK_CUTOFF)
        add_stock("600917.SH", 0.0, 1, amount=_LOW_AMOUNT)
        add_stock("600937.SH", 0.0, 1, miss_day="2024-03-15")
        add_stock("600927.SH", 0.19, 4, start="2023-11-01")
        if variant in ("coverage79", "coverage80"):
            n_extra = 13 if variant == "coverage79" else 10
            for i in range(n_extra):
                add_stock(f"6008{i + 1:02d}.SH", 0.07, i % 6 + 1)

    for k in range(6):
        board_rows.append(f"IND{k + 1},industry,合成行业{k + 1},sw,v1")
        for code in ind_codes[6 * k: 6 * k + 6]:
            member_pairs.append((f"IND{k + 1}", code))
    member_pairs.append(("IND6", "600047.SH"))
    theme_members = {
        "THM1": ["600901.SH", "600902.SH", "600903.SH", "600904.SH",
                 "600905.SH", "600906.SH", "600907.SH"],
        "THM2": ["600901.SH", "600912.SH", "600913.SH", "600914.SH",
                 "600915.SH", "600916.SH", "600917.SH"],
        "THM3": ["600921.SH", "600922.SH", "600923.SH", "600924.SH",
                 "600925.SH", "600926.SH", "600927.SH"],
        "THM4": ["600931.SH", "600932.SH", "600933.SH", "600934.SH",
                 "600935.SH", "600936.SH", "600937.SH"],
        "THM5": ["600941.SH", "600942.SH", "600943.SH", "600944.SH",
                 "600945.SH", "600946.SH"],
        "THM6": ["600951.SH", "600952.SH", "600953.SH", "600954.SH",
                 "600955.SH", "600956.SH"],
    }
    for name in ("THM1", "THM2", "THM3", "THM4", "THM5", "THM6"):
        board_rows.append(f"{name},theme,合成主题{name[-1]},em,v1")
    if variant == "coverage79":
        theme_members["THM6"] += [f"6008{i + 1:02d}.SH" for i in range(13)]
        theme_members["THM6"] += [f"6009{70 + i}.SH" for i in range(5)]
    if variant == "coverage80":
        theme_members["THM6"] += [f"6008{i + 1:02d}.SH" for i in range(10)]
        theme_members["THM6"] += [f"6009{70 + i}.SH" for i in range(4)]
    member_rows = []
    for name, codes in theme_members.items():
        for code in codes:
            eff = "2023-01-01"
            if variant == "future_member" and code == "600936.SH":
                eff = "2024-06-10"
            member_rows.append(
                f"{name},{code},2024-05-01T00:00:00+08:00,2024-05-01T00:00:00+08:00,"
                f"{eff},,current_snapshot"
            )
    for board, code in member_pairs:
        member_rows.append(
            f"{board},{code},2024-05-01T00:00:00+08:00,2024-05-01T00:00:00+08:00,"
            "2023-01-01,,current_snapshot"
        )
    board_rows.append("STY1,style,融资融券标签,em,v1")
    member_pairs.append(("STY1", "600011.SH"))
    member_pairs.append(("STY1", "600012.SH"))

    def add_index(code: str, closes: list[float], basis: str = "price") -> None:
        for day, close in zip(days_all, closes, strict=False):
            index_rows.append(f"{code},{day},{close:.6f},{_INDEX_AMOUNT},{basis}")

    if variant == "market_down":
        bench = [c * 40 for c in _window_path(days_win, -0.10, -0.05)]
        bench = [4000.0] * (len(days_all) - len(days_win)) + bench
    else:
        bench = [4000.0] * len(days_all)
    add_index("000300.SH", bench)
    for k, (r6, r3) in enumerate(ind_params):
        closes = [100.0] * (len(days_all) - len(days_win)) + _window_path(
            days_win, r6, r3
        )
        add_index(f"IND{k + 1}.SH", closes)

    return _ranking_case(
        days_all, days_win, add_stocks, board_rows, member_rows, index_rows
    )


def _mini_boards(variant: str) -> dict:
    """小宇宙：4 行业指数（B 与 B2 完全并列）× 各 5 股，用于手算百分位与贡献。

    窗口即全部历史 [2023-12-28, 2024-06-28]；基准 000300.SH 常数 4000。
    zero_seats 变体：全部股票截止日 ST，构造零席位全排除场景（B4）。
    """
    if variant not in ("base", "drawdown_shock", "mixed_basis", "zero_seats"):
        raise KeyError(f"unknown mini_boards variant: {variant!r}")
    days = _bdates(_RANK_ANCHOR_6M, _RANK_CUTOFF)
    board_rows = [
        "MINIA,industry,迷你行业A,sw,v1",
        "MINIB,industry,迷你行业B,sw,v1",
        "MINIB2,industry,迷你行业B复制,sw,v1",
        "MINIC,industry,迷你行业C,sw,v1",
    ]
    member_rows: list[str] = []
    index_rows: list[str] = []
    ind_params = {
        "MINIA": (0.20, 0.10),
        "MINIB": (0.10, 0.05),
        "MINIB2": (0.10, 0.05),
        "MINIC": (0.0, 0.0),
    }

    def add_stocks(add_stock) -> None:
        for board, codes in (
            ("MINIA", ["600101.SH", "600102.SH", "600103.SH",
                       "600104.SH", "600105.SH"]),
            ("MINIB", ["600111.SH", "600112.SH", "600113.SH",
                       "600114.SH", "600115.SH"]),
            ("MINIB2", ["600121.SH", "600122.SH", "600123.SH",
                        "600124.SH", "600125.SH"]),
            ("MINIC", ["600131.SH", "600132.SH", "600133.SH",
                       "600134.SH", "600135.SH"]),
        ):
            for j, code in enumerate(codes):
                shock = variant == "drawdown_shock" and code == "600101.SH"
                dip = 20.0 if shock else None
                st_day = _RANK_CUTOFF if variant == "zero_seats" else None
                add_stock(code, 0.0, j + 1, dip=dip, st_day=st_day)
                member_rows.append(
                    f"{board},{code},2024-05-01T00:00:00+08:00,2024-05-01T00:00:00+08:00,"
                    "2023-01-01,,current_snapshot"
                )

    for board, (r6, r3) in ind_params.items():
        closes = _window_path(days, r6, r3)
        for day, close in zip(days, closes, strict=False):
            index_rows.append(f"{board}.SH,{day},{close:.6f},{_INDEX_AMOUNT},price")
    basis = "total_return" if variant == "mixed_basis" else "price"
    for day in days:
        index_rows.append(f"000300.SH,{day},4000.000000,{_INDEX_AMOUNT},{basis}")

    return _ranking_case(days, days, add_stocks, board_rows, member_rows, index_rows)


def _board_width(variant: str) -> dict:
    """单一行业 10 成员（6 涨 4 跌）：行业宽度与集中度成员口径手算材料（B3）。

    成员半年收益 r6 = (0.30, 0.24, 0.18, 0.12, 0.06, 0.03,
    -0.06, -0.12, -0.18, -0.24)，单调路径：正收益成员末日收于 60 日均线上方，
    负收益成员在均线下方。独立手算：宽度 = 6/10 = 0.6；
    集中度 = 前 5 正收益和 0.90 / 正收益总和 0.93 ≈ 0.9677419355。
    """
    if variant != "base":
        raise KeyError(f"unknown board_width variant: {variant!r}")
    days_all = _bdates(_RANK_START, _RANK_CUTOFF)
    days_win = [d for d in days_all if d >= _RANK_ANCHOR_6M]
    r6_targets = [0.30, 0.24, 0.18, 0.12, 0.06, 0.03, -0.06, -0.12, -0.18, -0.24]
    board_rows = ["WIND,industry,合成宽度行业,sw,v1"]
    member_rows: list[str] = []
    index_rows: list[str] = []

    def add_stocks(add_stock) -> None:
        for i, r6 in enumerate(r6_targets):
            code = f"6001{i + 10}.SH"
            base = r6 / 1.5 - 0.03
            add_stock(code, base, 1)
            member_rows.append(
                f"WIND,{code},2024-05-01T00:00:00+08:00,2024-05-01T00:00:00+08:00,"
                "2023-01-01,,current_snapshot"
            )

    for day in days_all:
        index_rows.append(f"000300.SH,{day},4000.000000,{_INDEX_AMOUNT},price")
        index_rows.append(f"WIND.SH,{day},100.000000,{_INDEX_AMOUNT},price")

    return _ranking_case(
        days_all, days_win, add_stocks, board_rows, member_rows, index_rows
    )


def _ma60_window(variant: str) -> dict:
    """MA60 窗口口径手算材料（R2）：10 成员中 5 个两种窗口口径都在均线上方、
    4 个都在下方、1 个"翻转"成员——末日收盘低于 61 日窗口均值但高于严格
    60 日窗口均值。61 日口径宽度 0.5，正确 60 日口径宽度 0.6。
    """
    if variant != "base":
        raise KeyError(f"unknown ma60_window variant: {variant!r}")
    days_all = _bdates(_RANK_START, _RANK_CUTOFF)
    days_win = [d for d in days_all if d >= _RANK_ANCHOR_6M]
    board_rows = ["WIND,industry,合成MA60窗口行业,sw,v1"]
    member_rows: list[str] = []
    index_rows: list[str] = []

    def ma60_path(kind: str) -> list[float]:
        pre_tail = [100.0] * (len(days_win) - 61)
        if kind == "rising":
            # 尾窗 100→105 单调上行：末日收盘高于两种口径均值
            seg = [100.0 + 5.0 * k / 60 for k in range(61)]
        elif kind == "falling":
            # 尾窗 105→100 单调下行：末日收盘低于两种口径均值
            seg = [105.0 - 5.0 * k / 60 for k in range(61)]
        else:  # flip：尾窗首日 150 只进 61 日窗口，抬高 61 日口径均值
            seg = [150.0] + [100.0] * 59 + [100.5]
        return [100.0] * (len(days_all) - len(days_win)) + pre_tail + seg

    def add_stocks(add_stock) -> None:
        kinds = ["rising"] * 5 + ["falling"] * 4 + ["flip"]
        for i, kind in enumerate(kinds):
            code = f"6001{i + 20}.SH"
            add_stock(code, 0.0, 1, closes=ma60_path(kind))
            member_rows.append(
                f"WIND,{code},2024-05-01T00:00:00+08:00,2024-05-01T00:00:00+08:00,"
                "2023-01-01,,current_snapshot"
            )

    for day in days_all:
        index_rows.append(f"000300.SH,{day},4000.000000,{_INDEX_AMOUNT},price")
        index_rows.append(f"WIND.SH,{day},100.000000,{_INDEX_AMOUNT},price")

    return _ranking_case(
        days_all, days_win, add_stocks, board_rows, member_rows, index_rows
    )


def _price_series(variant: str) -> dict:
    if variant == "base":
        return {
            "code": "600519.SH",
            "dates": ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06"],
            "closes": [100.0, 120.0, 60.0, 90.0],
        }
    raise KeyError(f"unknown price_series variant: {variant!r}")


def _product(name: str, **overrides: object) -> dict:
    product = {
        "provider": "fake",
        "fee_class": "free",
        "permission": {
            "status": "verified_free",
            "basis": "测试合成产品，无真实来源",
            "source_url": "",
        },
        "kind": "market",
        "requires_codes": True,
        "tables": ["daily_bars"],
        "provider_timezone": "Asia/Shanghai",
        "notes": "",
    }
    product.update(overrides)
    return product


def _product_config(variant: str) -> dict:
    products = {
        "test_prices": _product("test_prices"),
        "test_fin": _product(
            "test_fin",
            provider="local_file",
            kind="fundamental",
            tables=["financial_reports"],
        ),
    }
    if variant == "base":
        pass
    elif variant == "with_unverified":
        products["test_unverified"] = _product(
            "test_unverified",
            permission={"status": "unverified", "basis": "权限未核", "source_url": ""},
        )
    elif variant == "with_paid":
        products["test_paid"] = _product("test_paid", fee_class="paid")
    else:
        raise KeyError(f"unknown product_config variant: {variant!r}")
    return {"schema_version": 1, "products": products}


def _import_manifest(variant: str) -> dict:
    source = {
        "name": "手工编制合成财务样例",
        "fee_class": "free",
        "permission_basis": "合成测试材料，无第三方权利",
        "source_url": "",
        "obtained_at": "2024-07-01T09:00:00+08:00",
    }
    if variant == "paid_disguised":
        source["fee_class"] = "paid"
    elif variant == "unknown_fee":
        del source["fee_class"]
    elif variant == "missing_basis":
        source["permission_basis"] = ""
    elif variant != "base":
        raise KeyError(f"unknown import_manifest variant: {variant!r}")
    return {
        "manifest": {
            "mode": "free",
            "items": [
                {
                    "product_id": "test_fin",
                    "table": "financial_reports",
                    "files": ["financial_reports.csv"],
                    "source": source,
                }
            ],
        },
        "files": {
            "financial_reports.csv": (
                "code,report_period,ann_date,net_profit_ex\n"
                "000001.SZ,2024Q1,2024-04-20,8\n"
            )
        },
    }


def _local_product(name: str, kind: str, tables: list[str], markets: list[str]) -> dict:
    return {
        "provider": "local_file",
        "fee_class": "free",
        "permission": {
            "status": "verified_free",
            "basis": "合成测试材料，无第三方权利",
            "source_url": "",
        },
        "kind": kind,
        "requires_codes": kind not in ("catalog", "calendar"),
        "tables": tables,
        "markets": markets,
        "provider_timezone": "Asia/Shanghai",
        "notes": "",
    }


def _product_config_full(variant: str) -> dict:
    if variant != "base":
        raise KeyError(f"unknown product_config_full variant: {variant!r}")
    return {
        "schema_version": 1,
        "products": {
            "test_calendar": _local_product("test_calendar", "calendar", ["calendar"], ["SH", "SZ"]),
            "test_daily": _local_product("test_daily", "market", ["daily_bars"], ["SH", "SZ"]),
            "test_status": _local_product(
                "test_status", "market", ["security_status"], ["SH", "SZ"]
            ),
            "test_boards": _local_product("test_boards", "catalog", ["boards"], ["SH", "SZ"]),
            "test_members": _local_product(
                "test_members", "membership", ["memberships"], ["SH", "SZ"]
            ),
            "test_index": _local_product("test_index", "index", ["index_bars"], ["SH", "SZ"]),
            "test_actions": _local_product(
                "test_actions", "event", ["corporate_actions"], ["SH", "SZ"]
            ),
            "test_themelib": _local_product(
                "test_themelib", "catalog", ["theme_library"], ["SH", "SZ"]
            ),
            "test_fin": _local_product(
                "test_fin", "fundamental", ["financial_reports"], ["SH", "SZ"]
            ),
            "test_mcap": _local_product("test_mcap", "fundamental", ["market_cap"], ["SH", "SZ"]),
        },
    }


def _snapshot_csvs(variant: str) -> dict:
    """合成快照事实：2024-06 的 20 个工作日；AAA 完整，BBB 06-10 明确停牌，CCC 缺末日。

    窗口 [2024-06-03, 2024-06-28]，as_of 2024-07-01T09:00+08:00。
    只含事实，不含资格结论；未知 variant 报错。
    """
    days = [
        "2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06", "2024-06-07",
        "2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14",
        "2024-06-17", "2024-06-18", "2024-06-19", "2024-06-20", "2024-06-21",
        "2024-06-24", "2024-06-25", "2024-06-26", "2024-06-27", "2024-06-28",
    ]
    if variant not in (
        "base", "member_after_as_of", "missing_latest_day", "late_revision", "bj_uncovered",
    ):
        raise KeyError(f"unknown snapshot_csvs variant: {variant!r}")
    codes = ("AAA.SH", "BBB.SZ", "CCC.SZ")
    bar_rows = []
    status_rows = []
    for code in codes:
        for i, day in enumerate(days):
            if code == "BBB.SZ" and day == "2024-06-10":
                status_rows.append(f"{day},{code},False,True,True")
                continue
            close = 10.0 + i + (0 if code == "AAA.SH" else 1)
            bar_rows.append(f"{day},{code},{close},1000,20000")
            status_rows.append(f"{day},{code},False,False,True")
    if variant == "member_after_as_of":
        member_available = "2024-07-05T00:00:00+08:00"
    else:
        member_available = "2024-06-01T00:00:00+08:00"
    member_rows = [
        f"IND1,{code},2024-06-01T00:00:00+08:00,{member_available},,,current_snapshot"
        for code in codes
    ]
    if variant == "missing_latest_day":
        bar_rows = [row for row in bar_rows if not row.startswith("2024-06-28,CCC.SZ")]
        status_rows = [row for row in status_rows if not row.startswith("2024-06-28,CCC.SZ")]
    fin_rows = ["AAA.SH,2024Q1,2024-04-20,2024-04-20T00:00:00+08:00,8"]
    if variant == "late_revision":
        fin_rows.append("AAA.SH,2024Q1,2024-04-20,2024-07-05T00:00:00+08:00,80")
    if variant == "bj_uncovered":
        bar_rows.append("2024-06-28,830001.BJ,10.0,1000,20000")
        status_rows.append("2024-06-28,830001.BJ,False,False,True")
    files = {
        "calendar.csv": "date,is_trading_day\n" + "\n".join(
            f"{day},True" for day in days
        ),
        "daily_bars.csv": "date,code,close_raw,volume_shares,amount_cny\n" + "\n".join(bar_rows),
        "security_status.csv": "date,code,is_st,is_suspended,is_listed\n" + "\n".join(status_rows),
        "boards.csv": "board_id,board_kind,name,provider,classification_version\nIND1,industry,测试行业,test,v1",
        "memberships.csv": (
            "board_id,code,observed_at,available_at,effective_from,effective_to,membership_kind\n"
            + "\n".join(member_rows)
        ),
        "index_bars.csv": "code,date,close,amount_cny,return_basis\n"
        + "\n".join(f"IDX,{day},{100 + i},1000000,price" for i, day in enumerate(days)),
        "financial_reports.csv": "code,report_period,ann_date,available_at,net_profit_ex\n"
        + "\n".join(fin_rows),
    }
    items = [
        ("test_calendar", "calendar", "calendar.csv"),
        ("test_daily", "daily_bars", "daily_bars.csv"),
        ("test_status", "security_status", "security_status.csv"),
        ("test_boards", "boards", "boards.csv"),
        ("test_members", "memberships", "memberships.csv"),
        ("test_index", "index_bars", "index_bars.csv"),
        ("test_fin", "financial_reports", "financial_reports.csv"),
    ]
    source = {
        "name": "合成快照材料",
        "fee_class": "free",
        "permission_basis": "合成测试材料，无第三方权利",
        "source_url": "",
        "obtained_at": "2024-06-30T09:00:00+08:00",
    }
    manifest = {
        "mode": "free",
        "items": [
            {"product_id": pid, "table": table, "files": [fname], "source": dict(source)}
            for pid, table, fname in items
        ],
    }
    return {"manifest": manifest, "files": files}

"""二期 01.2 正式研究取数：小样本验证与全量初始化采集。

数据来源与口径（呈报见 .hetu/quant/qualification/phase2/data-acquisition-plan-20261017.md）：
- 窗口 2016-04-29..2026-10-13；宇宙为沪深 A 股普通股（含窗口内退市，北交所不在首批）。
- BaoStock：交易日历、证券目录、daily/status 各自 k_data 查询（旧脚本会额外查询状态；
  本次已暂停，现有日线 raw 可离线派生）；corporate_actions（yearType=operate，逐年）。
- 东财股本结构（akshare stock_zh_a_gbjg_em，RPT_F10_EH_EQUITY）× BaoStock
  收盘价本地重建 market_cap；股本结构无公告日字段，available_at 保守记重建
  取得时间（§3b 拟用公告日的口径因字段缺失待评审，见小样本验证报告）。
- historical_securities 由 cn_a_basic 目录派生（§3a：目录取得时间为 published_at（事件日不等于公开日），
  目录 raw 响应为原件）；knowledge_evidence 随导入逐记录登记原件 sha256。
  全量 market_cap 的 knowledge 登记粒度为 (code, 股本版本)，非逐日记录，
  理由与待确认项见 data-sample-verify-20261017.md。

礼貌与边界：串行执行；provider.fetch 之间与 operate 逐年查询之间固定休眠；
不绕过 BaoStock／东财限流；失败保留在台账与 state 中，不静默跳过；不付费。

用法（小样本；2026-10-17 已授权，3 只代码、11 次 provider.fetch + 3 次 gbjg 请求）：
  quant/.venv/bin/python quant/probes/phase2_acquire.py sample \
      --data-root .hetu/quant \
      --out .hetu/quant/qualification/phase2/data-sample-20261017

用法（全量；授权另文登记后执行，分表、断点续采、失败保留）：
  quant/.venv/bin/python quant/probes/phase2_acquire.py full \
      --data-root .hetu/quant \
      --state .hetu/quant/qualification/phase2/acquire-state.json \
      [--table calendar|securities|daily_bars|security_status|corporate_actions|market_cap] \
      [--batch-size 200] [--delay 3.0] [--limit N] [--codes-file codes.txt]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hetu_quant.backtest_data import select_known_versions  # noqa: E402
from hetu_quant.data import DataStore  # noqa: E402
from hetu_quant.models import DataRequest, SnapshotRef  # noqa: E402
from hetu_quant.providers.baostock import BaoStockProvider  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")

WINDOW_START = date(2016, 4, 29)
WINDOW_END = date(2026, 10, 13)
SAMPLE_CODES = ("600519.SH", "600005.SH", "688001.SH")  # 在册主板 / 窗口内退市 / 科创板

TABLE_PRODUCTS = {
    "calendar": "cn_trade_calendar",
    "securities": "cn_a_basic",
    "daily_bars": "cn_a_daily",
    "security_status": "cn_a_status",
    "corporate_actions": "cn_a_actions",
}
ALL_TABLES = (*TABLE_PRODUCTS, "market_cap")

UNIVERSE_RULE = "security_type=stock 且 (status=listed 或 out_date>=2016-04-29)，沪深市场"


# ---------------------------------------------------------------------------
# 礼貌取数包装


class PoliteBaoStockProvider(BaoStockProvider):
    """BaoStock 适配器的礼貌变体：fetch 之间与 operate 逐年查询之间休眠。"""

    def __init__(self, delay: float = 3.0, year_delay: float = 1.5) -> None:
        super().__init__()
        self._delay = delay
        self._year_delay = year_delay

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        time.sleep(self._delay)
        if product_id != "cn_a_actions":
            return super().fetch(product_id, codes, start, end)
        # 与父类相同的 operate 逐年查询，但逐年之间保持礼貌间隔；
        # 全空响应保留查询列名（父类直接返回无列空表，raw 证据退化）
        bs = self._session()
        frames = []
        columns: list[str] = []
        for code in codes:
            bs_code = (
                code.lower()
                .replace(".SH", ".sh")
                .replace(".SZ", ".sz")
                .replace(".BJ", ".bj")
                if "." in code
                else code
            )
            for year in range(start.year, end.year + 1):
                if frames or year != start.year:
                    time.sleep(self._year_delay)
                frame = self._collect(
                    bs.query_dividend_data(code=bs_code, year=str(year), yearType="operate")
                )
                if not columns and len(frame.columns):
                    columns = list(frame.columns)
                frames.append(frame)
        frames = [frame for frame in frames if len(frame)]
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
        raw.attrs["query_evidence"] = [
            {
                "code": code,
                "basis": "ex_date",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "method": "query_dividend_data",
                "yearType": "operate",
            }
            for code in codes
        ]
        return raw


class CountingProvider:
    """fetch 计数台账：每次调用（含失败）记一条，供授权上限与报告核对。"""

    def __init__(self, inner: object, ledger: list[dict[str, object]]) -> None:
        self._inner = inner
        self._ledger = ledger
        self.name = getattr(inner, "name", type(inner).__name__)

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        entry: dict[str, object] = {
            "provider": self.name,
            "product_id": product_id,
            "codes": list(codes),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "seq": len(self._ledger) + 1,
        }
        self._ledger.append(entry)
        try:
            frame = self._inner.fetch(product_id, codes, start, end)  # type: ignore[attr-defined]
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        entry["rows"] = int(len(frame))
        return frame

    def __getattr__(self, item: str) -> object:
        return getattr(self._inner, item)


# ---------------------------------------------------------------------------
# 工具


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def board_of(code: str) -> str:
    num = code.split(".", 1)[0]
    if num.startswith(("688", "689")):
        return "star"
    if num.startswith(("300", "301")):
        return "chinext"
    return "main"


def fetch_gbjg(code: str, delay: float) -> tuple[list[dict[str, object]], str | None]:
    """东财股本结构（akshare stock_zh_a_gbjg_em）；返回 (记录列表, 错误)。"""
    time.sleep(delay)
    import akshare as ak

    try:
        frame = ak.stock_zh_a_gbjg_em(symbol=code)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    # to_json 统一处理 date 对象与 NaN（NaN→null），保证证据 JSON 可序列化
    return json.loads(frame.to_json(orient="records", force_ascii=False)), None


def shares_steps(gbjg_rows: list[dict[str, object]]) -> list[tuple[str, float]]:
    """gbjg 记录 → 升序 (变更日, 总股本) 阶梯；总股本强制数值化，非法值跳过。"""
    steps: list[tuple[str, float]] = []
    for row in gbjg_rows:
        raw_day = row.get("变更日期")
        if isinstance(raw_day, (int, float)):
            # akshare to_json 将 datetime64 序列化为毫秒 epoch
            day = pd.to_datetime(raw_day, unit="ms").strftime("%Y-%m-%d")
        else:
            day = str(raw_day or "")[:10]
        shares = pd.to_numeric(pd.Series([row.get("总股本")]), errors="coerce").iloc[0]
        if len(day) == 10 and day[:4].isdigit() and not pd.isna(shares) and float(shares) > 0:
            steps.append((day, float(shares)))
    return sorted(steps)


def reconstruct_market_cap(
    code: str,
    closes: pd.DataFrame,
    steps: list[tuple[str, float]],
    obtained_at: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """总股本阶梯 × 收盘价重建市值；closes 需含 date/close_raw 列。"""
    stats = {"rows": 0, "no_shares_days": 0}
    out: list[dict[str, object]] = []
    if not steps:
        return out, stats
    for day, price in zip(closes["date"].astype(str), closes["close_raw"], strict=False):
        known = [step for step in steps if step[0] <= day]
        if not known:
            stats["no_shares_days"] += 1
            continue
        shares_from, shares = known[-1]
        if pd.isna(price) or shares <= 0:
            continue
        out.append(
            {
                "code": code,
                "date": day,
                "market_cap_cny": round(float(price) * shares, 2),
                "available_at": obtained_at,
                "_shares": shares,
                "_shares_from": shares_from,
            }
        )
    stats["rows"] = len(out)
    return out, stats


def derive_universe(securities: pd.DataFrame) -> list[str]:
    """正式协议宇宙：沪深 stock 类型，在册或窗口内退市（北交所不在首批）。"""
    frame = securities[securities["security_type"].astype(str) == "stock"]
    frame = frame[frame["market"].astype(str).isin(("SH", "SZ"))]
    out = frame["out_date"].astype(str)
    keep = (frame["status"].astype(str) == "listed") | (out >= WINDOW_START.isoformat())
    return sorted(frame.loc[keep, "code"].astype(str))


def latest_snapshot_with(store: DataStore, table: str) -> str | None:
    """含指定表的最新已发布快照 id（按 created_at）。"""
    best: tuple[str, str] | None = None
    for snapshot_id, manifest in store._published_manifests():  # noqa: SLF001
        if any(entry.get("table") == table for entry in manifest.get("table_files", [])):
            created = str(manifest.get("created_at", ""))
            if best is None or created > best[0]:
                best = (created, snapshot_id)
    return best[1] if best else None


def closes_for_codes(store: DataStore, codes: list[str]) -> pd.DataFrame:
    """从含 daily_bars 的最新快照按代码取收盘价（duckdb 直读分区）。"""
    import duckdb

    snapshot = latest_snapshot_with(store, "daily_bars")
    if snapshot is None:
        raise SystemExit("无含 daily_bars 表的快照，无法重建市值")
    pattern = str(store.root / "snapshots" / snapshot / "tables" / "daily_bars" / "*.parquet")
    codes_sql = ",".join(f"'{code}'" for code in codes)
    return duckdb.query(
        f"SELECT code, date, close_raw FROM read_parquet('{pattern}', union_by_name=true) "
        f"WHERE code IN ({codes_sql})"
    ).to_df()


# ---------------------------------------------------------------------------
# 小样本（已授权：3 只代码、11 次 provider.fetch + 3 次 gbjg 上游请求）


def run_sample(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.data_root)
    out = Path(args.out)
    report_path = out / "acquisition-report.json"
    if report_path.exists():
        raise SystemExit(
            f"小样本既有额度不重置：{report_path}；调试重跑同样计数，须先呈报增量并另获授权"
        )
    out.mkdir(parents=True, exist_ok=True)
    evidence_dir = root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    codes = tuple(args.codes or SAMPLE_CODES)
    ledger: list[dict[str, object]] = []
    store = DataStore(
        root,
        providers={"baostock": CountingProvider(PoliteBaoStockProvider(args.delay, args.year_delay), ledger)},
    )
    obtained_at = datetime.now(CN).isoformat()
    report: dict[str, object] = {
        "mode": "sample",
        "codes": list(codes),
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "started_at": obtained_at,
    }

    # 1) BaoStock 五产品全窗口 prepare（增量合并既有分区，复用已核验旧段）
    request = DataRequest(
        product_ids=(
            "cn_trade_calendar",
            "cn_a_basic",
            "cn_a_daily",
            "cn_a_status",
            "cn_a_actions",
        ),
        codes=codes,
        start=WINDOW_START,
        end=WINDOW_END,
        as_of=datetime.now(CN),
        purpose="backtest_low_volatility",
        mode="free",
    )
    ref = store.prepare(request)
    manifest = store.load_manifest(ref)
    report["prepare_snapshot_id"] = ref.snapshot_id
    report["prepare_failures"] = manifest.get("failures", [])
    report["prepare_gaps"] = manifest.get("gaps", {})

    # 2) 逐码读回核验
    bars = store.read_table(ref, "daily_bars")
    status = store.read_table(ref, "security_status")
    actions = store.read_table(ref, "corporate_actions")
    per_code: dict[str, dict[str, object]] = {}
    for code in codes:
        own_bars = bars[bars["code"].astype(str) == code]
        own_status = status[status["code"].astype(str) == code]
        own_actions = actions[actions["code"].astype(str) == code]
        per_code[code] = {
            "daily_bars_rows": int(len(own_bars)),
            "daily_bars_range": (
                [str(own_bars["date"].astype(str).min()), str(own_bars["date"].astype(str).max())]
                if len(own_bars)
                else None
            ),
            "security_status_rows": int(len(own_status)),
            "corporate_actions_rows": int(len(own_actions)),
            "corporate_actions_ex_dates": (
                sorted(set(own_actions["ex_date"].astype(str))) if len(own_actions) else []
            ),
        }
    calendar = store.read_table(ref, "calendar")
    trading = calendar[calendar["is_trading_day"].astype(str).isin(("True", "1", "true"))]
    report["calendar"] = {
        "rows": int(len(calendar)),
        "range": [str(calendar["date"].astype(str).min()), str(calendar["date"].astype(str).max())],
        "trading_days": int(len(trading)),
    }
    securities = store.read_table(ref, "securities")
    report["securities_rows"] = int(len(securities))
    report["per_code"] = per_code

    # 3) 东财股本结构（每码 1 次上游请求，礼貌间隔）+ 市值重建
    gbjg_info: dict[str, dict[str, object]] = {}
    cap_rows: list[dict[str, object]] = []
    mcap_stats: dict[str, dict[str, int]] = {}
    gbjg_sha: dict[str, str] = {}
    for code in codes:
        records, error = fetch_gbjg(code, args.gbjg_delay)
        info = {"rows": len(records), "error": error, "first_change": None, "last_change": None}
        steps = shares_steps(records)
        if steps:
            info["first_change"] = steps[0][0]
            info["last_change"] = steps[-1][0]
        gbjg_info[code] = info
        evidence_name = f"gbjg-{code.replace('.', '_')}-20261017.json"
        evidence_path = evidence_dir / evidence_name
        _write_json(
            evidence_path,
            {
                "code": code,
                "endpoint": "akshare stock_zh_a_gbjg_em (datacenter RPT_F10_EH_EQUITY)",
                "fetched_at": datetime.now(CN).isoformat(),
                "rows": records,
            },
        )
        # 原件双留存：evidence/（机制消费）与 out/（报告留证）
        shutil.copyfile(evidence_path, out / evidence_name)
        gbjg_sha[code] = _sha256(evidence_path)
        own = bars[bars["code"].astype(str) == code][["date", "close_raw"]]
        rows, stats = reconstruct_market_cap(code, own, shares_steps(records), obtained_at)
        mcap_stats[code] = stats
        cap_rows.extend(rows)
    report["gbjg"] = gbjg_info
    report["market_cap_stats"] = mcap_stats

    # 4) §3a：historical_securities 派生 + 目录原件留证 + knowledge_evidence 登记。
    # 生产消费者按 (code, evidence_ref) 合并版本与证据（backtest_data.py），同一
    # 原件对应多版本会产生交叉组合；故每个版本登记一份从目录 raw 响应提取的
    # 版本级原件（JSON），整批目录 raw 响应另存为批级原件。此偏离 §3a 字面
    # （“目录 raw 响应为原件”）已记录在小样本验证报告，供评审确认。
    # 目录原件定位：本快照 raw 缺省时沿 inherited_from 链回溯（增量合并快照
    # 可能只继承不复制 raw）。
    catalog_file = None
    seen: set[str] = set()
    probe_ref: str | None = ref.snapshot_id
    while probe_ref and probe_ref not in seen:
        seen.add(probe_ref)
        probe_manifest = store.load_manifest(SnapshotRef(snapshot_id=probe_ref, root=root))
        for item in probe_manifest.get("items", []):
            if item.get("product_id") == "cn_a_basic" and item.get("raw_files"):
                catalog_file = root / "snapshots" / probe_ref / str(item["raw_files"][0]["path"])
                break
        if catalog_file is not None:
            break
        probe_ref = next(
            (
                str(item.get("inherited_from"))
                for item in probe_manifest.get("items", [])
                if item.get("product_id") == "cn_a_basic" and item.get("inherited_from")
            ),
            None,
        )
    if catalog_file is None or not catalog_file.is_file():
        raise SystemExit(f"无法在任何快照中找到 cn_a_basic 目录 raw 原件（起点 {ref.snapshot_id}）")
    catalog_evidence_name = "cn-a-basic-catalog-20261017.parquet"
    shutil.copyfile(catalog_file, evidence_dir / catalog_evidence_name)
    shutil.copyfile(catalog_file, out / catalog_evidence_name)
    catalog_sha = _sha256(evidence_dir / catalog_evidence_name)

    hist_rows: list[dict[str, object]] = []
    knowledge_rows: list[dict[str, object]] = []
    sec_index = securities.set_index(securities["code"].astype(str))
    for code in codes:
        if code not in sec_index.index:
            raise SystemExit(f"刷新目录中无 {code}，无法按 §3a 派生 historical_securities")
        row = sec_index.loc[code]
        ipo = str(row["ipo_date"])[:10]
        raw_out = row["out_date"]
        out_date = "" if pd.isna(raw_out) else str(raw_out)[:10]
        market = str(row["market"])
        versions = [("orig", "", obtained_at, "True", "")]
        if out_date and out_date >= WINDOW_START.isoformat():
            versions.append((f"delist-{out_date}", "orig", obtained_at, "False", out_date))
        for revision, supersedes, published, listed, valid_to in versions:
            # 版本级提取原件：忠实摘录目录 raw 响应该代码的目录行
            evidence_name = f"sec-{code.replace('.', '_')}-{revision}-20261017.json"
            _write_json(
                evidence_dir / evidence_name,
                {
                    "code": code,
                    "revision": revision,
                    "extracted_from": catalog_evidence_name,
                    "source_raw_sha256": catalog_sha,
                    "ipo_date": ipo,
                    "out_date": out_date,
                    "status_in_catalog": str(row["status"]),
                },
            )
            shutil.copyfile(evidence_dir / evidence_name, out / evidence_name)
            hist_rows.append(
                {
                    "code": code,
                    "valid_from": ipo,
                    "valid_to": valid_to,
                    "listed": listed,
                    "market": market,
                    "board": board_of(code),
                    "available_at": published,
                    "evidence_ref": evidence_name,
                    "revision_id": revision,
                    "supersedes": supersedes,
                    "published_at": published,
                }
            )
            knowledge_rows.append(
                {
                    "table_name": "historical_securities",
                    "record_key": code,
                    "revision_id": revision,
                    "supersedes": supersedes,
                    "published_at": published,
                    "evidence_ref": evidence_name,
                    "sha256": _sha256(evidence_dir / evidence_name),
                    "verified": "True",
                }
            )
    # market_cap 逐记录登记（§3b：gbjg 无公告日字段，published_at 保守记取得时间）
    for cap in cap_rows:
        knowledge_rows.append(
            {
                "table_name": "market_cap",
                "record_key": f"{cap['code']}|{cap['date']}",
                "revision_id": f"gbjg-{cap['_shares_from']}",
                "supersedes": "",
                "published_at": cap["available_at"],
                "evidence_ref": f"gbjg-{cap['code'].replace('.', '_')}-20261017.json",
                "sha256": gbjg_sha[cap["code"]],
                "verified": "True",
            }
        )

    cap_csv = out / "market-cap-sample.csv"
    with cap_csv.open("w", encoding="utf-8") as handle:
        handle.write("code,date,market_cap_cny,available_at\n")
        for cap in cap_rows:
            handle.write(f"{cap['code']},{cap['date']},{cap['market_cap_cny']},{cap['available_at']}\n")
    hist_csv = out / "historical-securities-sample.csv"
    with hist_csv.open("w", encoding="utf-8") as handle:
        handle.write("code,valid_from,valid_to,listed,market,board,available_at,evidence_ref\n")
        for row in hist_rows:
            handle.write(
                f"{row['code']},{row['valid_from']},{row['valid_to']},{row['listed']},"
                f"{row['market']},{row['board']},{row['available_at']},{row['evidence_ref']}\n"
            )
    knowledge_csv = out / "knowledge-evidence-sample.csv"
    with knowledge_csv.open("w", encoding="utf-8") as handle:
        handle.write("table_name,record_key,revision_id,supersedes,published_at,evidence_ref,sha256,verified\n")
        for row in knowledge_rows:
            handle.write(
                f"{row['table_name']},{row['record_key']},{row['revision_id']},{row['supersedes']},"
                f"{row['published_at']},{row['evidence_ref']},{row['sha256']},{row['verified']}\n"
            )

    manifest_path = out / "import-manifest.json"
    _write_json(
        manifest_path,
        {
            "mode": "free",
            "items": [
                {
                    "product_id": "cn_a_market_cap",
                    "table": "market_cap",
                    "files": [cap_csv.name],
                    "source": {
                        "name": "eastmoney-gbjg-close-reconstruction-20261017",
                        "fee_class": "free",
                        "permission_basis": (
                            "东财股本结构接口（datacenter-web RPT_F10_EH_EQUITY，公开免费，"
                            "akshare stock_zh_a_gbjg_em）总股本阶梯 × BaoStock 收盘价重建；单位元；"
                            "§3b 拟用公告日，但 gbjg 记录无公告日字段，available_at 保守记本次重建"
                            "取得时间，历史信号可得性未证明（待评审）；原件存 evidence/ 与 out/"
                        ),
                        "source_url": "https://datacenter.eastmoney.com",
                        "obtained_at": obtained_at,
                    },
                },
                {
                    "product_id": "cn_a_hist_securities",
                    "table": "historical_securities",
                    "files": [hist_csv.name],
                    "source": {
                        "name": "cn-a-basic-catalog-derived-20261017",
                        "fee_class": "free",
                        "permission_basis": (
                            "由 BaoStock cn_a_basic 目录（ipo_date/out_date/status）派生；"
                            "§3a：上市/退市事件为公开事实，published_at 取事件日，目录 raw 响应"
                            "（parquet）为原件存 evidence/；若评审认为不足再补交易所公告原件"
                        ),
                        "source_url": "http://www.baostock.com",
                        "obtained_at": obtained_at,
                    },
                },
                {
                    "product_id": "cn_a_knowledge",
                    "table": "knowledge_evidence",
                    "files": [knowledge_csv.name],
                    "source": {
                        "name": "phase2-sample-knowledge-registration-20261017",
                        "fee_class": "free",
                        "permission_basis": (
                            "小样本 knowledge_evidence 登记：historical_securities 逐版本"
                            "（§3a 事件日）与 market_cap 逐记录（§3b 拟用公告日、字段缺失保守"
                            "记取得时间）登记 published_at 与原件 sha256；原件存 evidence/"
                        ),
                        "source_url": "",
                        "obtained_at": obtained_at,
                    },
                },
            ],
        },
    )
    imp_ref = store.import_files(manifest_path, (cap_csv, hist_csv, knowledge_csv))
    report["import_snapshot_id"] = imp_ref.snapshot_id

    # 5) 端到端核验：生产写入 → 选择器消费（与 backtest_data 相同的 (code, evidence_ref) 合并）
    knowledge_back = store.read_table(imp_ref, "knowledge_evidence")
    hist_back = store.read_table(imp_ref, "historical_securities")
    check_at = datetime(2017, 1, 31, 21, 0, tzinfo=CN)
    evidence_frame = knowledge_back[
        ["record_key", "evidence_ref", "sha256", "revision_id", "supersedes", "published_at", "verified"]
    ].rename(columns={"record_key": "code"})
    merged = hist_back.merge(evidence_frame, how="inner", on=["code", "evidence_ref"])
    chosen = select_known_versions(merged, check_at, keys=("code",))
    known = set(chosen["code"].astype(str))
    sha_cache: dict[str, str] = {}

    def _cached_sha(ref: str) -> str:
        if ref not in sha_cache:
            sha_cache[ref] = _sha256(evidence_dir / ref)
        return sha_cache[ref]

    report["selector_check"] = {
        "at": check_at.isoformat(),
        "known_codes": sorted(known),
        "delisted_600005_known_before_out": "600005.SH" in known,
        "star_688001_not_yet_known": "688001.SH" not in known,
        "merged_version_rows": int(len(merged)),
        "knowledge_rows": int(len(knowledge_back)),
        "hist_rows": int(len(hist_back)),
        "market_cap_rows": int(len(store.read_table(imp_ref, "market_cap"))),
        "evidence_files_verified": all(
            (evidence_dir / str(row["evidence_ref"])).is_file()
            and _cached_sha(str(row["evidence_ref"])) == str(row["sha256"])
            for _, row in knowledge_back.iterrows()
        ),
    }
    report["fetch_ledger"] = ledger
    report["fetch_count_provider"] = len(ledger)
    report["fetch_count_upstream_estimate"] = {
        "baostock_api": sum(
            (int(e["end"][:4]) - int(e["start"][:4]) + 1) * len(e["codes"])
            if e["product_id"] == "cn_a_actions" else max(1, len(e["codes"]))
            for e in ledger
        ),
        "eastmoney_gbjg": len(codes),
    }
    report["finished_at"] = datetime.now(CN).isoformat()
    _write_json(report_path, report)
    return report


# ---------------------------------------------------------------------------
# 全量初始化（授权另文登记后执行；market_cap 经 gbjg 重建后分块导入）


def run_full(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.data_root)
    state_path = Path(args.state)
    state: dict[str, object] = (
        json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    )
    completed: dict[str, list[str]] = state.setdefault("completed", {})  # type: ignore[union-attr]
    failures: list[dict[str, object]] = state.setdefault("failures", [])  # type: ignore[union-attr]
    runs: list[dict[str, object]] = state.setdefault("runs", [])  # type: ignore[union-attr]

    ledger: list[dict[str, object]] = []
    store = DataStore(
        root,
        providers={"baostock": CountingProvider(PoliteBaoStockProvider(args.delay, args.year_delay), ledger)},
    )
    if args.codes_file:
        universe = sorted(
            line.strip().upper()
            for line in Path(args.codes_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
    else:
        sec_ref = latest_snapshot_with(store, "securities")
        if sec_ref is None:
            raise SystemExit("无含 securities 表的快照，无法派生宇宙；先跑 --table securities 或给 --codes-file")
        securities = store.read_table(SnapshotRef(snapshot_id=sec_ref, root=root), "securities")
        universe = derive_universe(securities)

    tables = list(TABLE_PRODUCTS) if args.table == "all" else [args.table]
    run_record: dict[str, object] = {
        "started_at": datetime.now(CN).isoformat(),
        "tables": tables,
        "universe": len(universe),
        "ledger": ledger,
    }

    def save() -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(state_path, state)

    def batch_prepare(table: str, product_id: str, todo: list[str]) -> None:
        codeless = not todo and table in ("calendar", "securities")
        batches = [todo[i : i + args.batch_size] for i in range(0, len(todo), args.batch_size)] or [()]
        consecutive = 0
        for index, batch in enumerate(batches, start=1):
            request = DataRequest(
                product_ids=(product_id,),
                codes=tuple(batch),
                start=WINDOW_START,
                end=WINDOW_END,
                as_of=datetime.now(CN),
                purpose="backtest_low_volatility",
                mode="free",
            )
            label = table if codeless else f"{table} batch {index}/{len(batches)}"
            try:
                ref = store.prepare(request)
                manifest = store.load_manifest(ref)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {
                        "table": table,
                        "batch": index,
                        "n_codes": len(batch),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                consecutive += 1
                save()
            else:
                failed_here = {f["product_id"] for f in manifest.get("failures", [])}
                if product_id in failed_here:
                    failures.append(
                        {"table": table, "batch": index, "n_codes": len(batch), "error": "manifest failures"}
                    )
                    consecutive += 1
                else:
                    # 空月（退市后/IPO 前无交易日）记 gaps 属预期，重取为空不判失败；
                    # 连续空月的重取成本在报告中单独说明
                    completed.setdefault(table, []).extend(batch)
                    consecutive = 0
                save()
                print(f"[full] {label} -> {ref.snapshot_id}")
            if consecutive >= 3:
                print("[full] 连续失败 3 批，停止本轮，避免触碰限流；失败已保留在 state")
                run_record["stopped"] = "consecutive_failures"
                raise SystemExit(2)

    for table in tables:
        if table in completed and table in ("calendar", "securities"):
            continue
        if table in TABLE_PRODUCTS:
            product_id = TABLE_PRODUCTS[table]
            if table in ("calendar", "securities"):
                batch_prepare(table, product_id, [])
                continue
            done = set(completed.get(table, []))
            todo = [code for code in universe if code not in done]
            if args.limit:
                todo = todo[: args.limit]
            batch_prepare(table, product_id, todo)
            continue
        # market_cap：gbjg 逐码取数 + 收盘价重建 + 分块导入（断点在 state["completed"]["market_cap"]）
        evidence_dir = root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        done = set(completed.get("market_cap", []))
        todo = [code for code in universe if code not in done]
        if args.limit:
            todo = todo[: args.limit]
        cap_rows: list[dict[str, object]] = []
        knowledge_rows: list[dict[str, object]] = []

        def flush(obtained_at: str) -> None:
            nonlocal cap_rows, knowledge_rows
            if not cap_rows:
                return
            stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
            chunk_dir = state_path.parent / f"market-cap-chunk-{stamp}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            cap_csv = chunk_dir / "market-cap.csv"
            with cap_csv.open("w", encoding="utf-8") as handle:
                handle.write("code,date,market_cap_cny,available_at\n")
                for cap in cap_rows:
                    handle.write(f"{cap['code']},{cap['date']},{cap['market_cap_cny']},{cap['available_at']}\n")
            knowledge_csv = chunk_dir / "knowledge-evidence.csv"
            with knowledge_csv.open("w", encoding="utf-8") as handle:
                handle.write(
                    "table_name,record_key,revision_id,supersedes,published_at,evidence_ref,sha256,verified\n"
                )
                for row in knowledge_rows:
                    handle.write(
                        f"{row['table_name']},{row['record_key']},{row['revision_id']},{row['supersedes']},"
                        f"{row['published_at']},{row['evidence_ref']},{row['sha256']},{row['verified']}\n"
                    )
            manifest_path = chunk_dir / "import-manifest.json"
            _write_json(
                manifest_path,
                {
                    "mode": "free",
                    "items": [
                        {
                            "product_id": "cn_a_market_cap",
                            "table": "market_cap",
                            "files": [cap_csv.name],
                            "source": _market_cap_source(obtained_at),
                        },
                        {
                            "product_id": "cn_a_knowledge",
                            "table": "knowledge_evidence",
                            "files": [knowledge_csv.name],
                            "source": _market_cap_knowledge_source(obtained_at),
                        },
                    ],
                },
            )
            ref = store.import_files(manifest_path, (cap_csv, knowledge_csv))
            completed.setdefault("market_cap", []).extend(
                sorted({str(cap["code"]) for cap in cap_rows})
            )
            save()
            print(f"[full] market_cap chunk -> {ref.snapshot_id}（累计 {len(completed['market_cap'])} 码）")
            cap_rows, knowledge_rows = [], []

        obtained_at = datetime.now(CN).isoformat()
        for code in todo:
            records, error = fetch_gbjg(code, args.gbjg_delay)
            if error:
                failures.append({"table": "market_cap", "code": code, "error": error})
                save()
                continue
            evidence_name = f"gbjg-{code.replace('.', '_')}-{datetime.now(CN):%Y%m%d}.json"
            evidence_path = evidence_dir / evidence_name
            _write_json(
                evidence_path,
                {
                    "code": code,
                    "endpoint": "akshare stock_zh_a_gbjg_em (datacenter RPT_F10_EH_EQUITY)",
                    "fetched_at": datetime.now(CN).isoformat(),
                    "rows": records,
                },
            )
            closes = closes_for_codes(store, [code])
            own = closes[closes["code"].astype(str) == code][["date", "close_raw"]]
            rows, _stats = reconstruct_market_cap(code, own, shares_steps(records), obtained_at)
            if not rows:
                failures.append(
                    {"table": "market_cap", "code": code, "error": "无收盘价或无可用股本阶梯，重建 0 行"}
                )
                save()
                continue
            cap_rows.extend(rows)
            sha = _sha256(evidence_path)
            for shares_from in sorted({str(cap["_shares_from"]) for cap in rows}):
                knowledge_rows.append(
                    {
                        "table_name": "market_cap",
                        "record_key": code,
                        "revision_id": f"gbjg-{shares_from}",
                        "supersedes": "",
                        "published_at": obtained_at,
                        "evidence_ref": evidence_name,
                        "sha256": sha,
                        "verified": "True",
                    }
                )
            if len({str(cap["code"]) for cap in cap_rows}) >= args.batch_size:
                flush(obtained_at)
        flush(obtained_at)

    run_record["finished_at"] = datetime.now(CN).isoformat()
    runs.append(run_record)
    save()
    return state


def _market_cap_source(obtained_at: str) -> dict[str, str]:
    return {
        "name": "eastmoney-gbjg-close-reconstruction",
        "fee_class": "free",
        "permission_basis": (
            "东财股本结构接口（datacenter-web RPT_F10_EH_EQUITY，公开免费，"
            "akshare stock_zh_a_gbjg_em）总股本阶梯 × BaoStock 收盘价重建；单位元；"
            "§3b 拟用公告日，但 gbjg 记录无公告日字段，available_at 保守记重建取得时间"
            "（待评审）；gbjg 原件存 evidence/"
        ),
        "source_url": "https://datacenter.eastmoney.com",
        "obtained_at": obtained_at,
    }


def _market_cap_knowledge_source(obtained_at: str) -> dict[str, str]:
    return {
        "name": "phase2-market-cap-knowledge-registration",
        "fee_class": "free",
        "permission_basis": (
            "market_cap 的 knowledge_evidence 按 (code, 股本版本) 登记 published_at 与原件 "
            "sha256（§3b 公告日字段缺失，保守记取得时间；逐日记录粒度见小样本报告待确认项）"
        ),
        "source_url": "",
        "obtained_at": obtained_at,
    }


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="二期 01.2 正式研究取数（小样本／全量）")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="小样本取数验证（3 只代码、30 次 provider.fetch 上限内）")
    sample.add_argument("--data-root", required=True)
    sample.add_argument("--out", required=True)
    sample.add_argument("--codes", nargs="*", default=None)
    sample.add_argument("--delay", type=float, default=3.0, help="provider.fetch 之间休眠秒数")
    sample.add_argument("--year-delay", type=float, default=1.5, help="operate 逐年查询之间休眠秒数")
    sample.add_argument("--gbjg-delay", type=float, default=2.0, help="东财 gbjg 请求之间休眠秒数")
    sample.add_argument("--force", action="store_true", help="允许在已有报告的输出目录重跑")

    full = sub.add_parser("full", help="全量初始化采集（需授权；断点续采、失败保留）")
    full.add_argument("--data-root", required=True)
    full.add_argument("--state", required=True)
    full.add_argument("--table", default="all", choices=[*ALL_TABLES])
    full.add_argument("--batch-size", type=int, default=200)
    full.add_argument("--delay", type=float, default=3.0)
    full.add_argument("--year-delay", type=float, default=1.5)
    full.add_argument("--gbjg-delay", type=float, default=2.0)
    full.add_argument("--limit", type=int, default=None)
    full.add_argument("--codes-file", default=None, help="代码清单（每行一个）；缺省按目录派生宇宙")

    args = parser.parse_args(argv)
    if args.command == "sample":
        report = run_sample(args)
        print(
            json.dumps(
                {key: report[key] for key in ("prepare_snapshot_id", "import_snapshot_id", "fetch_count_provider")},
                ensure_ascii=False,
            )
        )
    else:
        state = run_full(args)
        print(
            json.dumps(
                {
                    "completed": {key: len(value) for key, value in state["completed"].items()},
                    "failures": len(state["failures"]),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

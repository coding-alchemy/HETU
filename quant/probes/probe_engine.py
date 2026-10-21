"""二期 01.1 候选引擎准入探查：许可、隔离环境、免费本地输入与次日成交可行性。

就绪只能消费可核验的运行证据：解释器须实际执行并报告 Python 3.11 与
rqalpha 6.4.1；锁文件须非空、固定 rqalpha==6.4.1 且与隔离环境 pip freeze
一致；pip check 须实际通过。许可与依赖全部成立后，探查用隔离 venv 实际
运行 runner（本地合成 bundle 走真实引擎，不联网），登记并逐项哈希核验
实际消费文件（bundle 输入：定义、证券、日历、行情、信号；引擎输出：清单、
成交、净值等轨迹表），信号表 executable_from 与成交表的时点、价格关系
从表中直接核对，不采信任何自述字段。
许可未明示满足前不执行任何引擎文件、不启动子进程、不访问网络。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")

ENGINE_CANDIDATE = "rqalpha"
ENGINE_VERSION = "6.4.1"
ENGINE_PYTHON_VERSION_PREFIX = "Python 3.11."
# 与 .hetu/quant/qualification/phase2/engine-license-review.json 的 status 取值一致；
# 未经有权的决定者确认（decided_by），不得把记录自我声明视为许可满足。
LICENSE_STATUS_SATISFIED = "satisfied"
LICENSE_REVIEW_RELATIVE = Path("qualification") / "phase2" / "engine-license-review.json"
POSITIVE_EVIDENCE_RELATIVE = Path("qualification") / "phase2" / "engine-positive-evidence.json"
RUNTIME_RELATIVE = Path("runtime") / ENGINE_CANDIDATE
ENGINE_RUN_TIMEOUT_SECONDS = 300

# 探查自带的合成正例事实（与 quant/tests/test_engine_admission.py 正例一致）：
# d1 收盘信号目标权重 1.0，d2 开盘价 10.00 成交 1,000 股，d3 收盘 12.00。
PROBE_DAYS = ("2024-01-02", "2024-01-03", "2024-01-04")
PROBE_INITIAL_CASH = 10000.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_license(review_path: Path, evidence: list[dict[str, object]]) -> tuple[str, object]:
    """返回 (license_status, usage_scope)；核验复核记录与许可原文哈希一致。"""
    if not review_path.exists():
        evidence.append(
            {
                "kind": "license_review",
                "status": "missing",
                "path": str(review_path),
                "note": "无许可复核记录，许可视为未核验",
            }
        )
        return "unverified", None
    review = json.loads(review_path.read_text(encoding="utf-8"))
    license_file = Path(review.get("license_file", ""))
    if not license_file.is_absolute():
        license_file = review_path.parent / license_file
    expected_sha = review.get("license_sha256", "")
    if not license_file.exists() or _sha256(license_file) != expected_sha:
        evidence.append(
            {
                "kind": "license_review",
                "status": "evidence_mismatch",
                "path": str(license_file),
                "note": "许可原文缺失或哈希与复核记录不符",
            }
        )
        return "evidence_mismatch", None
    evidence.append(
        {
            "kind": "license_review",
            "status": "verified_original",
            "path": str(license_file),
            "sha256": expected_sha,
            "source_url": review.get("license_source_url"),
        }
    )
    status = review.get("status", "unclear")
    if status == LICENSE_STATUS_SATISFIED and not review.get("decided_by"):
        # 未经有权的决定者确认，不得把记录自我声明视为许可满足。
        status = "unclear"
    return status, review.get("usage_facts")


def _run_checked(cmd: list[str]) -> tuple[int, str]:
    """执行本地验证命令；不可执行或超时的目标返回非零，不视为有效证据。"""
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return completed.returncode, (completed.stdout or "").strip()


def _check_dependencies(
    engine_python: Path, requirements_lock: Path, *, license_ok: bool
) -> dict[str, str]:
    """许可满足后实际执行验证；许可未满足时只记录存在性，不执行目标文件。

    requirements_lock 取值：missing / invalid（空或未固定 rqalpha）/
    inconsistent（与 pip freeze 不一致）/ verified。
    """
    status = {"engine_python": "missing", "requirements_lock": "missing", "pip_check": "not_run"}
    lock_lines: list[str] = []
    if requirements_lock.exists():
        content = requirements_lock.read_text(encoding="utf-8")
        lock_lines = [line.strip() for line in content.splitlines() if line.strip()]
        pinned = any(line == f"{ENGINE_CANDIDATE}=={ENGINE_VERSION}" for line in lock_lines)
        status["requirements_lock"] = "valid_unverified" if pinned else "invalid"
    if not engine_python.exists():
        return status
    if not license_ok:
        status["engine_python"] = "present_unverified"
        return status
    code, out = _run_checked([str(engine_python), "--version"])
    if code != 0 or not out.startswith(ENGINE_PYTHON_VERSION_PREFIX):
        status["engine_python"] = "invalid"
        return status
    code, out = _run_checked(
        [str(engine_python), "-c", "import rqalpha; print(rqalpha.__version__)"]
    )
    if code != 0 or out != ENGINE_VERSION:
        status["engine_python"] = "rqalpha_missing"
        return status
    status["engine_python"] = "verified"
    code, out = _run_checked([str(engine_python), "-m", "pip", "check"])
    status["pip_check"] = "passed" if code == 0 else "failed"
    if status["pip_check"] != "passed":
        return status
    # 锁文件须与隔离环境实际冻结一致（排序后逐行相等）
    if status["requirements_lock"] in ("missing", "invalid"):
        return status
    code, out = _run_checked([str(engine_python), "-m", "pip", "freeze"])
    freeze_lines = sorted(line.strip() for line in out.splitlines() if line.strip() and not line.startswith("-e "))
    if code != 0 or freeze_lines != sorted(lock_lines):
        status["requirements_lock"] = "inconsistent"
        return status
    status["requirements_lock"] = "verified"
    return status


REQUIRED_INPUT_ARTIFACTS: tuple[str, ...] = (
    "definition.json",
    "signals.parquet",
    "tables/calendar.parquet",
    "tables/daily_bars.parquet",
    "tables/historical_securities.parquet",
    "tables/trading_rules.parquet",
)
REQUIRED_OUTPUT_ARTIFACTS: tuple[str, ...] = ("backtest.json", "trades.parquet", "nav.parquet")
# 引擎输出的全部轨迹表（超出必需清单的部分同样登记哈希，增强证据完整性）
OUTPUT_TABLE_NAMES: tuple[str, ...] = (
    "signals", "targets", "orders", "trades", "positions",
    "cash", "nav", "costs", "exclusions",
)


def _check_positive_evidence(evidence_path: Path) -> tuple[str, str]:
    """复核正向运行证据：实际消费文件逐项哈希核验，时点关系从表中直接核对。

    返回 (free_input_status, next_open_feasible)。必需清单内的 bundle 输入
    （定义、证券、日历、行情、信号）与引擎输出（清单、成交、净值）必须全部
    登记且哈希与原件一致；登记路径必须就是目录内的同名被消费文件；信号表
    executable_from 必须是 signal_at 的下一交易日，每笔成交必须落在同代码
    信号的 executable_from 且价格为当日开盘价、有限数值。任何文件缺失、
    哈希不符、空信号／空成交或时点矛盾均视为无效证据。
    """
    if not evidence_path.exists():
        return "pending_engine_run", "unverified"
    record = json.loads(evidence_path.read_text(encoding="utf-8"))
    artifacts = record.get("artifacts", {})
    bundle = Path(record.get("bundle_dir", ""))
    output_dir = Path(record.get("output_dir", ""))
    for name in REQUIRED_INPUT_ARTIFACTS + REQUIRED_OUTPUT_ARTIFACTS:
        entry = artifacts.get(name)
        if not isinstance(entry, dict):
            return "evidence_invalid", "unverified"
        expected_dir = bundle if name in REQUIRED_INPUT_ARTIFACTS else output_dir
        # 绑定实际消费路径：登记路径必须就是目录内的同名被消费文件，
        # 目录内无关文件的自洽哈希不构成证据。
        artifact = Path(entry.get("path", ""))
        try:
            if artifact.resolve() != (expected_dir / name).resolve():
                return "evidence_invalid", "unverified"
        except (OSError, ValueError):
            return "evidence_invalid", "unverified"
        if not artifact.exists() or _sha256(artifact) != entry.get("sha256"):
            return "evidence_invalid", "unverified"

    import math

    import pandas as pd

    try:
        calendar = pd.read_parquet(bundle / "tables" / "calendar.parquet")
        trading_days = [
            str(day)
            for day, flag in zip(calendar["date"].astype(str), calendar["is_trading_day"], strict=False)
            if str(flag).strip().lower() in ("true", "1")
        ]
        signals = pd.read_parquet(bundle / "signals.parquet")
        bars = pd.read_parquet(bundle / "tables" / "daily_bars.parquet")
        trades = pd.read_parquet(output_dir / "trades.parquet")
        # 空信号／空成交不构成次日成交证据：至少一笔有效匹配成交
        if not len(signals) or not len(trades):
            return "evidence_invalid", "contradicted"
        for signal in signals.itertuples():
            signal_index = trading_days.index(signal.signal_at)
            if signal_index + 1 >= len(trading_days):
                return "evidence_invalid", "contradicted"
            if signal.executable_from != trading_days[signal_index + 1]:
                return "evidence_invalid", "contradicted"
        for trade in trades.itertuples():
            matching = signals[
                (signals["code"] == trade.code) & (signals["executable_from"] == trade.executed_at)
            ]
            if matching.empty:
                return "evidence_invalid", "contradicted"
            open_rows = bars[(bars["code"] == trade.code) & (bars["date"] == trade.executed_at)]
            if open_rows.empty:
                return "evidence_invalid", "contradicted"
            trade_price = float(trade.price_raw)
            open_price = float(open_rows.iloc[0]["open_raw"])
            if not (math.isfinite(trade_price) and math.isfinite(open_price)):
                return "evidence_invalid", "contradicted"
            if abs(open_price - trade_price) > 1e-9:
                return "evidence_invalid", "contradicted"
    except (KeyError, ValueError, OSError, json.JSONDecodeError, AttributeError):
        return "evidence_invalid", "contradicted"
    return "verified_local_bundle", "verified"


# 引擎语义清单（02.1）：逐项支持状态、数据需求与日线近似；手算对照证据在
# quant/tests/test_backtest_account.py（独立账本，不调用引擎代码）。
ENGINE_SEMANTICS: dict[str, object] = {
    "schema_version": 1,
    "engine": ENGINE_CANDIDATE,
    "version": ENGINE_VERSION,
    "supported": [
        {
            "item": "next_open_fill",
            "note": "信号日收盘信号，executable_from 日盘前以当日开盘限价报送并成交，"
            "成交价=当日开盘价",
        },
        {
            "item": "commission_min_per_order",
            "note": "佣金按协议费率，最低佣金按订单一次计收（quant/engine/mod.py HetuStockCostDecider）",
        },
        {
            "item": "stamp_tax_sell_by_date",
            "note": "印花税卖出单边，费率按 trading_rules fee_rule 逐日期取值（2023-08-28 起 0.5‰）",
        },
        {
            "item": "transfer_fee_both_by_date",
            "note": "过户费双向，费率按 trading_rules 逐日期取值（2022-04-29 起 0.01‰）",
        },
        {"item": "slippage_in_execution_price",
         "note": "观测原价与含滑点成交价分列，滑点归因不重复扣现，毛净只加回显式费用"},
        {
            "item": "t1_sellable",
            "note": "T+1：当日买入不可卖，可卖=上一交易日日终持仓；超可卖卖出被引擎拒绝并记录原因",
        },
        {
            "item": "limit_up_down_conservative",
            "note": "开盘涨停拒绝买入、开盘跌停拒绝卖出（板块/ST 与生效日按 trading_rules）",
        },
        {"item": "lot_size_rules", "note": "整手 100 股申报、零股一次性卖出、科创板 200 股起 1 股递增"},
        {
            "item": "capacity_participation_cap",
            "note": "申报量受此前 20 个交易日成交量中位数的 participation_rate 上限约束",
        },
        {
            "item": "same_day_expiry",
            "note": "订单当日未成交部分收盘撤销，下一调仓按真实持仓重算，卖出失败资金不用于买入",
        },
        {
            "item": "cash_dividend_receivable_payable",
            "note": "登记日权益、除息应收、实际派息到账，缺日期明确拒绝",
        },
        {"item": "bonus_split_adjustment", "note": "生产m为新股数/原股数，送转股按实际上市日可卖"},
        {
            "item": "dividend_tax_holding_diff",
            "note": "股息差别化个税 20%/10%/0% 按持有期在卖出时扣缴（fee_rule dividend=holding_diff 启用）",
        },
        {
            "item": "trace_tables_nine",
            "note": "九张固定轨迹表，费用分项与 costs 表逐笔一致（同一费用决定器数据源）",
        },
    ],
    "failed": [],
    "data_requirements": [
        "公司行动须有登记日和实际派息日，缺字段先复用已有raw，不猜日期",
        "送转须有原登记/上市日；退市仅支持具证现金补偿，其他处置受限",
        "涨跌幅限制基价：除息日次一交易日的前收应为除权参考价，现用原始前收（合成场景不触发，生产需复权基价）",
    ],
    "daily_approximations": [
        "日线撮合：以当日开盘价成交近似次一交易日开盘市价单；一字排队不可证明，开盘处于不利限制价保守拒绝",
        "开盘限价单以当日开盘限价报送（1d 无法建模集合竞价队列）",
        "滑点进入模拟成交价，不建盘口冲击模型",
        "停牌日无 bar 或零成交即不可成交；估值用最近原始收盘（缺证据不按末价强平的对象语义属 02 后续）",
    ],
    "ledger_evidence": "quant/tests/test_backtest_account.py（独立手算账本，逐日核对现金/持仓/可卖/应收/净值/费用）",
}


def _write_probe_bundle(bundle_dir: Path) -> None:
    """落盘探查正例的生产布局 bundle：有效证券、日历、行情、制度与信号。

    制度表用合成零费率（stamp=0、transfer=0、dividend=zero），正例只证
    撮合机制（次日开盘成交）；完整费用语义由 quant/tests/test_backtest_account.py
    的独立手算账本证明（见准入记录 semantics 字段与 engine-semantics.json）。
    """
    import pandas as pd

    bundle_dir.mkdir(parents=True)
    (bundle_dir / "admission.json").write_text(
        json.dumps({"decision": "candidate_ready"}), encoding="utf-8"
    )
    (bundle_dir / "definition.json").write_text(
        json.dumps(
            {
                "strategy_id": "engine_admission_probe",
                "initial_cash_cny": PROBE_INITIAL_CASH,
                "costs": {"commission_rate": 0.0, "min_commission_cny": 0.0, "slippage_rate": 0.0},
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "signal_at": [PROBE_DAYS[0]],
            "executable_from": [PROBE_DAYS[1]],
            "code": ["SYNTH"],
            "target_weight": [1.0],
        }
    ).to_parquet(bundle_dir / "signals.parquet", index=False)
    tables_dir = bundle_dir / "tables"
    tables_dir.mkdir()
    pd.DataFrame({"date": list(PROBE_DAYS), "is_trading_day": ["True"] * 3}).to_parquet(
        tables_dir / "calendar.parquet", index=False
    )
    pd.DataFrame(
        {
            "date": list(PROBE_DAYS),
            "code": ["SYNTH"] * 3,
            "open_raw": [9.8, 10.0, 11.0],
            "high_raw": [9.9, 10.5, 12.0],
            "low_raw": [9.7, 10.4, 11.0],
            "close_raw": [9.9, 10.5, 12.0],
            "volume_shares": [1_000_000] * 3,
        }
    ).to_parquet(tables_dir / "daily_bars.parquet", index=False)
    pd.DataFrame(
        {
            "date": list(PROBE_DAYS),
            "code": ["SYNTH"] * 3,
            "is_st": ["False"] * 3,
            "is_suspended": ["False"] * 3,
            "is_listed": ["True"] * 3,
        }
    ).to_parquet(tables_dir / "security_status.parquet", index=False)
    pd.DataFrame(
        {
            "code": ["SYNTH"],
            "valid_from": ["2020-01-02"],
            "valid_to": [""],
            "listed": ["True"],
            "market": ["SZ"],
            "board": ["main"],
            "available_at": ["2020-01-02T18:00:00+08:00"],
            "evidence_ref": ["ev-synth"],
        }
    ).to_parquet(tables_dir / "historical_securities.parquet", index=False)
    pd.DataFrame(
        {
            "rule_id": ["probe-rules"],
            "market": ["SZ"],
            "board": ["main"],
            "security_type": ["stock"],
            "valid_from": ["2016-01-01"],
            "valid_to": [""],
            "min_buy_qty": ["100"],
            "qty_step": ["100"],
            "tick_size": ["0.01"],
            "settlement": ["T+1"],
            "limit_rule": ["pct10"],
            "fee_rule": ["stamp=sell:0;transfer=both:0;dividend=zero"],
            "source_ref": ["ev-probe-rules"],
        }
    ).to_parquet(tables_dir / "trading_rules.parquet", index=False)
    pd.DataFrame(
        {
            "code": [], "ex_date": [], "action_type": [], "m": [], "d": [],
            "terms_raw": [], "available_at": [],
        }
    ).to_parquet(tables_dir / "corporate_actions.parquet", index=False)


def _register_artifacts(
    evidence_dir: Path, bundle_dir: Path, output_dir: Path
) -> dict[str, dict[str, str]]:
    """登记实际消费文件与引擎产出的逐项 sha256（路径绑定到登记目录内的同名文件）。"""
    artifacts: dict[str, dict[str, str]] = {}
    for name in REQUIRED_INPUT_ARTIFACTS:
        path = bundle_dir / name
        artifacts[name] = {"path": str(path), "sha256": _sha256(path)}
    artifacts["backtest.json"] = {
        "path": str(output_dir / "backtest.json"),
        "sha256": _sha256(output_dir / "backtest.json"),
    }
    for name in OUTPUT_TABLE_NAMES:
        path = output_dir / f"{name}.parquet"
        # 输入清单已有同名条目（signals.parquet）时不得覆盖：输入哈希优先
        if path.exists() and f"{name}.parquet" not in artifacts:
            artifacts[f"{name}.parquet"] = {"path": str(path), "sha256": _sha256(path)}
    return artifacts


def _run_engine_probe(
    engine_python: Path,
    repo_root: Path,
    output_dir: Path,
    evidence: list[dict[str, object]],
) -> tuple[str, str]:
    """许可与依赖成立后，用隔离 venv 实际运行 runner 并登记／核验证据。

    返回 (free_input_status, next_open_feasible)。任何失败都如实记录并保持
    blocked，不放宽校验。
    """
    runner_path = repo_root / "quant" / "engine" / "runner.py"
    if not runner_path.exists():
        evidence.append(
            {"kind": "engine_run", "status": "runner_missing", "path": str(runner_path)}
        )
        return "engine_run_failed", "unverified"
    bundle_dir = output_dir / "bundle"
    engine_out = output_dir / "engine-out"
    _write_probe_bundle(bundle_dir)
    try:
        completed = subprocess.run(
            [
                str(engine_python), str(runner_path),
                "--bundle", str(bundle_dir), "--out", str(engine_out),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=ENGINE_RUN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        evidence.append(
            {
                "kind": "engine_run",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return "engine_run_failed", "unverified"
    if completed.returncode != 0:
        evidence.append(
            {
                "kind": "engine_run",
                "status": "failed",
                "returncode": completed.returncode,
                "stderr": (completed.stderr or "")[-2000:],
            }
        )
        return "engine_run_failed", "unverified"
    evidence.append(
        {
            "kind": "engine_run",
            "status": "passed",
            "engine_python": str(engine_python),
            "runner": str(runner_path),
            "bundle_dir": str(bundle_dir),
            "output_dir": str(engine_out),
        }
    )
    artifacts = _register_artifacts(output_dir, bundle_dir, engine_out)
    record = {
        "bundle_dir": str(bundle_dir),
        "output_dir": str(engine_out),
        "artifacts": artifacts,
        "verified_at": datetime.now(tz=CN).isoformat(),
        "runner": "probe_engine",
    }
    evidence_path = output_dir / "engine-positive-evidence.json"
    evidence_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    free_input_status, next_open_feasible = _check_positive_evidence(evidence_path)
    if free_input_status == "verified_local_bundle":
        evidence.append(
            {
                "kind": "engine_positive_evidence",
                "status": "verified",
                "path": str(evidence_path),
                "note": "探查自产证据：逐项哈希与次日开盘时点关系核验通过",
            }
        )
    return free_input_status, next_open_feasible


def probe_engine(
    output_dir: Path,
    *,
    data_root: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, object]:
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    data_root = data_root or repo_root / ".hetu" / "quant"
    evidence: list[dict[str, object]] = []

    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"output dir already exists: {output_dir}")

    license_status, usage_scope = _check_license(data_root / LICENSE_REVIEW_RELATIVE, evidence)

    runtime_dir = data_root / RUNTIME_RELATIVE
    engine_python = runtime_dir / ".venv" / "bin" / "python"
    requirements_lock = runtime_dir / "requirements.lock"
    dependency_status = _check_dependencies(
        engine_python, requirements_lock, license_ok=license_status == LICENSE_STATUS_SATISFIED
    )

    dependencies_ready = (
        dependency_status["engine_python"] == "verified"
        and dependency_status["requirements_lock"] == "verified"
        and dependency_status["pip_check"] == "passed"
    )
    if license_status == LICENSE_STATUS_SATISFIED and dependencies_ready:
        # 许可与依赖全部可核验后，才实际启动引擎子进程跑正例；否则只复核既有证据。
        free_input_status, next_open_feasible = _run_engine_probe(
            engine_python, repo_root, Path(output_dir), evidence
        )
    else:
        free_input_status, next_open_feasible = _check_positive_evidence(
            data_root / POSITIVE_EVIDENCE_RELATIVE
        )
        if free_input_status == "verified_local_bundle":
            evidence.append(
                {
                    "kind": "engine_positive_evidence",
                    "status": "verified",
                    "path": str(data_root / POSITIVE_EVIDENCE_RELATIVE),
                }
            )

    ready = (
        license_status == LICENSE_STATUS_SATISFIED
        and dependencies_ready
        and free_input_status == "verified_local_bundle"
        and next_open_feasible == "verified"
    )
    admission: dict[str, object] = {
        "candidate": ENGINE_CANDIDATE,
        "version": ENGINE_VERSION,
        "license_status": license_status,
        "usage_scope": usage_scope,
        "dependency_status": dependency_status,
        "free_input_status": free_input_status,
        "next_open_feasible": next_open_feasible,
        "semantics": {
            "execution": "next_open_fill",
            "costs": "full",
            "note": "02.1 在已核验的日期、制度及数据范围内通过合成独立账本（现金/持仓/可卖/应收/"
            "净值/费用逐日核对）：次日开盘成交、T+1、涨跌停保守拒绝、整手/零股/"
            "科创板数量规则、费率与限制生效切换、容量上限、除息应收/派息到账/送转、"
            "差别化股息税。手算证据见 quant/tests/test_backtest_account.py；"
            "逐项支持/数据需求/日线近似见同目录 engine-semantics.json",
        },
        "decision": "candidate_ready" if ready else "blocked",
        "evidence": evidence,
        "probed_at": datetime.now(tz=CN).isoformat(),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "engine-admission.json").write_text(
        json.dumps(admission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    semantics_doc = dict(ENGINE_SEMANTICS)
    semantics_doc["verified_at"] = datetime.now(tz=CN).isoformat()
    semantics_doc["verification"] = {
        "decision": admission["decision"],
        "probe_case": "合成零费率正例：次日开盘成交机制",
        "ledger_evidence": "quant/tests/test_backtest_account.py",
    }
    (output_dir / "engine-semantics.json").write_text(
        json.dumps(semantics_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return admission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RQAlpha 候选引擎准入探查（不联网）")
    parser.add_argument("--out", required=True, help="输出目录（必须为新目录）")
    parser.add_argument("--data-root", default=None, help="数据根目录，默认 .hetu/quant")
    args = parser.parse_args(argv)
    admission = probe_engine(
        Path(args.out),
        data_root=Path(args.data_root) if args.data_root else None,
    )
    print(json.dumps({"decision": admission["decision"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

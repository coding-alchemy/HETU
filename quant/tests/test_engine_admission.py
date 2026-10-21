"""二期 01.1 引擎准入：许可未知不放行；就绪只能消费可核验的运行证据。

合成 bundle 与伪证据只含事实；准入决定、子进程与网络调用次数的期望留在本测试侧。
正向证据校验锚定实际消费文件：bundle 输入（定义、证券、日历、行情、信号）
与输出（清单、成交、净值）逐项哈希核验，信号与成交的时点关系从表中直接核对。
真实引擎正例只在许可通过且隔离环境安装后运行，缺环境时明确受阻，
跳过本身不构成准入通过证据（准入结论以 probe 输出的 engine-admission.json 为准）。
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "quant" / "probes" / "probe_engine.py"
RUNNER_PATH = REPO_ROOT / "quant" / "engine" / "runner.py"
PHASE2_QUALIFICATION = REPO_ROOT / ".hetu" / "quant" / "qualification" / "phase2"

from hetu_quant.backtest import TABLE_COLUMNS  # noqa: E402


def _latest_admission_decision() -> str | None:
    records = sorted(PHASE2_QUALIFICATION.glob("engine-admission-*/engine-admission.json"))
    if not records:
        return None
    return str(json.loads(records[-1].read_text(encoding="utf-8")).get("decision"))

# 合成行情事实（正例共用）：d1 收盘信号，d2 开盘价 10.00，d3 收盘价 12.00。
SYNTH_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]
SYNTH_OPEN_D2 = 10.0
SYNTH_CLOSE_D3 = 12.0
SYNTH_CASH = 10000.0


def _load(path: Path) -> dict:
    return runpy.run_path(str(path))


def _counting_guard(calls: list):
    def _blocked(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("forbidden subprocess/network call during admission probe")

    return _blocked


@pytest.fixture
def io_guards(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"subprocess": [], "network": []}
    monkeypatch.setattr(subprocess, "Popen", _counting_guard(calls["subprocess"]))
    monkeypatch.setattr(subprocess, "run", _counting_guard(calls["subprocess"]))
    monkeypatch.setattr(socket, "socket", _counting_guard(calls["network"]))
    monkeypatch.setattr(socket, "create_connection", _counting_guard(calls["network"]))
    monkeypatch.setattr(urllib.request, "urlopen", _counting_guard(calls["network"]))
    return calls


def _write_tables(bundle: Path, *, with_bars: bool) -> None:
    """生产布局 tables/：日历、行情、状态、历史证券、制度与公司行动。"""
    tables = bundle / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"date": SYNTH_DAYS, "is_trading_day": ["True"] * 3}
    ).to_parquet(tables / "calendar.parquet", index=False)
    if with_bars:
        pd.DataFrame(
            {
                "date": SYNTH_DAYS,
                "code": ["SYNTH"] * 3,
                "open_raw": [9.8, SYNTH_OPEN_D2, 11.0],
                "high_raw": [9.9, 10.5, SYNTH_CLOSE_D3],
                "low_raw": [9.7, 10.4, 11.0],
                "close_raw": [9.9, 10.5, SYNTH_CLOSE_D3],
                "volume_shares": [1_000_000] * 3,
            }
        ).to_parquet(tables / "daily_bars.parquet", index=False)
    pd.DataFrame(
        {
            "date": SYNTH_DAYS,
            "code": ["SYNTH"] * 3,
            "is_st": ["False"] * 3,
            "is_suspended": ["False"] * 3,
            "is_listed": ["True"] * 3,
        }
    ).to_parquet(tables / "security_status.parquet", index=False)
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
    ).to_parquet(tables / "historical_securities.parquet", index=False)
    pd.DataFrame(
        {
            "rule_id": ["synth-rules"],
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
            "source_ref": ["ev-synth-rules"],
        }
    ).to_parquet(tables / "trading_rules.parquet", index=False)
    pd.DataFrame(
        {
            "code": [], "ex_date": [], "action_type": [], "m": [], "d": [],
            "terms_raw": [], "available_at": [],
        }
    ).to_parquet(tables / "corporate_actions.parquet", index=False)


def _write_bundle(bundle: Path, *, decision: str | None, with_bars: bool) -> None:
    bundle.mkdir(parents=True)
    (bundle / "definition.json").write_text(json.dumps({"strategy_id": "synthetic"}), encoding="utf-8")
    if decision is not None:
        (bundle / "admission.json").write_text(json.dumps({"decision": decision}), encoding="utf-8")
    _write_tables(bundle, with_bars=with_bars)


def _write_signals(bundle: Path, *, signal_at: str, executable_from: str) -> None:
    pd.DataFrame(
        {
            "signal_at": [signal_at],
            "executable_from": [executable_from],
            "code": ["SYNTH"],
            "target_weight": [1.0],
        }
    ).to_parquet(bundle / "signals.parquet", index=False)


def _write_real_bundle(bundle: Path) -> None:
    """写入有效证券、日历、行情与信号事实（含次日开盘成交所需的全部输入）。"""
    _write_bundle(bundle, decision="candidate_ready", with_bars=True)
    _write_signals(bundle, signal_at=SYNTH_DAYS[0], executable_from=SYNTH_DAYS[1])
    (bundle / "definition.json").write_text(
        json.dumps(
            {
                "strategy_id": "synthetic_probe",
                "initial_cash_cny": SYNTH_CASH,
                "costs": {"commission_rate": 0.0, "min_commission_cny": 0.0, "slippage_rate": 0.0},
            }
        ),
        encoding="utf-8",
    )


def _write_license_review(data_root: Path, *, status: str, decided_by: str | None) -> None:
    review_dir = data_root / "qualification" / "phase2"
    review_dir.mkdir(parents=True, exist_ok=True)
    license_file = review_dir / "license-original.txt"
    license_file.write_bytes(b"synthetic license text")
    review = {
        "status": status,
        "decided_by": decided_by,
        "license_file": license_file.name,
        "license_sha256": hashlib.sha256(license_file.read_bytes()).hexdigest(),
        "license_source_url": "https://example.invalid/LICENSE",
        "usage_facts": {"subject": "synthetic"},
    }
    (review_dir / "engine-license-review.json").write_text(
        json.dumps(review, ensure_ascii=False), encoding="utf-8"
    )


def _setup_env(data_root: Path, repo_root: Path, *, python: Path | None, lock_text: str | None) -> None:
    """在伪 data_root／repo_root 下布置引擎环境事实（python 可执行体与锁文件内容）。"""
    if python is not None:
        venv_bin = data_root / "runtime" / "rqalpha" / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        target = venv_bin / "python"
        if python == Path(sys.executable):
            target.symlink_to(python)
        else:
            target.write_bytes(python.read_bytes())
            target.chmod(0o755)
    if lock_text is not None:
        lock = data_root / "runtime" / "rqalpha" / "requirements.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(lock_text, encoding="utf-8")


def _fake_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / "quant" / "engine").mkdir(parents=True, exist_ok=True)
    return repo


def _write_engine_output(
    out: Path, *, fill_date: str = SYNTH_DAYS[1], fill_price: float = SYNTH_OPEN_D2
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest.json").write_text(json.dumps({"run_id": "synthetic"}), encoding="utf-8")
    pd.DataFrame(
        {
            "trade_id": ["t1"],
            "executed_at": [fill_date],
            "code": ["SYNTH"],
            "side": ["BUY"],
            "qty": [1000],
            "price_raw": [fill_price],
        }
    ).to_parquet(out / "trades.parquet", index=False)
    pd.DataFrame({"date": SYNTH_DAYS, "net_nav": [9910.0, 10500.0, 12000.0]}).to_parquet(
        out / "nav.parquet", index=False
    )


def _write_positive_evidence(
    data_root: Path,
    bundle: Path,
    out: Path,
    *,
    tamper_nav_hash: bool = False,
    replace_manifest_with_unrelated: bool = False,
    point_at_unrelated: bool = False,
) -> None:
    """按证据合同登记 bundle 输入与引擎输出的逐项哈希清单。"""
    if replace_manifest_with_unrelated:
        unrelated = data_root / "unrelated.txt"
        unrelated.write_bytes(b"unrelated")
        artifacts = {
            "unrelated.txt": {
                "path": str(unrelated),
                "sha256": hashlib.sha256(unrelated.read_bytes()).hexdigest(),
            }
        }
    elif point_at_unrelated:
        # 必需条目名齐全、路径在声明目录内、哈希与所指向文件自洽——
        # 但指向的是无关文件，而非实际消费的同名文件。
        unrelated_in = bundle / "unrelated-input.bin"
        unrelated_in.write_bytes(b"unrelated input")
        unrelated_out = out / "unrelated-output.bin"
        unrelated_out.write_bytes(b"unrelated output")
        artifacts = {}
        for name in (
            "definition.json",
            "signals.parquet",
            "tables/calendar.parquet",
            "tables/daily_bars.parquet",
            "tables/historical_securities.parquet",
            "tables/trading_rules.parquet",
        ):
            artifacts[name] = {
                "path": str(unrelated_in),
                "sha256": hashlib.sha256(unrelated_in.read_bytes()).hexdigest(),
            }
        for name in ("backtest.json", "trades.parquet", "nav.parquet"):
            artifacts[name] = {
                "path": str(unrelated_out),
                "sha256": hashlib.sha256(unrelated_out.read_bytes()).hexdigest(),
            }
    else:
        artifacts = {}
        for name in (
            "definition.json",
            "signals.parquet",
            "tables/calendar.parquet",
            "tables/daily_bars.parquet",
            "tables/historical_securities.parquet",
            "tables/trading_rules.parquet",
        ):
            artifacts[name] = {
                "path": str(bundle / name),
                "sha256": hashlib.sha256((bundle / name).read_bytes()).hexdigest(),
            }
        for name in ("backtest.json", "trades.parquet", "nav.parquet"):
            artifacts[name] = {
                "path": str(out / name),
                "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
            }
    if tamper_nav_hash:
        artifacts["nav.parquet"]["sha256"] = "0" * 64
    evidence = {
        "bundle_dir": str(bundle),
        "output_dir": str(out),
        "artifacts": artifacts,
        "verified_at": "2026-10-17T00:00:00+08:00",
    }
    review_dir = data_root / "qualification" / "phase2"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "engine-positive-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
    )


def _admission_with_evidence(
    tmp_path: Path,
    name: str,
    probe: dict,
    *,
    tamper_nav_hash: bool = False,
    replace_manifest: bool = False,
    point_at_unrelated: bool = False,
    empty_trades: bool = False,
    fill_date: str = SYNTH_DAYS[1],
    fill_price: float = SYNTH_OPEN_D2,
    signal_at: str = SYNTH_DAYS[0],
    executable_from: str = SYNTH_DAYS[1],
) -> dict:
    """布置许可满足 + 引擎环境 + 正向证据的合成场景，返回准入结果。"""
    data_root = tmp_path / name
    repo = _fake_repo(tmp_path, f"repo-{name}")
    _write_license_review(data_root, status="satisfied", decided_by="user")
    _setup_env(data_root, repo, python=Path(sys.executable), lock_text="rqalpha==6.4.1\n")
    bundle = data_root / "bundle"
    _write_real_bundle(bundle)
    if empty_trades:
        pd.DataFrame(
            {"signal_at": [], "executable_from": [], "code": [], "target_weight": []}
        ).to_parquet(bundle / "signals.parquet", index=False)
    else:
        _write_signals(bundle, signal_at=signal_at, executable_from=executable_from)
    out = data_root / "engine-out"
    if empty_trades:
        out.mkdir(parents=True, exist_ok=True)
        (out / "backtest.json").write_text(json.dumps({"run_id": "synthetic"}), encoding="utf-8")
        pd.DataFrame(
            {"trade_id": [], "executed_at": [], "code": [], "side": [], "qty": [], "price_raw": []}
        ).to_parquet(out / "trades.parquet", index=False)
        pd.DataFrame({"date": SYNTH_DAYS, "net_nav": [9910.0, 10500.0, 12000.0]}).to_parquet(
            out / "nav.parquet", index=False
        )
    else:
        _write_engine_output(out, fill_date=fill_date, fill_price=fill_price)
    _write_positive_evidence(
        data_root,
        bundle,
        out,
        tamper_nav_hash=tamper_nav_hash,
        replace_manifest_with_unrelated=replace_manifest,
        point_at_unrelated=point_at_unrelated,
    )
    return probe["probe_engine"](tmp_path / f"{name}-out", data_root=data_root, repo_root=repo)


def test_engine_admission_blocks_unknown_license_or_remote_fallback(
    tmp_path: Path, io_guards: dict[str, list]
) -> None:
    probe = _load(PROBE_PATH)
    data_root = tmp_path / "data"

    # 许可未核验：准入受阻，且不启动任何引擎子进程（即使引擎文件存在）。
    _setup_env(data_root, _fake_repo(tmp_path, "repo"), python=Path(sys.executable), lock_text="rqalpha==6.4.1\n")
    result = probe["probe_engine"](
        tmp_path / "probe-out", data_root=data_root, repo_root=_fake_repo(tmp_path, "repo")
    )
    assert result["decision"] == "blocked"
    assert result["license_status"] == "unverified"
    assert (tmp_path / "probe-out" / "engine-admission.json").exists()
    process_start_count = len(io_guards["subprocess"])
    assert process_start_count == 0  # 许可未知

    # 许可未核验：runner 在导入或启动引擎前拒绝。
    runner = _load(RUNNER_PATH)
    bundle = tmp_path / "bundle-unknown"
    _write_bundle(bundle, decision=None, with_bars=True)
    with pytest.raises(runner["EngineAdmissionError"]):
        runner["run_engine"](bundle, tmp_path / "engine-out-1")

    # 准入记录被否决：同样拒绝。
    bundle2 = tmp_path / "bundle-blocked"
    _write_bundle(bundle2, decision="blocked", with_bars=True)
    with pytest.raises(runner["EngineAdmissionError"]):
        runner["run_engine"](bundle2, tmp_path / "engine-out-2")

    # 准入通过但本地缺 bar：拒绝且不允许任何在线回退。
    bundle3 = tmp_path / "bundle-missing-bars"
    _write_bundle(bundle3, decision="candidate_ready", with_bars=False)
    with pytest.raises(runner["BundleDataError"]):
        runner["run_engine"](bundle3, tmp_path / "engine-out-3")

    network_call_count = len(io_guards["network"])
    assert network_call_count == 0  # 本地缺数，无在线回退
    assert len(io_guards["subprocess"]) == 0


def test_engine_admission_rejects_unverifiable_evidence(tmp_path: Path) -> None:
    """许可满足后，文件存在不等于就绪：伪 Python、空锁、篡改或矛盾证据均受阻。"""
    probe = _load(PROBE_PATH)

    # 变体 1：普通文本冒充引擎 Python。
    data_root = tmp_path / "v1"
    repo1 = _fake_repo(tmp_path, "repo1")
    _write_license_review(data_root, status="satisfied", decided_by="user")
    fake_python = data_root / "fake-python.txt"
    fake_python.write_text("not a python interpreter", encoding="utf-8")
    _setup_env(data_root, repo1, python=fake_python, lock_text="rqalpha==6.4.1\n")
    result = probe["probe_engine"](tmp_path / "v1-out", data_root=data_root, repo_root=repo1)
    assert result["decision"] == "blocked"
    assert result["dependency_status"]["engine_python"] == "invalid"

    # 变体 2：真实 Python 但锁文件为空。
    data_root = tmp_path / "v2"
    repo2 = _fake_repo(tmp_path, "repo2")
    _write_license_review(data_root, status="satisfied", decided_by="user")
    _setup_env(data_root, repo2, python=Path(sys.executable), lock_text="")
    result = probe["probe_engine"](tmp_path / "v2-out", data_root=data_root, repo_root=repo2)
    assert result["decision"] == "blocked"
    assert result["dependency_status"]["requirements_lock"] == "invalid"

    # 变体 3：锁文件固定 rqalpha，但解释器装的不是 rqalpha 6.4.1。
    data_root = tmp_path / "v3"
    repo3 = _fake_repo(tmp_path, "repo3")
    _write_license_review(data_root, status="satisfied", decided_by="user")
    _setup_env(data_root, repo3, python=Path(sys.executable), lock_text="rqalpha==6.4.1\n")
    result = probe["probe_engine"](tmp_path / "v3-out", data_root=data_root, repo_root=repo3)
    assert result["decision"] == "blocked"
    assert result["dependency_status"]["engine_python"] == "rqalpha_missing"

    # 变体 4：证据产物哈希被篡改。
    result = _admission_with_evidence(tmp_path, "v4", probe, tamper_nav_hash=True)
    assert result["decision"] == "blocked"
    assert result["free_input_status"] == "evidence_invalid"

    # 变体 5：成交落在信号 executable_from 之前的信号当日（次日语义矛盾）。
    result = _admission_with_evidence(tmp_path, "v5", probe, fill_date=SYNTH_DAYS[0])
    assert result["decision"] == "blocked"
    assert result["next_open_feasible"] == "contradicted"

    # 变体 6：信号表改为当日可执行（信号与成交同一时钟），证据哈希清单保持一致。
    result = _admission_with_evidence(
        tmp_path,
        "v6",
        probe,
        signal_at=SYNTH_DAYS[1],
        executable_from=SYNTH_DAYS[1],
    )
    assert result["decision"] == "blocked"
    assert result["next_open_feasible"] == "contradicted"

    # 变体 7：哈希清单被替换为无关文件，实际消费的输入与成交文件未纳入校验。
    result = _admission_with_evidence(tmp_path, "v7", probe, replace_manifest=True)
    assert result["decision"] == "blocked"
    assert result["free_input_status"] == "evidence_invalid"

    # 变体 8：必需条目名齐全、路径在声明目录内、哈希自洽，但全部指向无关文件。
    result = _admission_with_evidence(tmp_path, "v8x", probe, point_at_unrelated=True)
    assert result["decision"] == "blocked"
    assert result["free_input_status"] == "evidence_invalid"

    # 变体 9：信号与成交均为空表——零有效匹配成交不构成次日成交证据。
    result = _admission_with_evidence(tmp_path, "v9", probe, empty_trades=True)
    assert result["decision"] == "blocked"
    assert result["next_open_feasible"] != "verified"

    # 变体 10：成交价格为 NaN——非有限数值不得通过价格核验。
    result = _admission_with_evidence(tmp_path, "v10", probe, fill_price=float("nan"))
    assert result["decision"] == "blocked"
    assert result["next_open_feasible"] == "contradicted"

    # 正例：证据与文件完全一致时证据校验通过（决策仍因引擎依赖未证而受阻）。
    result = _admission_with_evidence(tmp_path, "v8", probe)
    assert result["free_input_status"] == "verified_local_bundle"
    assert result["next_open_feasible"] == "verified"
    assert result["decision"] == "blocked"
    assert result["dependency_status"]["engine_python"] == "rqalpha_missing"


def test_probe_engine_cli_writes_admission(tmp_path: Path) -> None:
    out = tmp_path / "cli-out"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--out",
            str(out),
            "--data-root",
            str(tmp_path / "empty-data"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((out / "engine-admission.json").read_text(encoding="utf-8"))
    assert payload["decision"] == "blocked"
    # 已写出的目录不可复用，避免覆盖既有证据。
    completed_again = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--out",
            str(out),
            "--data-root",
            str(tmp_path / "empty-data"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed_again.returncode != 0


def _engine_python() -> Path | None:
    override = os.environ.get("HETU_ENGINE_PYTHON")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None
    default = (
        REPO_ROOT / ".hetu" / "quant" / "runtime" / "rqalpha" / ".venv" / "bin" / "python"
    )
    return default if default.exists() else None


@pytest.mark.skipif(
    _latest_admission_decision() != "candidate_ready",
    reason=(
        "真实引擎未准入（最新 engine-admission.json decision="
        f"{_latest_admission_decision()!r}，见 {PHASE2_QUALIFICATION}）："
        "显式受阻跳过，跳过本身不构成准入通过证据"
    ),
)
def test_local_bundle_reaches_real_rqalpha(tmp_path: Path) -> None:
    """合成证券／日历／原价 bar 经 runner CLI 走真实引擎；断言次日开盘成交与净值数值。

    事实：初资 10,000，零成本合成设定；d1 收盘信号目标权重 1.0，
    d2 开盘价 10.00 成交 1,000 股，d3 收盘价 12.00，期末净值 12,000。
    """
    engine_python = _engine_python()
    assert engine_python is not None
    bundle = tmp_path / "bundle"
    _write_real_bundle(bundle)
    out = tmp_path / "engine-out"
    completed = subprocess.run(
        [str(engine_python), str(RUNNER_PATH), "--bundle", str(bundle), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr

    # 九张固定轨迹表必须齐全且列定义与 backtest.TABLE_COLUMNS 一致。
    for name, columns in TABLE_COLUMNS.items():
        table = pd.read_parquet(out / f"{name}.parquet")
        assert list(table.columns) == list(columns)
    assert (out / "backtest.json").exists()

    trades = pd.read_parquet(out / "trades.parquet")
    assert len(trades) == 1
    fill = trades.iloc[0]
    # 次日语义：成交日必须是信号日的下一交易日，且价格为当日开盘价。
    assert fill["executed_at"] == SYNTH_DAYS[1]
    assert fill["code"] == "SYNTH"
    assert fill["side"] == "BUY"
    assert abs(fill["price_raw"] - SYNTH_OPEN_D2) <= 1e-9
    assert abs(fill["qty"] - 1000) <= 1e-9

    # 现金变化：d2 成交后可用现金 = 初资 − 成交价 × 成交数量（零成本）。
    cash = pd.read_parquet(out / "cash.parquet")
    after_fill = cash[cash["date"] == SYNTH_DAYS[1]].iloc[0]
    assert abs(after_fill["cash_available"] - (SYNTH_CASH - 1000 * SYNTH_OPEN_D2)) <= 0.01

    # 持仓与净值：d3 收盘持仓 1,000 股 × 12.00，期末净值 12,000。
    positions = pd.read_parquet(out / "positions.parquet")
    final_pos = positions[positions["date"] == SYNTH_DAYS[2]].iloc[0]
    assert final_pos["code"] == "SYNTH"
    assert abs(final_pos["qty"] - 1000) <= 1e-9
    assert abs(final_pos["price_raw"] - SYNTH_CLOSE_D3) <= 1e-9

    nav = pd.read_parquet(out / "nav.parquet")
    final = nav[nav["date"] == SYNTH_DAYS[2]].iloc[0]
    assert abs(final["net_nav"] - 1000 * SYNTH_CLOSE_D3) <= 0.01

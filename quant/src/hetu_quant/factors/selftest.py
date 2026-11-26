"""Read and retain actual test attempts outside the source tree."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

from .catalog import get_factor
from .core import FactorRequest, MissingFact, TeachingSource, parameter_identity, resolve_request


def _folder(root, factor_id, version):
    if any(not value or Path(value).name != value or value.startswith(".") for value in (factor_id, version)):
        raise ValueError("invalid factor or version")
    return Path(root) / "factor-selftests" / factor_id / version


def record_selftest(root, factor_id, version, parameters, case_id, **evidence):
    folder = _folder(root, factor_id, version)
    folder.mkdir(parents=True, exist_ok=True)
    row = dict(
        **evidence,
        factor_id=factor_id,
        version=version,
        parameters=parameters,
        case_id=case_id,
        executed_at=datetime.now(UTC).isoformat(),
    )
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(row, handle, ensure_ascii=False, allow_nan=False, default=str)
            handle.flush()
            temporary.replace(folder / f"{uuid4().hex}.json")
        finally:
            temporary.unlink(missing_ok=True)


def replay_request(row):
    """Read a complete archived request without running its formula."""
    execution = row.get("execution_parameters")
    if not isinstance(execution, dict):
        raise ValueError("execution_parameters 须包含实际计算参数")
    replay = row.get("replay")
    if not isinstance(replay, dict):
        raise ValueError("replay 缺少复算资料")
    try:
        day = date.fromisoformat(replay.get("trade_date"))
    except (TypeError, ValueError):
        raise ValueError("replay.trade_date 缺少有效观察日") from None
    try:
        cutoff = datetime.fromisoformat(replay.get("cutoff"))
    except (TypeError, ValueError):
        raise ValueError("replay.cutoff 缺少有效截止时点") from None
    if cutoff.utcoffset() is None:
        raise ValueError("replay.cutoff 须包含时区")
    subjects = replay.get("subjects")
    if (
        not isinstance(subjects, list)
        or not subjects
        or not all(isinstance(code, str) and code.strip() for code in subjects)
    ):
        raise ValueError("replay.subjects 缺少有效观察对象")
    tables = replay.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ValueError("replay.tables 缺少输入事实表")
    for table, records in tables.items():
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ValueError(f"replay.tables.{table} 须包含输入事实记录")
    definition = get_factor(row["factor_id"])
    request = FactorRequest(definition.factor_id, tuple(subjects), day, cutoff, execution)
    try:
        resolved = resolve_request(definition, request)
    except MissingFact as error:
        raise ValueError(f"execution_parameters.{error.field} {error}") from error
    if missing := resolved.parameters.keys() - execution.keys():
        raise ValueError("缺实际计算参数 " + ", ".join(f"execution_parameters.{key}" for key in sorted(missing)))
    return request


def replay_selftest(row):
    """Recalculate an offline receipt using its exact archived raw inputs and request."""
    from . import compute_factor

    request = replay_request(row)
    with TemporaryDirectory(prefix="hetu-factor-replay-") as directory:
        root = Path(directory)
        (root / "receipt.json").write_text(json.dumps(row["replay"], ensure_ascii=False), encoding="utf-8")
        return compute_factor(None, TeachingSource("receipt", root), request)


def read_selftest(root, factor_id, version, parameters):
    definition = get_factor(factor_id)
    identity = parameter_identity(parameters)
    basic, formulas = {}, {}
    for path in _folder(root, factor_id, version).glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if (row["factor_id"], row["version"]) != (factor_id, version):
                continue
            tested = parameter_identity(row.get("tested_parameters", row["parameters"]))
            case = row["case_id"]
            if case in definition.case_ids and tested == identity:
                collection, key = basic, case
            elif case.startswith("formula:") and (
                tested == identity or parameter_identity(row.get("execution_parameters", row["parameters"])) == identity
            ):
                collection, key = formulas, (case, tested)
            else:
                continue
            if key not in collection or row["executed_at"] > collection[key]["executed_at"]:
                row["receipt"] = str(path)
                row["matches_tested_parameters"] = tested == identity
                collection[key] = row
        except (ValueError, OSError, KeyError, TypeError):
            continue
    specific = list(formulas.values())
    attached = [row for row in specific if row["matches_tested_parameters"]]
    if any(row["status"] == "failed" for row in [*basic.values(), *attached]):
        status = "failed"
    elif all(basic.get(case, {}).get("status") == "passed" for case in definition.case_ids):
        status = "passed"
    else:
        status = "untested"
    return dict(
        status=status,
        cases=[basic[case] for case in definition.case_ids if case in basic]
        + sorted(specific, key=lambda row: (row["case_id"], row["executed_at"])),
        formula_cases=sum(row["status"] == "passed" for row in attached),
    )

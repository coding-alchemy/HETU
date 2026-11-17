"""04.1 冻结定义与结果索引。

定义保存时做 JSON 校验与深拷贝，写入 ``definitions/<id>.json`` 并登记 SQLite；
结果只从已发布产物 ``results/<id>/result.json`` 读取，索引不得伪造 ResultRef。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from hetu_data.catalog import DataTaskStore

from hetu_quant.backtest_models import (
    BacktestDefinition,
    BacktestRef,
    backtest_definition_from_dict,
    backtest_definition_to_dict,
)
from hetu_quant.models import (
    DataRequest,
    RankingDefinition,
    ResultRef,
    data_request_from_dict,
    data_request_to_dict,
    ranking_definition_from_dict,
    ranking_definition_to_dict,
    validate_json_value,
)

_DEFINITION_KINDS = ("data", "ranking", "backtest", "maintenance")


class ExperimentStore(DataTaskStore):
    """定义、提交与结果的持久化入口；root 为 .hetu/quant 或测试临时目录。"""

    # ---- 定义 ----

    def save_definition(
        self,
        kind: Literal["data", "ranking", "backtest", "maintenance"],
        definition: DataRequest | RankingDefinition | BacktestDefinition | Mapping,
    ) -> str:
        if kind not in _DEFINITION_KINDS:
            raise ValueError(f"unknown definition kind: {kind!r}")
        if kind == "maintenance":
            from hetu_data.updates import validate_update_plan

            validate_update_plan(definition)
            payload = definition
        elif kind == "data":
            if not isinstance(definition, DataRequest):
                raise TypeError("kind 'data' requires a DataRequest")
            payload: Mapping[str, object] = data_request_to_dict(definition)
        elif kind == "backtest":
            if not isinstance(definition, BacktestDefinition):
                raise TypeError("kind 'backtest' requires a BacktestDefinition")
            payload = backtest_definition_to_dict(definition)
        else:
            if not isinstance(definition, RankingDefinition):
                raise TypeError("kind 'ranking' requires a RankingDefinition")
            # B2：保存时把当时完整有效协议（默认配置 merge 覆盖参数）固化进定义，
            # 之后修改 ranking-defaults.json 不影响本定义的结果。
            from hetu_quant.ranking import freeze_ranking_protocol

            definition = freeze_ranking_protocol(definition)
            payload = ranking_definition_to_dict(definition)
        return self._save_payload(kind, payload)

    def list_definitions(self, kind: str | None = None) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT definition_id, kind, created_at FROM definitions "
                "WHERE ? IS NULL OR kind = ? ORDER BY created_at DESC, definition_id",
                (kind, kind),
            ).fetchall()
        return [dict(row) for row in rows]

    def read_definition(self, definition_id: str) -> DataRequest | RankingDefinition:
        path = self.definitions_dir / f"{definition_id}.json"
        if not path.exists():
            raise KeyError(f"unknown definition: {definition_id}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        payload = doc["definition"]
        validate_json_value(payload, "$.definition")
        if doc["kind"] == "maintenance":
            from hetu_data.updates import validate_update_plan

            validate_update_plan(payload)
            return payload
        if doc["kind"] == "data":
            return data_request_from_dict(payload)
        if doc["kind"] == "ranking":
            return ranking_definition_from_dict(payload)
        if doc["kind"] == "backtest":
            return backtest_definition_from_dict(payload)
        raise ValueError(f"unknown definition kind: {doc['kind']!r}")

    # ---- 结果（只读已发布产物，不伪造）----

    def get_result(self, result_id: str) -> ResultRef:
        manifest_path = self.results_dir / result_id / "result.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"result manifest missing: {result_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = manifest.get("status")
        if status not in ("complete", "limited"):
            raise ValueError(f"result {result_id} has unknown status: {status!r}")
        return ResultRef(result_id=result_id, root=self.root, status=status)

    def list_results(self) -> list[str]:
        found = []
        for child in sorted(self.results_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(".tmp-"):
                if (child / "result.json").exists():
                    found.append(child.name)
        return found

    # ---- 回测结果（只读已发布 backtests 目录，与一期 results 独立）----

    @property
    def backtests_dir(self) -> Path:
        return self.root / "backtests"

    def get_backtest(self, run_id: str) -> BacktestRef:
        manifest_path = self.backtests_dir / run_id / "backtest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"backtest manifest missing: {run_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = manifest.get("status")
        if status not in ("complete", "limited"):
            raise ValueError(f"backtest {run_id} has unknown status: {status!r}")
        return BacktestRef(run_id=run_id, root=self.root, status=status)

    def list_backtests(self) -> list[str]:
        if not self.backtests_dir.exists():
            return []
        found = []
        for child in sorted(self.backtests_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(".tmp-") and child.name != "_partial":
                if (child / "backtest.json").exists():
                    found.append(child.name)
        return found

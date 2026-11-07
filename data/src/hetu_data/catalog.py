"""共享定义与任务 SQLite 目录；保持历史 schema 及 JSON 封装。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hetu_data.models import validate_json_value

CN = ZoneInfo("Asia/Shanghai")


def _new_id(prefix):
    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


class CatalogStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.definitions_dir = self.root / "definitions"
        self.results_dir = self.root / "results"
        self.definitions_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "catalog.sqlite"
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS definitions (
                    definition_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    definition_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    definition_id TEXT NOT NULL,
                    submission_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    progress REAL NOT NULL,
                    reason TEXT,
                    output TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _save_payload(self, kind, payload):
        validate_json_value(payload, "$.definition")
        copied = json.loads(json.dumps(payload, ensure_ascii=False))
        definition_id = _new_id("def")
        path = self.definitions_dir / f"{definition_id}.json"
        path.write_text(
            json.dumps(
                {"definition_id": definition_id, "kind": kind, "definition": copied},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO definitions VALUES (?, ?, ?, ?)",
                (definition_id, kind, str(path), datetime.now(CN).isoformat()),
            )
        return definition_id

    def definition_kind(self, definition_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT kind FROM definitions WHERE definition_id = ?",
                (definition_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown definition: {definition_id}")
        return str(row["kind"])

    def _read_payload(self, definition_id):
        path = self.definitions_dir / f"{definition_id}.json"
        if not path.exists():
            raise KeyError(f"unknown definition: {definition_id}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        validate_json_value(doc["definition"], "$.definition")
        return doc["definition"]


class DataTaskStore(CatalogStore):
    def save_definition(self, kind, definition):
        if kind != "maintenance":
            raise ValueError(f"unsupported data task kind: {kind!r}")
        from hetu_data.updates import validate_update_plan

        validate_update_plan(definition)
        return self._save_payload(kind, definition)

    def read_definition(self, definition_id):
        if self.definition_kind(definition_id) != "maintenance":
            raise ValueError("only maintenance definitions can be decoded")
        from hetu_data.updates import validate_update_plan

        payload = self._read_payload(definition_id)
        validate_update_plan(payload)
        return payload

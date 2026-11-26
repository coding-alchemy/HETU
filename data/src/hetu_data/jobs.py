"""04.1 任务提交、查询、列表、取消与显式重试。

同一 submission_id 只防一次点击重发：同定义返回原任务，异定义拒绝。
重试仅接受 failed/cancelled/interrupted，生成新任务号、新提交标识，
尝试数加一，保留父任务关联与原记录。列表按建立顺序倒排。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hetu_data.catalog import CatalogStore
from hetu_data.models import JOB_STATES, JobRecord

CN = ZoneInfo("Asia/Shanghai")

_ACTIVE_STATES = ("queued", "running", "cancel_requested")
_RETRYABLE_STATES = ("failed", "cancelled", "interrupted")
_JOB_RECORDS = "SELECT jobs.*, job_lineage.retry_of_job_id FROM jobs LEFT JOIN job_lineage USING (job_id)"


def _new_job_id() -> str:
    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    return f"job-{stamp}-{uuid.uuid4().hex[:8]}"


class JobService:
    """以 ExperimentStore 的 SQLite 索引持久化 JobRecord。"""

    def __init__(self, store: CatalogStore) -> None:
        self.store = store

    def _connect(self) -> sqlite3.Connection:
        return self.store._connect()

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=str(row["job_id"]),
            definition_id=str(row["definition_id"]),
            submission_id=str(row["submission_id"]),
            attempt=int(row["attempt"]),
            state=str(row["state"]),
            progress=float(row["progress"]),
            reason=row["reason"],
            output=Path(row["output"]) if row["output"] else None,
            retry_of_job_id=row["retry_of_job_id"],
        )

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as conn:
            return self._row(conn, job_id)

    def list(self) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                f"{_JOB_RECORDS} ORDER BY jobs.created_at DESC, jobs.rowid DESC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def original_submission_id(self, job_id: str) -> str | None:
        """沿已知父链查找由 submissions 佐证的首次提交。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                WITH RECURSIVE ancestors(job_id, retry_of_job_id) AS (
                    SELECT jobs.job_id, job_lineage.retry_of_job_id
                    FROM jobs LEFT JOIN job_lineage USING (job_id)
                    WHERE jobs.job_id = ?
                    UNION
                    SELECT parent.job_id, job_lineage.retry_of_job_id
                    FROM ancestors JOIN jobs AS parent ON parent.job_id = ancestors.retry_of_job_id
                    LEFT JOIN job_lineage ON job_lineage.job_id = parent.job_id
                )
                SELECT jobs.submission_id FROM ancestors JOIN jobs USING (job_id)
                JOIN submissions ON submissions.submission_id = jobs.submission_id
                    AND submissions.definition_id = jobs.definition_id
                WHERE ancestors.retry_of_job_id IS NULL AND jobs.attempt = 1
                """,
                (job_id,),
            ).fetchone()
        return str(row["submission_id"]) if row is not None else None

    def _insert_submission(
        self, conn: sqlite3.Connection, submission_id: str, definition_id: str
    ) -> None:
        conn.execute(
            "INSERT INTO submissions VALUES (?, ?)", (submission_id, definition_id)
        )

    def submit(self, definition_id: str, submission_id: str) -> JobRecord:
        self.store.definition_kind(definition_id)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT definition_id FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["definition_id"]) != definition_id:
                    raise ValueError(
                        f"submission {submission_id!r} already bound to definition "
                        f"{existing['definition_id']!r}"
                    )
                row = conn.execute(
                    f"{_JOB_RECORDS} WHERE jobs.submission_id = ?", (submission_id,)
                ).fetchone()
                return self._record(row)
            now = datetime.now(CN).isoformat()
            job_id = _new_job_id()
            try:
                self._insert_submission(conn, submission_id, definition_id)
            except sqlite3.IntegrityError:
                raise ValueError(
                    f"submission {submission_id!r} already bound to another definition"
                ) from None
            conn.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    definition_id,
                    submission_id,
                    1,
                    "queued",
                    0.0,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            conn.execute("INSERT INTO job_lineage (job_id) VALUES (?)", (job_id,))
            return self._row(conn, job_id)

    def retry(self, job_id: str, submission_id: str) -> JobRecord:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = self._row(conn, job_id)
            if parent.state not in _RETRYABLE_STATES:
                raise ValueError(f"job {job_id} in state {parent.state!r} is not retryable")
            clash = conn.execute(
                "SELECT definition_id FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if clash is not None:
                raise ValueError(
                    f"submission {submission_id!r} already bound to definition "
                    f"{clash['definition_id']!r}"
                )
            now = datetime.now(CN).isoformat()
            new_job_id = _new_job_id()
            try:
                self._insert_submission(conn, submission_id, parent.definition_id)
            except sqlite3.IntegrityError:
                raise ValueError(
                    f"submission {submission_id!r} already used"
                ) from None
            conn.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_job_id,
                    parent.definition_id,
                    submission_id,
                    parent.attempt + 1,
                    "queued",
                    0.0,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            conn.execute("INSERT INTO job_lineage VALUES (?, ?)", (new_job_id, parent.job_id))
            return self._row(conn, new_job_id)

    def cancel(self, job_id: str) -> JobRecord:
        """queued 直接 cancelled；running 记 cancel_requested；终态返回原记录。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            state = str(row["state"])
            now = datetime.now(CN).isoformat()
            if state == "queued":
                conn.execute(
                    "UPDATE jobs SET state = 'cancelled', updated_at = ?"
                    " WHERE job_id = ?",
                    (now, job_id),
                )
            elif state == "running":
                conn.execute(
                    "UPDATE jobs SET state = 'cancel_requested', updated_at = ?"
                    " WHERE job_id = ? AND state = 'running'",
                    (now, job_id),
                )
        return self.get(job_id)

    # ---- worker 内部事务接口 ----

    def _row(self, conn: sqlite3.Connection, job_id: str) -> JobRecord:
        row = conn.execute(
            f"{_JOB_RECORDS} WHERE jobs.job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        return self._record(row)

    def _update(
        self,
        job_id: str,
        *,
        state: str | None = None,
        progress: float | None = None,
        reason: str | None = None,
        output: Path | None = None,
    ) -> JobRecord:
        if state is not None and state not in JOB_STATES:
            raise ValueError(f"unknown job state: {state!r}")
        with self._connect() as conn:
            fields = ["updated_at = ?"]
            values: list[object] = [datetime.now(CN).isoformat()]
            if state is not None:
                fields.append("state = ?")
                values.append(state)
            if progress is not None:
                fields.append("progress = ?")
                values.append(progress)
            if reason is not None:
                fields.append("reason = ?")
                values.append(reason)
            if output is not None:
                fields.append("output = ?")
                values.append(str(output))
            values.append(job_id)
            conn.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", values
            )
            return self._row(conn, job_id)

    def claim(self, job_id: str) -> JobRecord | None:
        """事务按 queued 条件领取；返回 None 表示被他人领取或不可领取。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE jobs SET state = 'running', progress = 0.1, updated_at = ?"
                " WHERE job_id = ? AND state = 'queued'",
                (datetime.now(CN).isoformat(), job_id),
            )
            if cursor.rowcount != 1:
                conn.commit()
                return None
            record = self._row(conn, job_id)
            conn.commit()
            return record
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish(
        self,
        job_id: str,
        *,
        progress: float,
        output: Path | None,
        reason: str | None = None,
    ) -> JobRecord:
        """完成事务仅接受仍为 running 的任务，不能覆盖已收到的取消请求。"""
        now = datetime.now(CN).isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE jobs SET state = 'succeeded', progress = ?, output = ?,"
                " reason = ?, updated_at = ?"
                " WHERE job_id = ? AND state = 'running'",
                (progress, str(output) if output else None, reason, now, job_id),
            )
            if cursor.rowcount != 1:
                conn.execute(
                    "UPDATE jobs SET state = 'cancelled', updated_at = ?"
                    " WHERE job_id = ? AND state = 'cancel_requested'",
                    (now, job_id),
                )
            record = self._row(conn, job_id)
            conn.commit()
            return record
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finalize_cancel(self, job_id: str) -> JobRecord:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state = 'cancelled', updated_at = ?"
                " WHERE job_id = ? AND state = 'cancel_requested'",
                (datetime.now(CN).isoformat(), job_id),
            )
            return self._row(conn, job_id)

    def fail(self, job_id: str, reason: str) -> JobRecord:
        now = datetime.now(CN).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET state = 'failed', reason = ?, updated_at = ?"
                " WHERE job_id = ? AND state = 'running'",
                (reason, now, job_id),
            )
            if cursor.rowcount != 1:
                conn.execute(
                    "UPDATE jobs SET state = 'cancelled', updated_at = ?"
                    " WHERE job_id = ? AND state = 'cancel_requested'",
                    (now, job_id),
                )
            return self._row(conn, job_id)


def submit_update_plan(tasks, plan: dict, submission_id: str) -> JobRecord:
    """找回同次点击冻结的定义；新点击显式生成新任务。"""
    import fcntl

    from hetu_data.updates import validate_update_plan

    validate_update_plan(plan)
    # Submission coordination must not contend with the resident data writer.
    # Closing the handle releases this short cross-process/thread mutex.
    with (tasks.root / "submission.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        with tasks._connect() as conn:
            row = conn.execute(
                'SELECT definition_id FROM submissions WHERE submission_id = ?',
                (submission_id,),
            ).fetchone()
        if row is not None:
            definition_id = str(row['definition_id'])
            if tasks.read_definition(definition_id) != plan:
                raise ValueError('submission already bound to a different plan')
        else:
            definition_id = tasks.save_definition('maintenance', plan)
        return JobService(tasks).submit(definition_id, submission_id)

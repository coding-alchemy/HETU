"""One in-flight factor call per Streamlit session."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from hetu_quant.factors.core import CN, FactorResult
from hetu_quant.factors.workbench import FrozenTrial


@dataclass
class Attempt:
    id: str
    trial: FrozenTrial
    submitted_at: datetime
    state: str = "running"
    finished_at: datetime | None = None
    result: FactorResult | None = None
    error: str | None = None


class SessionRuns:
    def __init__(self, attempts: dict[str, Attempt]):
        self.attempts = attempts
        self.future: Future | None = None
        self.active_id: str | None = None

    def start(self, trial: FrozenTrial, compute: Callable[[FrozenTrial], FactorResult]) -> Attempt:
        if self.active_id is not None:
            return self.attempts[self.active_id]
        attempt = Attempt(uuid4().hex, trial, datetime.now(CN))
        self.attempts[attempt.id] = attempt
        self.active_id = attempt.id
        # Shutdown without waiting so the worker exits after this one task even if the session closes.
        with_nonblocking = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hetu-factor")
        try:
            self.future = with_nonblocking.submit(compute, trial)
        finally:
            with_nonblocking.shutdown(wait=False)
        return attempt

    def collect(self) -> None:
        future = self.future
        if future is None or not future.done():
            return
        attempt = self.attempts[self.active_id]
        try:
            attempt.result = future.result()
        except (OSError, ValueError, KeyError):
            attempt.state = "failed"
            attempt.error = "无法读取所选输入。请核对固定发布、范围和使用许可，或按原配置重试。"
        except Exception:
            attempt.state = "failed"
            attempt.error = "此次试算失败。请重试或调整配置。"
        else:
            attempt.state = "completed"
        finally:
            attempt.finished_at = datetime.now(CN)
            self.future = None
            self.active_id = None

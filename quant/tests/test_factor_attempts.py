from dataclasses import replace
from threading import Event

import pytest

from factor_cases import example_query
from hetu_quant.factors import compute_factor
from hetu_quant.factors.workbench import build_trial


def test_delayed_result_belongs_to_first_attempt():
    from hetu_quant.ui.factor_attempts import SessionRuns

    source, _, request = example_query("price_momentum")
    first_trial = build_trial(source, request)
    second_trial = build_trial(source, replace(request, parameters={"skip": 0}))
    entered, release = Event(), Event()
    calls, attempts = [], {}

    def observed(trial):
        calls.append(trial)
        if trial == first_trial:
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release trial")
        return compute_factor(None, trial.source, trial.request)

    runs = SessionRuns(attempts)
    first = runs.start(first_trial, observed)
    try:
        assert entered.wait(3)
        assert runs.start(first_trial, observed).id == first.id
        assert runs.start(second_trial, observed).id == first.id
        runs.collect()
        assert first.result is None
        assert calls == [first_trial]
    finally:
        release.set()
    runs.future.result(timeout=3)
    runs.collect()
    second = runs.start(second_trial, observed)
    runs.future.result(timeout=3)
    runs.collect()
    assert first.result.rows[0]["value"] == pytest.approx(0.2)
    assert second.result.rows[0]["value"] == pytest.approx(1.5)
    assert first.id != second.id and calls == [first_trial, second_trial]


def test_retry_after_read_failure_keeps_failed_attempt():
    from hetu_quant.ui.factor_attempts import SessionRuns

    source, _, request = example_query("price_momentum")
    trial = build_trial(source, request)
    runs = SessionRuns({})

    def unavailable(trial):
        raise OSError("raw supplier diagnostic: this must not be visible")

    first = runs.start(trial, unavailable)
    with pytest.raises(OSError):
        runs.future.result(timeout=3)
    runs.collect()
    assert first.state == "failed" and first.result is None
    assert "raw supplier diagnostic" not in first.error
    second = runs.start(first.trial, lambda q: compute_factor(None, q.source, q.request))
    runs.future.result(timeout=3)
    runs.collect()
    assert first.id != second.id and first.state == "failed"
    assert second.result.rows[0]["value"] == pytest.approx(0.2)

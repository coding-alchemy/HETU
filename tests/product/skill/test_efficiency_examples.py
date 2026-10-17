import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

DOCUMENT = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/references/host-tools.md"
)


@pytest.fixture
def examples():
    namespaces = {}
    for token in MarkdownIt().parse(DOCUMENT.read_text(encoding="utf-8")):
        if token.type == "fence" and token.info.startswith("python hetu-example="):
            name = token.info.split("=", 1)[1].strip()
            assert name not in namespaces
            namespace = {}
            exec(compile(token.content, str(DOCUMENT), "exec"), namespace)
            namespaces[name] = namespace
    return namespaces


def test_capture_preserves_bytes_and_does_not_repeat_fetch(examples, tmp_path):
    calls = []
    payload = b'{"source_time":"2026-06-30","value":17}\n'

    async def fetch():
        calls.append("fetch")
        return payload

    output = tmp_path / "raw.json"
    receipt = asyncio.run(examples["capture"]["capture_once"](fetch, output))
    assert calls == ["fetch"]
    assert output.read_bytes() == payload
    assert receipt["saved"] is True
    assert receipt["bytes"] == len(payload)
    assert receipt["received_at"]
    with pytest.raises(FileExistsError):
        asyncio.run(examples["capture"]["capture_once"](fetch, output))
    assert calls == ["fetch"]


def test_capture_save_failure_is_not_adoption(examples, tmp_path):
    async def fetch():
        return b"source bytes"

    output = tmp_path / "missing-parent" / "raw.txt"
    receipt = asyncio.run(examples["capture"]["capture_once"](fetch, output))
    assert receipt["acquired"] is True and receipt["saved"] is False
    assert receipt["failed_step"] == "save"
    assert "adopted" not in receipt and "exit_status" not in receipt
    assert not output.exists()


def test_capture_fetch_failure_does_not_write(examples, tmp_path):
    async def fetch():
        raise PermissionError("fixture permission refusal")

    output = tmp_path / "raw.txt"
    receipt = asyncio.run(examples["capture"]["capture_once"](fetch, output))
    assert receipt["failed_step"] == "fetch"
    assert receipt["acquired"] is False and receipt["saved"] is False
    assert not output.exists()


@pytest.mark.parametrize("returncode", [0, 1, 2])
def test_checker_result_is_read_only_for_this_successful_write(
    examples, tmp_path, monkeypatch, returncode
):
    output = tmp_path / "check.json"
    payload = {"mechanical_status": "PASS" if returncode == 0 else "FAIL", "issues": []}

    def run(args, **kwargs):
        if returncode in (0, 1):
            output.write_text(json.dumps(payload), encoding="utf-8")
        else:
            output.write_text("not valid json", encoding="utf-8")
        return subprocess.CompletedProcess(args, returncode, "", "fixture stderr")

    monkeypatch.setattr(examples["check"]["subprocess"], "run", run)
    result = examples["check"]["check_once"](
        Path("python"), Path("checker.py"), tmp_path / "research",
        tmp_path / "message.md", tmp_path / "lock.json", output,
    )
    assert result["returncode"] == returncode
    assert result["result"] == (payload if returncode in (0, 1) else None)


def test_checker_does_not_read_or_replace_an_old_result(examples, tmp_path):
    output = tmp_path / "old.json"
    output.write_text('{"mechanical_status":"PASS"}', encoding="utf-8")
    with pytest.raises(FileExistsError):
        examples["check"]["check_once"](
            Path("python"), Path("checker.py"), tmp_path / "research",
            tmp_path / "message.md", tmp_path / "lock.json", output,
        )
    assert json.loads(output.read_text())["mechanical_status"] == "PASS"


def test_mirror_only_changes_the_unique_explicit_value(examples, tmp_path):
    target = tmp_path / "checkpoint.md"
    target.write_text("技术：未检查\n实际深度：未达到 quick\n", encoding="utf-8")
    examples["mirror"]["replace_one"](target, "技术：未检查", "技术：已检查", locked=False)
    assert target.read_text() == "技术：已检查\n实际深度：未达到 quick\n"


@pytest.mark.parametrize(
    "text,old,locked",
    [("x\n", "x", True), ("x\nx\n", "x", False), ("y\n", "x", False)],
)
def test_mirror_rejects_locked_or_conflicting_targets(examples, tmp_path, text, old, locked):
    target = tmp_path / "checkpoint.md"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        examples["mirror"]["replace_one"](target, old, "new", locked=locked)
    assert target.read_text() == text

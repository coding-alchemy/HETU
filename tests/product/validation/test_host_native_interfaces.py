"""阶段 07.1b：宿主原生五接口事实表的受控记录与文档合同检查。

事实记录（支持／不支持／未验证）以 ``native-interfaces.json`` 为受控机读记录，
``skills/hetu-stock-analysis/references/host-tools.md`` 的“宿主原生接口事实记录”
段落为同一事实的人工可读表。本测试离线断言：

- 记录结构完整：四宿主 × 六接口（压缩、取消、恢复、委派、用量、中途自然语言控制），
  判定只取三值，支持／不支持必须有证据定位，无证据一律未验证；
- claude 打印模式明确记“不支持控制”，绝不因能运行打印模式被认证为支持；
- host-tools.md 的事实表与机读记录逐项一致（文档漂移即失败）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "host_cli"
RECORD_PATH = FIXTURES_DIR / "native-interfaces.json"
HOST_TOOLS_PATH = (
    Path(__file__).resolve().parents[3]
    / "skills/hetu-stock-analysis/references/host-tools.md"
)
KNOWN_HOSTS = ("codex", "claude", "opencode", "zcode")
VERDICT_TO_CN = {"supported": "支持", "unsupported": "不支持", "unverified": "未验证"}
CN_TO_VERDICT = {value: key for key, value in VERDICT_TO_CN.items()}


def _load_record() -> dict[str, Any]:
    payload = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _doc_table_rows() -> dict[str, dict[str, str]]:
    text = HOST_TOOLS_PATH.read_text(encoding="utf-8")
    section_match = re.search(
        r"## 宿主原生接口事实记录（阶段 07\.1b）(.*?)\n## ", text, re.DOTALL
    )
    assert section_match, "host-tools.md 缺少 07.1b 宿主原生接口事实记录段落"
    section = section_match.group(1)
    header = re.search(
        r"^\| 宿主 \| 压缩 \| 取消 \| 恢复 \| 委派 \| 用量 \| 中途自然语言控制 \|$",
        section,
        re.MULTILINE,
    )
    assert header, "事实表缺少规定表头"
    rows: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 7 or cells[0] in ("宿主", "---") or cells[0].startswith("-"):
            continue
        host, *verdicts = cells
        rows[host] = dict(zip(RECORD_INTERFACES, verdicts, strict=True))
    return rows


RECORD_INTERFACES = ("压缩", "取消", "恢复", "委派", "用量", "中途自然语言控制")


def test_record_covers_all_hosts_and_interfaces() -> None:
    record = _load_record()
    assert record["schema_version"] == "1.0"
    assert record["recorded_at"]
    assert set(record["hosts"]) == set(KNOWN_HOSTS)
    assert record["interfaces"] == [
        "compaction",
        "cancel",
        "resume",
        "delegation",
        "usage",
        "mid_run_control",
    ]
    for host in KNOWN_HOSTS:
        for interface in record["interfaces"]:
            entry = record["hosts"][host][interface]
            assert entry["verdict"] in record["verdicts"], (
                f"{host}.{interface}: 非法判定 {entry['verdict']!r}"
            )
            if entry["verdict"] in ("supported", "unsupported"):
                assert entry["evidence"], (
                    f"{host}.{interface}: 支持／不支持判定必须给出证据定位"
                )
            else:
                assert entry["verdict"] == "unverified"


def test_claude_print_mode_is_not_certified_as_control() -> None:
    record = _load_record()
    control = record["hosts"]["claude"]["mid_run_control"]
    assert control["verdict"] == "unsupported", (
        "claude 打印模式没有中途自然语言控制，必须记 不支持控制，"
        "不得因能运行打印模式认证为支持"
    )
    assert "打印模式" in control["note"]


def test_doc_table_matches_controlled_record() -> None:
    record = _load_record()
    rows = _doc_table_rows()
    assert set(rows) == set(KNOWN_HOSTS), "事实表必须逐宿主一行，且只含四宿主"
    for host in KNOWN_HOSTS:
        for interface, label in zip(
            record["interfaces"], RECORD_INTERFACES, strict=True
        ):
            expected = VERDICT_TO_CN[record["hosts"][host][interface]["verdict"]]
            actual = rows[host][label]
            assert actual in CN_TO_VERDICT, f"{host}.{label}: 非法判定文本 {actual!r}"
            assert actual == expected, (
                f"{host}.{label}: 文档记 {actual}，机读记录记 {expected}——"
                "请同步 host-tools.md 与 native-interfaces.json"
            )

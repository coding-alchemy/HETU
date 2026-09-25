"""阶段 07.1b：原生适配器约束的静态与文档合同检查。

计划 07.1 条目 2 要求：原生适配仅发送请求、观察和转达用户操作，不能循环驱动
W0–W10、路由模型或注入研究下一步；同一检查器路径必须在本地与 CI 同时生效
（本文件位于 tests/product/validation，由 scripts/check.sh 统一收集）。

本测试不启动任何宿主或模型，只做离线静态断言：

- 模块文档字符串明确声明适配器约束（文档合同）；
- host_acceptance.py 只依赖标准库，不可能导入工作流编排或模型路由代码
  （import 边界）；
- 控制转达协议只接受已记录的 ``watch`` / ``finalize`` 操作；
- 适配器源码不含 W0–W10 驱动、模型路由或研究下一步注入的标识符。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "host_acceptance.py"
)
SOURCE = SCRIPT_PATH.read_text(encoding="utf-8")
DOCSTRING = ast.get_docstring(ast.parse(SOURCE)) or ""


def test_module_docstring_declares_native_adapter_constraint() -> None:
    normalized = " ".join(DOCSTRING.split())
    for marker in (
        "only sends the explicitly approved request",
        "observes",
        "relays user/operator operations",
        "never loop-drives the W0–W10 work packages",
        "never routes or selects models",
        "never injects research next steps",
    ):
        assert marker in normalized, f"模块文档缺少适配器约束声明: {marker!r}"


def test_host_acceptance_imports_are_stdlib_only() -> None:
    tree = ast.parse(SOURCE)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    non_stdlib = sorted(roots - set(sys.stdlib_module_names))
    assert non_stdlib == [], (
        "host_acceptance.py 只允许标准库依赖（原生适配不得导入工作流编排、"
        f"模型路由或研究逻辑），发现: {non_stdlib}"
    )


def test_control_relay_accepts_only_recorded_ops() -> None:
    tree = ast.parse(SOURCE)
    accepted: set[str | None] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "op":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant):
                accepted.add(comparator.value if isinstance(comparator.value, str) else None)
            else:
                accepted.add(None)
    assert accepted, "未找到控制操作判定，适配器约束无法核实"
    assert accepted == {"watch", "finalize"}, (
        f"控制转达协议只允许 watch/finalize，发现: {accepted}"
    )


def test_native_adapter_has_no_workflow_driver_identifiers() -> None:
    forbidden_tokens = (
        "W0–W10",
        "route_model",
        "select_model",
        "next_work_package",
        "inject_next_step",
    )
    body = SOURCE.replace(DOCSTRING, "", 1) if DOCSTRING else SOURCE
    for token in forbidden_tokens:
        assert token not in body, (
            f"适配器源码出现工作流驱动/模型路由/研究注入标识符: {token!r}"
        )

# 阶段 02：扩展边界、flag 解析与历史审计可移植性

> 文档版本：v1.0
> 文档状态：已执行完成（2026-09-24，提交 dc2ca33）；结果见文末补记
> 创建／修订日期：2026-09-24
> 范围：5-A1/A2/B1、6-B1、8-B1
> 依据：[路线图](README.md)、[修复设计](../../2026-09-24-zn-dev-remaining-groups-fix-design.md)
> 前序：[阶段 01](2026-09-24-01-metering-integrity.md)；本阶段不依赖其新接口。

## 1. 扩展版本、登记表与测试隔离

文件：`src/hetu_stock/skill/extensions.py`、`tests/product/skill/test_extensions.py`、
`tests/product/cli/test_extension_cli.py`。复用 build_extension_package、CliRunner 与
managed_home；所有 validate 路径使用 tmp_path 的 XDG_DATA_HOME，不读取真实登记表。

- [ ] 为全部扩展单测设置隔离根；用临时空登记表与包含所需 provide 的登记表验证依赖
  判断仍正确。测试互不借用其他用例安装状态，CLI 原有隔离行为保留。
- [ ] 补默认版本行为用例并确认旧实现失败：0.9.0→0.10.0 后 enable/inspect 选 0.10.0，
  再 update 0.11.0 报 previous=0.10.0；update 不改变既有宿主绑定；显式旧版可选，
  未登记版本受控拒绝；release-9/release-10 与数字文字混合比较不崩溃。
- [ ] 按设计 §5-A1 的自然排序约定复用一个排序键，替换默认版本与 previous_version
  的字典序选择；不改版本合法性、不引入 SemVer 依赖、不宣称预发布优先级。
- [ ] 参数化登记表负例：bindings=null、宿主绑定非对象、字符串 binding、缺/错类型 version、
  extensions 非对象、扩展/versions/版本记录非对象及空版本集合。通过 list/context/
  disable 和受影响的默认选择入口验证错误原因与不写回；相关字段按实际下游使用验证。
- [ ] `_read_registry` 在消费和写回前受控校验，保留合法缺省宿主绑定语义；不自动删改坏表。
  CLI 不打印正文或秘密，不把所有异常吞为成功。共用断言须包含：

```python
before = registry_path.read_bytes()
result = runner.invoke(app, command_args)
assert result.exit_code == 1
assert "Traceback" not in result.output
assert registry_path.read_bytes() == before
```

同时断言 JSON/文本失败说明中存在准确错误位置。路径与命令参数使用测试临时根。

## 2. flag 解析与策展合同

修改 `scripts/capture_host_cli.py`，新增
`tests/product/validation/test_capture_host_cli.py`，不修改或重新捕获现有宿主夹具。

- [ ] 建立合成帮助输入：`  -h, --help  Show help`、`  -m, --model <MODEL>  Choose model`、
  描述含 `--example`、Usage/示例行、独立长选项及仅短选项。断言只取选项声明和别名。
- [ ] 旧解析器对别名遗漏先失败，再只修 `_observed_flags` 的选项声明解析。
  首次初始化使用完整提取；描述区、参数值与示例不参与提取。
- [ ] mock subprocess/shutil 使 capture_host 使用合成 help/version；首次无策展结果能初始化；
  已有非空 accepted 子集刷新后原样保留，checked_absent 原样保留且不能包含实际声明选项。
  不增加“所有 help flag 必须进入人工白名单”的门禁，不调用真实宿主二进制。

## 3. 七项历史审计条件跳过

只改 `tests/product/skill/test_phase5_parallel_contract.py` 中
`test_impact_map_writeback_is_append_only_new_section` 与六个 `test_writeback_*` 的
缺证据处理，保留全部原断言。活功能、R1 及十二夹具测试不加 skip。

- [ ] 缺少指定历史 impact-map 时，仅这七项带明确“本机历史证据缺失”原因跳过；文件
  存在但无法读、结构损坏或断言失败时仍失败。不要用捕获任意异常的方式跳过。
- [ ] 不移动真实 `.hetu/`；无证据及损坏证据测试在 03 的临时副本完成。本机已有证据时，
  定向测试仍执行历史审计；不宣称由此使历史性能通过。

## 4. 直接回归与交付

```bash
.venv/bin/python -m pytest -q tests/product/skill/test_extensions.py tests/product/cli/test_extension_cli.py tests/product/validation/test_capture_host_cli.py tests/product/skill/test_phase5_parallel_contract.py
.venv/bin/python -m ruff check src/hetu_stock/skill/extensions.py scripts/capture_host_cli.py tests/product/skill/test_extensions.py tests/product/cli/test_extension_cli.py tests/product/validation/test_capture_host_cli.py tests/product/skill/test_phase5_parallel_contract.py
git diff --check
```

- [ ] 检查上述文件实际变化；5-B2 死代码、5-B3 完整性边界不借修复重写，workflow 不变。
- [ ] 计划获批实施后，按文件精确暂存并本地提交；记录修复矩阵结果与 SHA。可在阶段文件
  追加简短实际结果，不另建冗余过程规格。

继续已获批的 03，或按用户批准范围停下。不得通过删除测试或清理真实登记表使回归通过。

## 5. 执行结果补记（2026-09-24，提交 dc2ca33）

- 5-A1：`_version_sort_key`/`_latest_version` 共用排序键接入 enable/inspect 默认选择与
  update previous_version；0.9.0→0.10.0→0.11.0、release-9/release-10、01.0/1.0 平局
  行为固定；显式旧版可选、未登记版本拒绝、update 不动既有绑定。
- 5-A2：`_read_registry` 在任何消费/写回前经 `_check_registry_shape` 校验；10 项参数化
  负例（bindings=null、宿主绑定非对象、裸字符串、缺/错类型 version、extensions/扩展/
  versions/版本记录非对象、空版本集合）均 exit 1、无 Traceback、含准确位置、字节不变。
- 5-B1：`test_extensions.py` 全部用例 autouse 隔离 XDG_DATA_HOME；同一假登记表正反
  两态各验证一次依赖判断。
- 6-B1：`_observed_flags` 只取选项声明区（短长别名、排除描述/参数值/示例）；新增
  `test_capture_host_cli.py`（合成文本＋真实夹具只读核对＋mock 捕获/刷新）。
- 8-B1：七项 writeback 测试加带理由 `skipif(not IMPACT_MAP.exists())`；本机有证据时
  全部照常执行通过。

先红证据：旧实现 git archive 副本（PYTHONPATH 钉住快照 src）——5-A1 2 项、5-A2 10 项、
6-B1 3 项失败；5-B1 为测试隔离修复（红证据是设计已实证的本机假表干扰），01.0/1.0 平局
用例在旧实现通过属保留边界。定向回归 105 passed, 1 skipped（zcode 夹具 unavailable，
带理由宿主可用性 skip）；ruff 通过；`git diff --check` 通过。

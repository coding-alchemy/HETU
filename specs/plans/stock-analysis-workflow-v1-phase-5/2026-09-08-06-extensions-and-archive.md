# 五期阶段 06：扩展管理与旧成果读取

> 文档版本：v1.0
> 文档状态：已批准，尚未执行；用户明确要求暂不执行
> 批准日期：2026-09-08
> 创建／修订日期：2026-09-08
> 适用范围：第三方规则资料包、按宿主生命周期、自然语言管理及旧格式读取导出
> 上级：[路线图](README.md)；前序：[03](2026-09-08-03-control-context-reuse.md)、[05](2026-09-08-05-installation-and-recovery.md)
> 设计依据：[§4、§5、§7](../../2026-09-06-stock-analysis-workflow-v1-phase-5-design.md)
> 执行方式：使用 `superpowers:executing-plans`；两个子系统分别提交测试和评审结果。

## 1. 前置条件与范围映射

扩展开放依赖核心 W0–W10 稳定、03 产物合同、05 原子维护及目标宿主相关安全行为。旧成果
读取不依赖性能或扩展，可独立实施。此阶段不建市场、不引入第三方程序运行、不恢复旧状态机。

| 要求 | 任务 | 直接验证 |
|---|---|---|
| 长期 3.2.1–2、5–6 | 06.1 | 默认禁用、独立命名空间、按宿主绑定、原子更新及回滚 |
| 长期 3.2.3–4、7 | 06.1、06.2 | 硬依赖／权限失败关闭，不执行附带代码，Agent 决定适用与采用 |
| 长期 3.2.8 及管理完成标准 | 06.2 | 自然语言与 CLI 实际状态一致、纯管理不启动研究 |
| 长期 3.2.5 及采用标准、设计 4.3 | 06.2 | 本次加载与采用可追溯、核心 owner 不被覆盖 |
| 长期 3.5 全部 | 06.3 | schema 3 原字段、未知保留、安全导出、源字节不变 |

## 2. 文件、接口与最小布局

新增 `src/hetu_stock/skill/extensions.py`、`src/hetu_stock/helpers/archive.py`；修改
`src/hetu_stock/cli.py`、`src/hetu_stock/helpers/__init__.py`、`src/hetu_stock/skill/work_packages.py`、
`skills/hetu-stock-analysis/references/artifact-contract.md`、`work-package-result.md`、
`host-tools.md`、`checkpoint.md`、`report-guidance.md`、`scripts/check-run-artifacts.py` 及必要
W0／W10 入口。后三类 Skill 路径均在 `skills/hetu-stock-analysis/` 内。
更新 `docs/agent-skill-usage.md`、CLI 帮助、包清单；不扩大 canonical 的单股触发范围。

新增 `tests/product/skill/test_extensions.py`、`tests/product/cli/test_extension_cli.py`、
`tests/helpers/test_archive.py`、`tests/product/fixtures/extensions/`、`tests/product/fixtures/archive/`；
复用工作包合同、import 边界和产物检查测试。

扩展目录遵守设计：`$XDG_DATA_HOME/hetu-stock/extensions/<ID>/<版本>/`，未设置使用
`~/.local/share`；登记表为 `extensions/registry.json`，启用绑定按 `codex|claude|opencode|zcode`
区分。包内最小入口 `extension.json` 记录设计 §4.1 所列 ID、版本、来源、摘要、兼容与能力
声明，并定位工作包 Markdown 和参考资料；ID 采用 `x.<命名空间>.<名称>`，不能覆盖 W／WX。
沿用工作包字段与硬依赖校验，不建立研究可执行规则语言或开放全局 schema。

```text
hetu-stock skill extension list|inspect|validate|install|enable|disable|update|uninstall|context
绑定变更：--host <宿主> --id <ID> [--version <版本>]
包输入：--source <用户选定的本地包>
结果：默认可读输出，--json 返回相同目标、版本、启用状态、完成状态和错误原因
hetu-stock helper archive-inspect --source <旧目录>
hetu-stock helper archive-export --source <旧目录> --output <新目录>
```

## 3. 任务 06.1：扩展校验、原子登记和按宿主生命周期

- [ ] 先构造可信资料包、未启用包、核心／官方 ID 冲突、缺依赖、硬环、纯回访环、版本冲突、
  摘要损坏、权限超界和附带脚本包。每份包的实际文件与元数据可独立检查；恶意正文不是测试答案。
- [ ] 增加 `validate_extension(source: Path) -> dict[str, object]`，复用工作包加载／硬依赖
  检查，返回经校验摘要。纯回访关系不作为硬依赖；不解释 `required_when` 或自动安排研究。
- [ ] 安装只保存和验证候选，默认不启用；只展示转义后的元数据，未启用正文不交给研究者。
  拒绝不安全路径、符号链接越界及未声明文件，包含程序／脚本／安装钩子／依赖安装要求时
  拒绝启用与加载，不执行“验证脚本”或从资料中的链接补装能力。
- [ ] 实现登记变更锁、临时文件验证和原子替换；包版本发布后只读，更新准备新版本并保留
  旧候选。半完成安装不可启用，操作失败不混版本或损坏其他宿主绑定。
- [ ] 在所选宿主 enable／disable；update 展示版本、摘要、来源和能力变化，新增权限不得
  继承旧授权。uninstall 明确受影响绑定和硬依赖，未授权删除其他宿主仍使用包时失败并说明。
- [ ] 用实际注册表和文件验证：只在 Codex 启用时另三宿主不加载；多个宿主启用后禁用一个，
  其他绑定保持；更新失败旧包可用，依赖卸载后阻止新加载，不自动选另一版本或改授权。

## 4. 任务 06.2：研究加载、采用及自然语言管理

- [ ] `context --host` 只返回本宿主已启用且完整兼容的候选摘要和受管定位；每次检查依赖、
  实际能力及授权。无合法候选返回空列表及原因，不读未启用正文；管理工具不可用则核心研究
  继续，本次扩展不能加载。
- [ ] 主 Agent 按主体、深度、授权和问题决定是否读正文；正文仍为不可信资料，代码校验及
  用户启用不提高其指令层级。冲突、秘密定位、外传、交易或越权内容停用并说明影响。
- [ ] 产物增加 `run.extensions` 的实际 ID、版本、来源、摘要及启用范围；`work_package`
  允许本次显式记录且已校验的第三方 ID。输出为 `work-packages/x.<命名空间>.<名称>.md`，
  保留目标、证据、反证、冲突、缺口、回访和自检；不代写或替代核心 owner。
- [ ] checkpoint 与报告附录记录加载和采用／排除原因，事实仍回源；运行中不替换已加载
  版本，扩展更新不后台重研。checker 不扫描任意全局插件目录补身份，不判断语义适用性。
- [ ] 将自然语言管理说明放进现有使用文档及命令帮助，Agent 按管理意图定位共用命令。
  变更前展示目标宿主、来源、版本、摘要、范围和能力；目标授权明确直接执行，歧义或新增
  权限才询问。结果必须来自实际执行，纯管理不进入 W0、不建研究目录。
- [ ] 在四宿主真实短会话分别执行“查看本宿主扩展”“启用指定包”“禁用”“更新并说明变化”
  并与 CLI 对照。分别验证歧义、缺工具、校验失败、新权限和恶意正文；只生成命令或帮助
  文本不能算自然语言入口通过。同宿主后续适用任务沿用绑定，授权失效仍拒绝。

定向验证：

```bash
.venv/bin/python -m pytest -q tests/product/skill/test_extensions.py tests/product/cli/test_extension_cli.py tests/product/skill/test_work_package_contract.py tests/product/skill/test_phase5_execution_contract.py
```

预期：合规资料可用，附带代码从未执行；未启用和不可信候选不进入研究，状态／文件／采用
记录一致。上述行为逐宿主验证，mock 或静态检查不能代替安全和实际加载证据。

## 5. 任务 06.3：旧格式只读检查与安全导出

新增 `inspect_archive(source: Path) -> dict[str, object]`、
`export_archive(source: Path, output: Path) -> dict[str, object]`，在 helper 子命令内懒加载。
输入只含显式旧目录，输出含源字段定位、可读内容、未解释／不可导出范围及错误；不导入旧
RunState、StageResult 或执行器。schema_version 3 按普通 JSON，未知字段原样保留。

- [ ] 先加入以下无损未知字段和只读测试，以及多次尝试／原采用指针／用户决定 fixture。

```python
import json
from hetu_stock.helpers.archive import inspect_archive

def test_unknown_fields_are_preserved_without_mutating_source(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    path = source / "state.json"
    payload = {"schema_version": 3, "unknown_field": {"value": "保留原值"}}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    result = inspect_archive(source)
    assert result["raw_records"]["state.json"] == payload
    assert path.read_bytes() == before
```

- [ ] 读取 `state.json`、`stage_results/*.json` 和已有报告，已知请求、证据／主张、论点、
  阶段尝试、原采用和用户决定逐字段呈现；已有状态标“原记录”，不能当新任务恢复状态。
- [ ] 只检查源目录内的引用；缺引用、损坏 JSON 或未知版本标具体范围并保留可取回原文件。
  不执行 HTML、命令、反序列化代码或外链下载，不猜测和重建研究结论。
- [ ] 导出到新目标，生成可读索引及授权允许的原记录／附件，索引保留来源与字段定位。
  目标已存在、路径穿越、symlink 越界都拒绝；输出不得位于源内部或覆盖源文件。
- [ ] 受限材料保留允许定位及原因；原记录含秘密时不复制到索引／包，原件保留原位置，
  明示脱敏和未导出范围。测试使用无价值模拟 secret，不能用真实凭据检验。
- [ ] 测试已知完整字段、多版本未知字段、多尝试、缺引用、坏 JSON、危险路径、已有目标和
  受限内容；逐字段／逐文件对照源字节。更新自然语言用法，读取旧成果不触发研究。

命令：`.venv/bin/python -m pytest -q tests/helpers/test_archive.py tests/helpers/test_cli.py tests/product/cli/test_command_tree.py tests/product/engineering/test_import_boundaries.py`。
预期：原值保持、未知未丢、不安全内容未执行、源不变，旧执行模块未导入；再跑路线图工程门禁。

## 6. 停止、回退与完成

扩展结构、权限、安全、兼容或依赖失败只停用候选，核心可继续；更新失败恢复已批准绑定。
未知 archive 字段不能可靠映射时保留原文，不为统一格式静默丢内容。安全导出无法完成则
给部分结果和范围，不能称无损全量导出。回退只限本次包版本、登记或新导出目录，不改源研究。
扩展和 archive 分别验收，单个子系统通过不抵消另一系统缺口；没有股票分析启动需求。

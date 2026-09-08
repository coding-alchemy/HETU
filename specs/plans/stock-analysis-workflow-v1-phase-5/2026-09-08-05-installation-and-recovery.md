# 五期阶段 05：原子安装、更新与恢复

> 文档版本：v1.0
> 文档状态：已批准；2026-09-08 任务 05.1 执行完毕（33/33 安装器测试+全量门禁通过），05.2/05.3 待续
> 批准日期：2026-09-08
> 创建／修订日期：2026-09-08
> 适用范围：Skill 实体目录、辅助环境、启动器、诊断、回滚和卸载
> 上级：[路线图](README.md)；设计：[§3 全部](../../2026-09-06-stock-analysis-workflow-v1-phase-5-design.md)
> 执行方式：使用 `superpowers:executing-plans`，先失败测试再最小实现。

## 1. 范围与前置条件

计划批准后可独立推进，不等待性能数值；ZCode 发现机制使用 01 的实际证据。本阶段验证临时
目录和受控平台，不更新用户生产安装或执行股票分析。正式安装／自动发现认证在 07 完成。

| 要求 | 任务 | 直接验证 |
|---|---|---|
| 长期 3.1、设计 3.1.1–6 | 05.1 | 完整暂存、校验、同目标锁、原子交换、备份、异常恢复 |
| 设计 3.1 路径和平台边界、3.3 | 05.1、05.3 | 原三宿主路径不变，ZCode 按证据，macOS/APFS 与 Linux/ext4 真验证 |
| 设计 3.2、五期 3.2.2 | 05.2 | 独立版本 venv、启动器原子切换、组合恢复，正常研究不重装 |
| 长期 3.1 完成标准、3.4 工程要求 | 05.2、05.3 | 诊断可定位、卸载不删用户数据、原子失败实际可用版本可证 |

## 2. 文件与接口

修改 `src/hetu_stock/skill/installer.py`、`src/hetu_stock/skill/__init__.py`、
`src/hetu_stock/cli.py`、`scripts/install.sh`、`docs/agent-skill-usage.md`、`README.md`。
测试修改 `tests/product/skill/test_installer.py`、`tests/product/install/test_install_script.py`、
`tests/product/cli/test_skill_cli.py`、`test_command_tree.py`、`test_help_text.py`。
沿用 `verify_skill_manifest`、`validate_skill_package`、`install_skill(source, destination_root, *, force=False)`。

保留 `skill install --host --source --destination --force`，补 `skill status|diagnose|rollback|uninstall`，
维护命令明确 `--host` 和可选 `--destination`；rollback 可选 `--backup` 指定诊断已列出的备份，
未指定则仅在上一完整组合唯一可判断时使用，歧义不猜选。目标仍为根下 `hetu-stock-analysis`。
`HostTarget` 增 `ZCODE="zcode"`，默认发现路径必须有原生证据，不猜 `~/.zcode/skills`。

维护记录存于目标文件系统内、宿主发现目录外的受管区，以规范化目标定位并共用锁。只有安装
操作状态、目录定位、旧新包摘要、备份和关联环境；不存证券或研究下一动作。选址或同文件系统
条件无法确认则在触碰旧版前失败。此记录不使历史研究或认证自动过期。

## 3. 任务 05.1：替换先删后复制的安装路径

- [x] 在现有 `test_installer.py` 使用 `_make_skill_package` 创建旧包／新包，先加入“复制新版
  失败仍可验证旧包”的测试；当前 `force` 路径删除旧目标，测试应真实暴露这一问题。

```python
def test_force_copy_failure_preserves_installed_package(tmp_path, monkeypatch):
    source = tmp_path / "source" / "hetu-stock-analysis"
    _make_skill_package(source)
    destination = tmp_path / "installed"
    target = install_skill(source, destination)
    before = installer_module.build_skill_manifest(target)
    def fail_copy(*args, **kwargs):
        raise OSError("injected copy failure")
    monkeypatch.setattr(installer_module.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="injected copy failure"):
        install_skill(source, destination, force=True)
    verify_skill_manifest(target)
    assert installer_module.build_skill_manifest(target) == before
```

- [x] 在 installer 实现 `exchange_directories(left: Path, right: Path) -> None`，仅封装原生
  Linux `renameat2(RENAME_EXCHANGE)`／macOS `renameatx_np(RENAME_SWAP)`；不可用抛明确
  `OSError`，不退化为两次 rename，不使用 symlink 替代实体入口。不引入通用平台插件框架。
- [x] 对规范化目标加跨进程维护锁；先核对源／目标边界和非受管路径，再在同文件系统受管区
  准备末级同名完整包，校验 manifest、frontmatter、链接及包结构。首次安装用一次目录改名，
  覆盖更新才交换；未 `force` 时继续拒绝覆盖。
- [x] 交换前写入最小事务记录并刷盘；交换后确认目标完整，旧包在暂存位登记备份，最后完成
  记录。目标校验失败则交换回旧包，恢复失败保留两份包与记录，不报回滚成功。
- [x] 启动下一次维护时按记录与真实目录／摘要恢复中断操作，不能只信状态字符串或盲删暂存。
  暂存、备份和目标未唯一匹配时报告具体动作，不选择不确定版本。
- [x] 参数化故障注入：源损坏、复制中断、暂存校验失败、交换失败、交换后校验失败、刷盘／记录
  失败、进程中断、同目标并发、不同目标独立操作、跨文件系统与原生交换不支持。
  每项验证目标实际完整且可读、备份和用户文件保持，不只断言异常类型。
- [x] 保留原包内 symlink／越界／缺文件拒绝、清单严格和用户指定 destination 行为；更新原先
  期待“失败后目标删除”的测试为“旧版仍可用”，不删掉坏输入案例。

## 4. 任务 05.2：辅助环境与启动器组合恢复

- [ ] 在 `test_install_script.py` 的受控 HOME／XDG fixture 中先测试 pip 失败、CLI 自检失败、
  非受管启动器、Skill 切换后启动器失败；预期旧环境、旧启动器和旧 Skill 保持或恢复一致。
  环境变量仅由测试子进程传入，不能改执行者全局配置。
- [ ] 修改 install.sh：在最终保留的版本目录创建新 venv，完成依赖安装和 CLI 自检，不升级
  旧 venv，也不在创建后移动 venv；保留 Python 3.11／3.12、TLS 与受管目录限制。
- [ ] 通过同一维护事务关联旧新环境与 Skill；启动器用临时链接原子替换，拒绝覆盖非受管文件。
  Skill 发布与启动器切换分步，各自原子；后续步骤失败恢复已切换部分，不虚称跨目录整体原子。
- [ ] 成功前不删除旧组合，成功后保留上一可用组合；回滚沿用同一校验、交换和启动器流程。
  多宿主仍引用共享环境时不能删除它。
- [ ] 用真实临时版本环境核对 CLI 可运行及 shebang 仍指向存在的解释器；无完整辅助包的
  public 基础研究仍可使用 Skill 和原生工具，日常研究不自动跑安装和工程检查。

## 5. 任务 05.3：维护命令、ZCode 路径与真实平台验证

- [ ] CLI 增 status／diagnose：只读完整性、备份、受管启动器和辅助能力，输出脱敏定位、
  原因和下一动作，不自动联网、修复或列环境变量全集。保留顶层仅 `skill`／`helper`。
- [ ] rollback 使用明确完整备份走 05.1／05.2，保留被换下版本；无备份或恢复失败非零退出。
  uninstall 先列所选 Skill、受管启动器、环境与扩展绑定范围，再按用户已授权操作；共享环境、
  其他宿主、研究、旧报告、授权配置和非受管文件保留，不把整个宿主目录当删除目标。
- [ ] 使用 01 的 ZCode 原生发现证据实现 default_user_skill_root 分支、install.sh host 解析
  和帮助；路径未验证则明确失败或要求用户给出明确 destination，不能宣称自动发现已支持。
  新路径不得改变原 Codex、Claude、OpenCode 默认值及自定义路径含义。
- [ ] 在 macOS/APFS 和 Linux/ext4 实际执行交换、并发、中断后恢复及不支持文件系统负例。
  单元 mock 只验证错误路径，不替代真实平台成功；无平台时登记未验证，由 07 补齐。
- [ ] 更新使用说明的安装、诊断、回滚、卸载及平台条件，声明实际支持边界；暂存／备份不被
  Skill 自动发现，真实安装入口继续是实体目录。

验证命令：

```bash
.venv/bin/python -m pytest -q tests/product/skill/test_installer.py tests/product/install/test_install_script.py tests/product/cli/test_skill_cli.py tests/product/cli/test_command_tree.py tests/product/cli/test_help_text.py
```

预期：成功更新后新包可运行、旧版可回滚；每类失败后实际可用版本和用户数据与期望一致。
再跑路线图工程门禁，代码和帮助检查通过不代表真实平台验证完成。

## 6. 停止、回退与完成

不能安全选维护区、原生交换不支持、受管归属不明或恢复候选有歧义，均在改旧版前失败；
后置故障保留现场并恢复已切换部分。回退不删除用户环境、研究和其他宿主绑定。
本阶段无实际股票分析；变更只影响安装行为和组合可用性，不自动触发历史研究重验。
完成须具备正常安装、全部直接故障、诊断与卸载的实际文件结果；缺平台证据不得声称支持。

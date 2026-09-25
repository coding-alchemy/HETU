# HETU zn_dev 剩余四项（原第 5–8 组）集中问题盘点与最小修复设计

> 文档版本：v1.2
> 文档状态：阶段 01–05 已执行完成；阶段 05（2026-09-25 全面评审 4 项迁移前阻断问题）
> 经用户批准后执行完成（实现提交 `8e0177f`），4 项阻断关闭；未迁移、未推送
> 创建日期：2026-09-24
> 适用范围：zn_dev 上原第 5 组（第三方扩展管理）、第 6 组（计量与宿主验收工具）、
> 第 7 组（研究质量与宿主认证）、第 8 组（正式性能采样与校准）的集中问题盘点与最小修复
> 依据：[长期需求](stock-analysis-workflow-v1-requirements.md)、
> [五期后半需求去向](2026-09-20-stock-analysis-workflow-v1-phase-5-remainder-requirements.md)、
> [五期中期实现](2026-09-21-stock-analysis-workflow-v1-phase-5-middle-implementation.md)、
> [五期下一批迁移实现](2026-09-24-stock-analysis-workflow-v1-phase-5-execution-implementation.md)、
> [五期执行计划路线图](plans/stock-analysis-workflow-v1-phase-5/README.md)
> 判定依据：用户有效要求与已批准合同；源码、canonical Skill 和测试用于核对实现事实，不以当前实现覆盖要求。
> 修订说明：按五处复评意见订正；撤回 6-B4 重入缺陷，缩减非必要修复与重复验证。
> v1.2（2026-09-25）：新增 §9–§10，登记全面评审确认的 4 项迁移前阻断问题
> （uninstall 越界删除、check 计时 fail-open、capture 覆盖有效夹具、描述续行误取）
> 的最小修复设计与白名单处置边界；§1–§8 既有结论不变。

---

## 0. 现场与盘点方法

现场（2026-09-24 实测）：分支 `zn_dev`，HEAD `a044bb2`（与 main 基点 `751ed53` 的
merge-base 即 `751ed53`，第 1–4 组已合入 main）；本地领先 `origin/zn_dev` 4 个提交、
落后 1 个；工作区仅有一处既有未提交修改
`specs/2026-09-24-stock-analysis-workflow-v1-phase-5-execution-implementation.md`
（上一轮状态订正，本批保留、不覆盖、不混入提交）。

首轮诊断方法（沿用记录，本次文档订正未重复执行）：四路并行只读核查（代码通读＋本机真实库只读核对＋`/tmp` 沙箱复现），关键结论由
主核查线再次读码或实测复核；未登录外部账户、未读取凭据、未运行真实宿主会话、未请求
新模型样本；未修改实现代码或 `.hetu/` 原始证据。诊断产出本设计，后续只订正文档。

首轮诊断登记的基线（本次订正未重跑）：

- 扩展与验收测试：`test_extensions.py`＋`tests/product/validation/` **229 passed,
  5 skipped**（skip 均为带理由的宿主可用性条件 skip）。
- `test_phase5_parallel_contract.py`：**36 passed**。
- E4 记录的两项既有失败（`test_usage_guide_states_host_support_statuses`、
  `test_installed_host_version_matches_fixture[opencode]`）当前均通过。
- 临时移走本机 `.hetu/` 后复跑：**恰 7 个 writeback 测试失败**（FileNotFoundError，
  非 skip），其余 208 项通过；`.hetu/` 已原样恢复。
- 全量工程门禁（修复前，`env -u HETU_HOST_EVIDENCE bash scripts/check.sh`，本机
  `.hetu/` 在位）：**1544 passed, 5 skipped**（skip 均为带理由的宿主可用性条件
  skip），ruff/mypy/文档链接/MANIFEST/Skill validate/`git diff --check` 全部通过。
  即 E4 登记的两项既有失败现已消失，当前基线为全绿（依赖本机 `.hetu/`）。

计数约定：子缺陷、测试问题、证据限制与迁移步骤均归入所属项（第 5–8 组），不另计需求。

## 1. 结论总览

| 组 | 本批修复 | 登记但不执行 | 暂缓专项 |
|---|---|---|---|
| 5 扩展管理 | 5-A1、5-A2、5-B1 | 5-B2 清理、5-B3 边界测试；E3A、缺工具刺激、E1 归因 | 不开宿主补验 |
| 6 计量与验收工具 | 6-A1～6-A4、6-B1、6-B3；6-B2 随 A 组补负例 | 6-B4 重入缺陷已撤回；CI 本机依赖和未公开 schema 限制；6-C 额外加固 | 不建在线监控、不运行新模型 |
| 7 质量与认证 | 无实现修复 | 7-B1/7-B2 补记本批不做；A2 边界、既有支持范围 | 不启动认证或深度／多模型补验 |
| 8 性能与校准 | 8-B1 历史审计条件跳过 | 比较生成器尚未实现／证据层限制 | 不采样、不校准、不修改指标 |

仍是四项需求，六个 A 项与四个纳入的 B 项均为组内修复，不另计需求。
「历史账目已订正」与「生产导出器已修复」分别判断。历史工具错挂本身不改变原生 token
数值，但状态及重试身份影响未来导出完整性，不能笼统保证所有路径总量不受影响。
中期 B1–B4 的既有账目按其独立核算与已批准口径继续有效，不重开或重算。

## 2. 第 5 组：第三方扩展管理

### 5-A1 「默认最新版本」按字典序选择，跨 0.9→0.10 选错（本批纳入）

- 违反的有效要求：CLI 契约「Bind an extension to a host (defaults to the newest
  version)」（`src/hetu_stock/cli.py:413`）；阶段 06 设计「更新不动旧绑定、正确报告
  前一版本」。
- 定位：`src/hetu_stock/skill/extensions.py:483`（`_find_version`：`sorted(versions)[-1]`）、
  `:455`（`update_extension` 的 `previous_version` 同法）；`inspect_extension`(:642）
  同路径。
- 触发场景与最小复现（已沙箱实证）：安装 `0.9.0` → `update` 发布 `0.10.0` → 不带版本
  `enable`，实际绑定 `0.9.0`；`update 0.11.0` 报告 `previous_version=0.9.0`（应为
  `0.10.0`）。
- 已证原因：`sorted()` 字典序，`"0.10.0" < "0.9.0"`。历史 06.2/E1 会话全部显式带
  `--version`，该路径从未被真实会话覆盖，故未暴露。
- 用户可观察影响：跨小版本后「启用这个扩展」静默绑回旧版；更新报告的能力差异按错误
  基准计算。不越权，可显式 `--version` 规避。
- 最小修复：默认选择与 previous_version 共用数值感知排序键。按连续数字／非数字段
  分词，数字按整数、非数字按原字符序比较，使用固定类型标签避免 int/str 混比，原字符串
  作最终平局键；`0.9.0 < 0.10.0 < 0.11.0`、`release-9 < release-10`。不宣称 SemVer
  预发布优先级，不收窄现有安全版本字符串范围；显式版本始终精确匹配。
- 验证：enable 默认、inspect 默认与 update previous_version 的可观察结果；显式选择
  旧版本仍生效、未登记版本仍拒绝；数字/文字混合不崩溃。更新不更改已有宿主绑定。

### 5-A2 畸形登记表使全部扩展管理命令未捕获异常崩溃（本批纳入）

- 违反的有效要求：正常失败须 exit 1 并给出原因（CLI 既有约定）；设计「失败时给出
  原因和下一动作」。
- 定位：`_read_registry`（`extensions.py:109-122`）只校验顶层结构；下游
  `list_extensions`(:635 `binding["version"]` → TypeError)、`_resolve_bindings`
  (:504 `binding.get` → AttributeError，影响 `context`/`enable`)、
  `disable_extension`(:601)。
- 触发场景与最小复现（已沙箱实证）：将某宿主 binding 值写为裸字符串 `"0.1.0"`
  （正是 2026-09-18 Codex 伪造启用事件的形态），`extension list --json` 与
  `context --host codex` 均输出 Traceback。
- 已证原因：登记表容器与值形态无校验。现行 disable 会先移除绑定并提交，再访问
  `removed["version"]`，畸形值可导致“已改文件后才崩溃”，不能只描述为只读失败。
- 最小修复：在任何消费或写回前，校验 extensions、bindings、宿主绑定集合为对象；
  扩展记录、versions、版本记录以及下游实际读取的字段形态须满足既有合同；binding
  为含合法字符串 version 的对象。空版本集合在默认选择前受控拒绝。保留原先合法的
  缺省宿主绑定初始化语义，不引入新登记表格式或自动修复损坏文件。
- 失败抛 ExtensionError，指出错误位置且不输出正文或秘密；list/context/disable 经
  CLI 返回明确原因、无 Traceback。裸字符串 binding、bindings=null、extensions
  非对象、空/畸形 versions 均有对应回归；失败前后登记表字节必须相同。

### 5-B1 validate 系列测试不隔离全局登记表（本批纳入）

- 问题：`validate_extension` 经 `_registered_provides()`（`extensions.py:286-295`）
  读取**调用者机器**的 `~/.local/share/hetu-stock/extensions/registry.json`；
  `test_extensions.py:42-160` 段未 monkeypatch `XDG_DATA_HOME` 的用例结果依赖本机
  状态。已实证：构造提供 `x.demo.missing` 的假登记表后
  `test_validate_rejects_missing_hard_dependency`(:73) 失败——本机恰好无此 provide
  才通过。
- 修复：相关用例统一 monkeypatch `XDG_DATA_HOME` 至 `tmp_path`；验证用同一假登记表
  正反两态各跑一次。

### 5-B2 `_RESERVED_ID` 不可达死代码与两个名不符实的测试（本批不执行）

- 问题：`extensions.py:344` 先强制 `x.` 前缀，`:346` 的 `_RESERVED_ID`
  （`^W\d+|^WX`）永不可达（逐值验证）；`test_validate_rejects_core_id_conflict`(:55)
  与 `test_validate_rejects_wx_official_id_conflict`(:61) 名义测保留 ID 冲突，实际
  走的是格式拒绝分支。
- 后续可选清理方向（本批不改）：删除不可达分支；两测试改写为断言「W/WX 形态 ID 被 `x.` 前缀规则拒绝」的
  真实路径（行为不变，测试声明与覆盖一致）。

### 5-B3 安装后新增未声明文件不被完整性校验发现（登记为已知边界，不改行为）

`_stored_integrity_ok`（`extensions.py:403-414`）只重哈希已登记文件，向受管目录新增
未声明文件不会被发现。完整性摘要只覆盖声明文件是设计取舍（正文本就不受信任）；本批
不新增钉住该边界的测试，不改变校验范围；这里只记录现状，不据此扩大安全通过声明。

### 5-C 证据或宿主能力限制（本批不关闭）

1. **Claude E3A（未留采用/排除记录）**：核对现行指令与检查器双侧均已存在要求
   （`host-tools.md:43`、`checkpoint.md:38-40`、`work-package-result.md:3`、
   `check-run-artifacts.py:1093-1135,1217-1227`）。非代码缺陷、非指令缺口；是
   Claude 宿主未执行既有条款的行为未通过＋关闭所需真实会话证据缺口。按结束规则不
   追加复验。细节订正：违规点是「未留采用/排除记录」，「是否读正文」本属主 Agent
   裁量（`host-tools.md:26-27`）。
2. **Claude／ZCode 缺工具刺激未成立**：现行 `host-tools.md:38-40` 缺工具规则本身
   允许绝对路径回退，探针机为非封闭会话，PATH 受限下 CLI 仍可达——刺激按构造不可
   能成立。指令无缺口，属证据构造限制。
3. **Claude E1 加载途径与归因未证实**：`extensions.py`/`cli.py` 没有也不可能有
   「文本经何途径进入上下文」的观测通道（由宿主 Skill 发现机制决定）。宿主能力
   限制，代码层无法关闭。

第 5 组其余方面（生命周期、按宿主绑定、依赖与完整性校验、失败关闭、原子提交、研究
面只回元数据）在首轮读码与 46 项行为测试所覆盖范围未发现其他问题，不外推所有场景无缺陷。

## 3. 第 6 组：计量与宿主验收工具

### 6-A1 db 导出工具关联启发式错位＋静默漏带（本批纳入）

- 违反的有效要求：工具归属与计数必须正确；缺失/未知不得静默处理（阶段 01 计量
  语义；中期 §5.1 对账口径）。
- 定位：`scripts/host_acceptance.py:2049-2081`（`_zcode_db_events`）：tool_usage
  按 `turn_id` 分组排序后按 `tool_call_count` 顺序切片，无 part 级对账。
- 触发场景与实证：某请求 `tool_call_count` 少报（典型：`status='error'` 记 0 但实际
  派发了工具）→ 后续请求整体错位，末尾工具被静默丢弃。本机真实库（只读）实证两处：
  `turn_aaabd3e3-…` 中 error 请求实际拥有 `call_ee0a70e8`，致 6 个工具错挂、末尾
  `call_fe51528d`（Write）丢失；另一会话丢失 `tool_cfd00485-…`。全程无 gap。
- 最小复现（已在 /tmp 用生产 CLI 跑通后清理）：2 条 model_usage（error/0 工具＋
  completed/1 工具）＋2 条 tool_usage → 导出错挂且丢 1 个工具，exit 0、无缺口记录。
- 影响：db 通道导出事件的工具归属与计数不可信；**token 总量不受影响**（token 在
  model_usage 行上），不推翻 B1–B4 已关闭账目。
- 订正：按 turn 对工具总数只能发现差额，不能证明逐请求归属；数量相等但工具互换
  仍会错账。不得保留顺序切片后仅补总数检查。
- 最小修复：以可核验的原生请求身份、message/part 中的工具 call ID 与 tool_usage
  记录建立关联，先验证各关联字段的实际含义；不凭时间邻近、顺序、数量或单一 turn_id
  猜归属。已支持的结构用最小合成 sqlite 固定其形状与关系；缺少、矛盾或无法唯一关联
  时受控拒绝该次完整导出，带可定位原因，不伪装为零工具。
- 窗口规则仍分别作用于模型请求与工具实际开始时间。跨边界关联可以只读核对窗口外身份，
  但不能把窗外请求/工具加入计量集合或静默把孤立工具挂给窗内请求；不能完整解释时拒绝。
- 测试覆盖：少报、数量相等但请求归属交换、重复工具身份、找不到归属、同 turn 多请求、
  跨窗口边界及正常完整链路。正常场景必须可导出，不能以“全部拒绝”代替修复。
- 若现有可读取证据无法确定请求与 message/part 的稳定关系，停止本项并登记最小能力
  缺口；不虚构字段、不启动模型补验，不宣称 6-A1 已关闭。

### 6-A2 执行状态与用量结算状态混淆（本批纳入）

- 原因：SELECT 不读取 status；error/cancelled 的默认零值可能被当作已知完整用量。
  首轮记录的 97 条 error、54 条 cancelled 零值只能证明需辨别语义，不能证明没有消耗。
- 撤回“只取 completed、排除数记入 provenance 即修复”的方案。中期一次性账目口径
  不得自动推广为所有未来导出的状态过滤规则。
- status 加入已支持结构核查，但状态只说明执行结果，不独自证明用量结算。读取窗口内
  全部尝试：按已核实通道语义结算、输入输出有效且可回源的实际用量计入，即使业务失败；
  未结算、字段缺失或零值只是初始化占位时保留未知/缺口，不按零或从分母中消失。
- 本批采取保守出口：遇到无法证明结算的必需尝试，导出明确失败且不发布成功产物，报告
  对应身份与状态。没有结算证据的非 completed 零值必须走此路径；可靠零用量须有可
  核验依据。非零本身也不等于最终结算，不新增猜测式兜底。
- provenance 记录已纳入请求的状态分布；失败诊断区分未知与已知，不能仅靠附注让下游
  check 把不完整账目判为通过。状态缺列/未知结构按 schema 缺口处理。
- 测试覆盖：completed 正常、失败但可靠结算、取消且未知、占位零、可靠零、缺列、未知
  状态及正常请求与未知请求混合；检查导出返回码、文件与最终计量判断，非只测 SQL。

### 6-A3 重试身份缺 `attempt_index`（重试场景缺陷，本批纳入；需合成回归证明）

- 定位：`scripts/host_acceptance.py:2086`（`message_id = str(logical_request_id)`）。
  对账口径要求按 `logical_request_id`＋`attempt_index` 去重、重试独立计量。首轮核查本机库
  31893 行 attempt_index 全为 0，故未现实化；一旦出现重试行：token 不同→
  `usage_conflict`（方向安全），token 相同→**静默去重少计**。
- 最小修复：`attempt_index` 加入必需列并校验为非负整数，message_id 使用请求身份与
  attempt 的无歧义编码，例如 `json.dumps([logical_request_id, attempt_index], separators=(",", ":"))`；
  不用缺省 0 掩盖缺失。补同请求两 attempt 独立计量、同 attempt 重复快照去重及身份
  缺失/冲突负例；旧事件不与新编码导出混用，历史已保存产物不重写。

### 6-A4 `db-export` 多会话部分写入＋重跑崩溃（本批纳入）

- 违反的有效要求：自身 docstring 合同「otherwise nothing is written and the
  command fails」（`host_acceptance.py:4189-4194`）；证据写入原子、可诊断。
- 定位与复现（均读码确认）：`:4264-4285` 逐会话循环内即 `append_event`——前序会话
  有行、后续会话零行时返回 1，但前序事件已落盘且 provenance 未写（部分写入）；
  重跑同目录时事件先重复追加，随后 `_write_json(db-export.json)` 抛未捕获
  `FileExistsError`（`:4287` 只捕 `sqlite3.Error`），traceback 退出。
- 影响：证据目录残留无 provenance 的部分导出；命令不可重入。缓解：下游 `check` 对
  缺 scope 声明 fail-closed、重复事件按身份去重，不致误判通过。
- 最小修复：改为两阶段——先对全部 (scope, session) 收集并校验，全部通过才统一写入；
  `db-export.json` 或 `usage-events.jsonl` 已存在时在写入前清洁拒绝，禁止把旧
  半成品或其他通道事件混入。先准备本次完整内容，再发布；写入错误须诊断并清除本次
  新建半成品，不动既有证据。补多会话失败零写入、已有输出字节不变、重跑清洁拒绝
  与写入失败测试。先红后绿；不扩展为通用事务框架或承诺断电跨文件原子性。

### 6-B1 `capture_host_cli.py` 标志解析只取行首第一个 flag（本批纳入）

- 定位与实证：`capture_host_cli.py:32,39-41`——`_FLAG_LINE_RE` 只匹配行首一个
  flag token，`-h, --help` 合并行的长 flag 全部丢失。真实夹具帮助文本实证：codex
  缺 `--model/--config/--sandbox` 等 9 项，opencode 缺 `--model/--session` 等 5 项，
  claude 缺 `--debug/--worktree/--allowed-tools` 等 9 项。一致性测试只查
  accepted⊆help（完备性永不检查），该脚本无测试引用。
- 影响：首次自动初始化会漏掉选项别名；人工策展白名单本来允许是有意子集，遗漏某项
  不等于宿主不支持该项，不能要求策展白名单穷尽全部帮助选项。
- 最小修复：解析选项声明中的短长别名，排除描述、示例与参数值中的类 flag 文本；以
  合成帮助文本和已有夹具只读文本验证提取。首次初始化采用解析结果；刷新保持已有
  accepted_flags / checked_absent，不自动重策展、不重捕真实宿主。
- 撤回 `help flags ⊆ accepted ∪ checked_absent` 断言。checked_absent 表示确认不存在，
  不得收容帮助中存在的选项。保留既有白名单真实性与互斥检查。

### 6-B2 db 通道负例测试缺口（随 6-A 修复一并补齐）

现有 db 通道测试只有精确匹配正例。6-A1～6-A4 的修复各带对应负例测试，不单独立项。

### 6-B3 角色归属粒度：db-export 不区分 `query_source`（最小透明化，本批纳入）

- 问题：宿主附加行（如 `session_title`）并入会话 scope，角色/环节视图无法从生产
  导出还原（token 不丢；中期对账的 host_aux 单列靠一次性脚本完成）。
- 最小修复：不改总量口径；`query_source` 作为可选列（不加入 schema 必需列），存在
  时在 provenance 中记录各会话行的分布，供后续角色核对。无该列时明确标为不可得，不填零分布；不改变既有总量，
  不据此宣称实现逐角色 token 对账，不能用该分布替代 6-A1/A2/A3 的证明。

### 6-B4 CI 重入缺陷撤回，环境限制保留（本批不修改 workflow）

现有 workflow 使用 `ubuntu-latest` 托管 runner，每次任务为新环境且未恢复旧输出目录。
因此“第二次手动触发必然因固定路径失败”不成立，撤回路径唯一化修复和缺陷计数。
ZCode 所依赖的本机 app/db 在该 CI 环境不可得，属于能力限制；改输出路径不能补齐。
本设计登记此限制，不改判定、不启动 CI 认证或新增 runner。

### 6-C 证据或能力限制（本批不关闭）

- zcode 夹具为 `unavailable`（无 CLI 二进制）；zcode 能力证据依赖本机 ZCode.app 与
  `~/.zcode` 内部 db，schema 未公开，漂移即 fail-closed（设计如此）。
- 额外加固候选（本批明确不执行）：`computed_total_tokens` 加入必需列并与
  input+output 一致性对账，口径漂移即 fail-closed；本机库 31887 行全部满足等价。

## 4. 第 7 组：研究质量与宿主认证

**首轮已检查范围内未发现实现或声明缺陷。** 支持声明一致性经逐项核对：README、`docs/agent-skill-usage.md`、
`hosts.json`、SKILL.md 与证据层 `host-capability-matrix.md`/`status-summary.md` 互相
一致——仅 zcode×public×standard 质量门槛验收，其余组合如实 `UNVERIFIED`；未发现把
UNVERIFIED 写成已支持或反向错误。`check` 判定逻辑 fail-closed 覆盖完整（空事件、
scope 覆盖、交付源、零分母、隔离证据链逐项失败），168 项定向测试全过。

### 7-B1 「10/10 通过率」分母口径透明度补记（本批不执行）

- 问题：07.3 要求通过率按所有有效独立尝试计算；三次环境终止尝试（S3 并发终止、
  C2 限流终止、Q1 失活）已在 `attempt-register.md` 登记保留，但
  `batch-record.md` §2.1 的「10 次独立端到端、10/10」未说明这些尝试与分母的关系，
  未定位到「独立评审证明无效」的依据。
- 边界：该验收已经用户 2026-09-14 方案 A 批准，按既有结果有效性规则**不作废、不重验**；
  本项只是声明透明度缺口。
- 后续可选方向（本批不执行）：不改动原始记录；在 `.hetu/validation/phase-5/`（Git 忽略、不入库）追加
  一份补记文件，说明三次终止尝试的登记位置、与分母的关系及现有口径依据。
  **本批已剔除，不追加补记或改写既有验收；后续若处理须另行授权。**

### 7-B2 历史批次记录括注数字不一致（仅登记，不改写历史）

`.hetu/.../20260913-stage07-research-batch/batch-record.md:122` 括注「10/11」与其
表格（6+5=11）不一致；下游声明均使用与表格一致的口径，未受影响。属历史记录原文
瑕疵，本批仅保留诊断登记，不创建 7-B1 补记。

### 7-C 证据或能力限制（本批不关闭）

1. A2 深度资格边界（研究必需信息/允许替代/特定获取路径）：现行 Skill 文本经核对
   内部自洽、无自相矛盾，A2 判 quick 的独立复核逐项可定位；边界本身按已登记缺口
   保留，需专项核查（不在本批）。
2. 零分母/状态转换无语义实例：688048 映射已按「证明范围限定于实际出现的语义场景」
   披露，方式正确。
3. 其余组合的完整宿主认证未完成：是未完成的认证，不是实现缺陷；不启动。

## 5. 第 8 组：正式性能采样与校准

**首轮已检查范围内未发现计算、比较或审计逻辑错误。** `db-delivery` 交付端点（首个 visible＋最终报告
标记＋非草稿＋run 归属，完成时刻取 part.time.end 与 message.time.completed 的 max，
缺失即 fail-closed）、`summarize_timing`（单时钟基、倒置/零长窗口/越窗均显式失败）、
`sweep` 端点（completedAt、create-only）与计划口径逐条吻合。配对比较的中位数与比较
表生成器**在仓库内无实现**（在 `.hetu` 证据层，07 计划 §6.1.5 复选框未勾选）——这不
构成代码缺陷，但意味着比较口径的证明责任在证据层，不能靠迁移代码证明。

### 8-B1 7 个 writeback 测试依赖本机 `.hetu/` 且无兜底（本批纳入）

- 定位：`tests/product/skill/test_phase5_parallel_contract.py:39`（`IMPACT_MAP` 指向
  `.hetu/validation/phase-5/20260909-stage02-calibration/impact-map.md`），
  `test_impact_map_writeback_is_append_only_new_section` 及六个 `test_writeback_*`
  （503–590 行），无 skipif。
- 实证：临时移走 `.hetu/` 后恰这 7 项 FileNotFoundError 失败（记 FAILED 非 skip）；
  其余 208 项通过。`.hetu/` 被 `.gitignore` 忽略，干净克隆必红 7 项。
- 性质判断：七项断言的对象是 2026-09-09 那份历史 impact-map 的回写形态，属**历史
  审计**，不是对活代码行为的回归保护（其中 `..._names_exist_in_their_test_files`
  有独立价值，但其输入清单来自该历史文件）。迁移文档已决定它们不迁 main
  （执行文档 §7）；本批不删除、不改断言内容。
- 违反的有效要求：整体完成标准 07.6「测试不依赖本地历史记录」（路线图 §7）。
- 最小修复：按仓库既有惯例（`test_phase2_output_contract.py` 对
  `skipif(not DEMO_ROOT.is_dir())` 的同款处理）为 7 个测试加**带理由的条件 skip**
  （证据文件缺失时跳过并说明，属环境条件 skip，非无条件 skip 掩盖失败）；本机有
  `.hetu/` 时行为不变。
- 验证：不搬动真实 `.hetu/`；用不含私有证据的临时源码副本检查这七项带理由跳过，
  有证据时正常审计。用临时副本中的损坏文件确认断言仍失败（包括路径存在但不是普通
  文件的错误，不能用泛化异常捕获跳过）。既有宿主 skip 与这七项分别登记，不固定总 skip 数。

### 8-C/D 登记（本批不启动）

- 未采样：三条性能验收线均未正式通过（配对 0/3、0/1、0/1；复用收益未测）；A3/AQ/AD
  未启动且不自动补跑；P2 已在 A2 资格门后停止，未使用授权不自动延续。
- 未校准：三档预算数值未校准；比较表确定性生成器未勾选。
- 未达标：不宣称提速、token 节省或数值预算已校准；不通过修改阈值或删除断言处理。

## 6. 本批范围、映射与验证

只修改下表实现与测试，不修改 Skill、MANIFEST 内容、workflow、真实安装或 `.hetu/`
原始证据及补记；不动 main、不推送、不使用 worktree。既有未提交文档原样保留。

| 修复 | 文件范围 | 必须验证的结果 | 计划 |
|---|---|---|---|
| 6-A1 | `scripts/host_acceptance.py`、`tests/product/validation/test_host_acceptance.py` | 正常真实关系可导出；错配/漏带/不明确关联失败，数量相等不冒充正确 | 01 |
| 6-A2 | 同上 | 失败已知用量按合同计量；未知不为零、不静默排除、不通过完整性 | 01 |
| 6-A3 | 同上 | 同请求不同 attempt 分别计量，同 attempt 重复快照不重复计量 | 01 |
| 6-A4 | 同上 | 多会话验证失败无新增输出；重跑和旧半成品不被追加或覆盖 | 01 |
| 6-B2 / 6-B3 | 同上 | A 组负例；query_source 有/无两态披露，不称完整角色账目 | 01 |
| 5-A1 | `src/hetu_stock/skill/extensions.py`、`tests/product/skill/test_extensions.py` | 三个默认版本入口排序一致；显式选择与绑定不受损 | 02 |
| 5-A2 | 同上及 `tests/product/cli/test_extension_cli.py` | 畸形容器/叶子受控失败，错误前后登记表字节相同 | 02 |
| 5-B1 | `tests/product/skill/test_extensions.py` | validate 不依赖真实登记表，临时正反数据结果确定 | 02 |
| 6-B1 | `scripts/capture_host_cli.py`、`tests/product/validation/test_capture_host_cli.py`（新建） | 选项别名完整，描述不误取，刷新保留策展子集 | 02 |
| 8-B1 | `tests/product/skill/test_phase5_parallel_contract.py` | 仅七项历史审计缺证据 skip；损坏证据仍失败，活功能测试不跳过 | 02/03 |

精简执行计划见[路线图](plans/zn-dev-remaining-groups-fix/README.md)。01、02 定向回归，
03 在修复树上执行一次最终工程门禁及必要可移植性检查，不额外重复有/无证据两套全量。
测试数以实际输出为准，首轮诊断数字不是验收硬编码值。

## 7. 既有结果与本批完成条件

- 不重开 M2-6、中期 B1–B6 与已迁第 1–4 组验收。版本或提交变化不使既有证据失效。
- 未来 db 导出语义、扩展默认版本选择和错误路径直接受影响，由对应新负例及相关既有
  正例回归；历史账目由独立核算支持，不用新导出结果改写旧账。
- 七项 writeback 为未迁历史审计：条件跳过不代表性能通过，不能推广到活功能测试。
- 5-B2、5-B3、6-C 额外加固、7-B1/7-B2 均不执行；6-B4 撤回缺陷，环境限制保留。
- 已证问题修复与必要回归成立后按四组重判“实现／证据／迁移条件”，不宣称四组全部
  闭环。能力缺口仍不能解决时列明最小缺口，不反复探测或扩大授权。

## 8. 批准登记与停止点

用户于 2026-09-24 同意复评订正并要求制定执行计划；本版落实五处修复方案订正及范围
收敛。此批准允许编制计划，不等于批准实施。计划待用户审阅，本轮未改实现或运行模型。
计划批准后按阶段推进；不自动迁移、推送、强推、认证或性能采样。

2026-09-25：用户批准 §9–§10 阻断项修复设计与阶段 05 计划，含两项取舍（缺宿主保留
已有捕获时非零退出，明确表述为「本次刷新未完成，已有捕获已保留」；claude 夹具从
`accepted_flags` 显式移除 `--permission-prompt-tool`，登记为「现有证据不足以证明
支持」，不加入 `checked_absent`、不推断不支持、不启动真实宿主补验）。阶段 05 已按
批准执行完成，结果见阶段文件 §5 补记。

---

## 9. 全面评审迁移前阻断项（2026-09-25，第 5、6 组组内修复）

以下 4 项为全面评审已证阻断问题，均已在规划阶段读码／只读预览复核定位；对应
`2026-09-20-…-remainder-requirements.md` §4 第 8 项登记的同类问题。本批只修这 4 项，
其余登记级后续项（db-delivery 重入、schema 漂移暴露、死代码等）不处理。

### 9.1 扩展卸载可能越出受管目录（第 5 组）

- 定位：`src/hetu_stock/skill/extensions.py` `uninstall_extension`（:721-739）：
  `shutil.rmtree(root / extension_id, ignore_errors=True)` 的删除目标直接取自登记表键，
  未校验形态。登记表键被手改或损坏为外部绝对路径（`root / "/abs/path"` 整体替换）或
  `..` 段时，卸载递归删除受管根外目录。正常 install/validate 均拒绝非法 ID，该场景必须
  登记表先被篡改才可达，不夸大为正常使用触发。
- 最小修复：在删除与登记表写回之前，校验 `extension_id` 满足 `_EXTENSION_ID` 形态，
  并按 `_managed_version_dir` 的既有判据核验 `(root / extension_id).resolve()` 仍在受管
  根内；非法目标抛 ExtensionError 受控拒绝，CLI 非零退出并给出明确原因。拒绝时登记表、
  外部文件与其他宿主绑定均不改变（校验先于一切变更）。不改动 `_check_registry_shape`：
  登记表键目前只在本路径触达文件系统删除，其他路径已由 5-A2 形态校验覆盖。
- 保持行为：正常卸载成功；仍有宿主绑定时拒绝卸载；未安装扩展拒绝。
- 验证：tmp_path 沙箱构造受管根与根外哨兵目录，登记表写入外部绝对路径键；
  负例断言非零退出、哨兵文件与登记表字节不变；正常卸载与绑定拒绝正例保留。
  不使用真实登记表或真实用户目录。

### 9.2 check 缺交付时点仍可能验收通过（第 6 组）

- 定位：`scripts/host_acceptance.py` `_cmd_check`（:4131-4133）：`timing` 不完整时只
  转写 `timing["gaps"]`。`delivered_at=None` 时 `summarize_timing`（:769-777）对 None
  边界不产生任何 gap（None 非非法时间戳），得 `complete=False, total_wait=None, gaps=[]`
  → 无 failure 追加 → `passed=True`、`baseline_qualification=established`。
- 最小修复：`not timing["complete"]` 且无具体 gap 可转写时，追加一条可定位失败，指明
  `request_at`／`delivered_at` 哪个缺失；不补造时间、不以零代替未知。有具体 gap 的路径
  不变。计时判定现行对 probe 与正式基线走同一代码路径，本修复保持该结构：不借修复统一
  或收紧 probe／正式基线的既有区别（required scopes、qualification 措辞等均不动）。
- 保持行为：完整合法基线仍通过；既有 timing gap 类型（倒置、零窗、越窗、时钟混基）的
  失败路径不变。
- 验证：使用 `test_host_acceptance.py` 现有合成证据辅助函数：缺 `delivered_at` 场景
  断言非零退出、`passed=False`、`baseline_qualification=not_established` 且失败原因可定位；
  完整基线正例；probe 模式完整计时正例及 probe 缺交付时点的直接受影响行为（同样拒绝，
  维持现有同路径语义）。不重新评定历史批次，不修改原始证据。

### 9.3 本机缺宿主程序时覆盖历史有效夹具（第 6 组）

- 定位：`scripts/capture_host_cli.py` `capture_host`（:90-99）在二进制缺失时返回
  unavailable payload，`main`（:150-156）无条件写回：已有 captured 状态、版本、帮助文本
  和策展名单被静默改写为 unavailable，命令仍 exit 0。
- 最小修复：二进制缺失且既有夹具为有效捕获（`status == "captured"` 且含 `help_text`）时
  不写回，`main` 明确打印「本次未更新，保留已有捕获」并以非零退出标明本次刷新未完成；
  首次无记录（或既有记录非有效捕获，如 unavailable／不可解析）仍按现状记录 unavailable，
  不猜测能力。宿主可用时的正常刷新与策展名单保留语义不变。
- 验证：临时目录合成夹具＋mock `shutil.which`／`subprocess.run`：负例断言已有有效文件
  逐字节不变、输出明示未更新、退出非零；首次无记录正确写入 unavailable；正常刷新正例
  保持。不调用真实宿主，不修改仓库内真实夹具制造负例。

### 9.4 帮助文本描述续行被当成选项声明（第 6 组）

- 定位：`scripts/capture_host_cli.py` `_observed_flags`（:59-63）：声明段取「行首 flag
  起到首个 2+ 空格」，无 2+ 空格的描述续行被整行当声明解析。已证实例（claude 夹具帮助
  文本）：`--permission-prompts` 的描述续行 `--permission-prompt-tool) or "none"` 被提取。
- 最小修复：声明段在占位符抹除后，除 flag token、逗号与空白外含有任何其他文本即判为
  描述续行并跳过；占位符正则扩展容忍变长省略号 `<FILE>...`／`[x]...`（codex 夹具
  `-i, --image <FILE>...` 实证需要，否则误删真实声明）。不建立通用 CLI 解析框架。
- 离线前后对照（规划阶段已只读预览，执行时随测试复核）：codex 零变化；opencode 零变化；
  claude 唯一变化为不再提取 `--permission-prompt-tool`；zcode unavailable 不参与。
- 白名单处置边界：`--permission-prompt-tool` 在 `claude.json` `accepted_flags` 中的唯一
  依据是上述描述续行（git 历史无更早策展记录，该夹具随 `20fb6ff` 引入）；描述引用不构成
  声明证据，但也不能据此断言宿主不支持。处置：从 `accepted_flags` 移除该项并在交付报告
  与本节如实登记「未获证明」；**不**迁入 `checked_absent`（该名单语义为确认不存在）；
  不启动真实宿主补验；不为测试通过猜测支持／不支持。该 fixture 修改属本批显式范围，
  交付报告逐项说明依据。
- 保持行为：短长别名、仅短选项、独立长选项、参数占位符与 Examples 排除；`accepted ⊆
  observed` 与 `checked_absent` 互斥的只读核对断言保留。
- 验证：合成帮助文本负例（续行类 flag 不提取）＋既有合成正例全保留；真实夹具只读对照
  测试随名单处置更新。

## 10. 本批（阻断项）完成条件与停止点

- 四项均先红后绿：新增/扩展测试对当前实现先确认失败，再修复至通过。
- 定向回归（`test_extensions.py`、`test_extension_cli.py`、`test_capture_host_cli.py`、
  `test_host_acceptance.py`、`test_host_cli_protocol.py`）通过；ruff/mypy 改动文件干净；
  `git diff --check` 与规格/计划相对链接检查通过。不默认重跑全量门禁。
- 精确暂存本批文件作本地提交；不推送、不合并、不迁移、不使用 worktree/stash。
- 交付报告逐项给出：已证原因、修复位置、行为变化、红绿证据、正向回归、真实夹具离线
  对照（含白名单依据与未确定部分）、检查结果、提交 SHA、工作区状态、阻断项关闭情况。
- 达到本标准后停止交用户复评；不宣称四组全部闭环、认证通过、性能达标或迁移完成。
- 发现范围外问题仅登记，不夹带修复；必需能力无法取得时保留现场并报告最小缺口。

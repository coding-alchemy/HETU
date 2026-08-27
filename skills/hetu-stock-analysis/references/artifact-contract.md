# 研究产物合同

## 读取时机与职责

首次建立或恢复研究目录时读取本合同，并按需读取[工作包结果结构](work-package-result.md)。本合同只约定目录、命名、`manifest.json`、哈希、修订和追溯；不裁决事实真假、工作包覆盖状态或发布资格，也不提供业务结论模板。`checkpoint.md` 保存任务卡、覆盖变化、回访、用户决定和自检摘要；`evidence.md` 保存来源、时间、授权、主张、计算和冲突；`manifest.json` 是产物索引；`report.md` 是唯一最终报告。三者不复制完整工作包内容，也不承载机器执行游标。

## 固定研究目录

每次独立研究使用：

```text
.hetu/research/<证券标识>-<任务时间>/
├── checkpoint.md
├── evidence.md
├── manifest.json
├── work-packages/
│   ├── W0-task-framing.md
│   ├── W1-subject-verification.md
│   ├── W2-incremental-events.md
│   ├── W3-industry-competition.md
│   ├── W4-business-governance.md
│   ├── W5-financial-validation.md
│   ├── W6-forecast-scenarios.md
│   ├── W7-valuation-expectations.md
│   ├── W8-market-signals.md
│   ├── W9-thesis-counterevidence.md
│   └── W10-report-review.md
├── artifacts/
│   ├── raw/<source-id>/
│   ├── normalized/<work-package-id>/
│   ├── derived/<work-package-id>/
│   └── scripts/<work-package-id>/
└── report.md
```

`<任务时间>` 使用带时区的 `YYYYMMDDTHHMMSS±HHMM`。W1 核验前必须落盘时可使用路径安全的请求标识，并在 `manifest.json` 记录正式证券标识；目录创建后不静默改名。同一任务恢复使用原目录；独立重试使用新目录，不覆盖失败或已交付运行。W0 创建研究根和四类 `artifacts/` 子目录、`manifest.json` 与自己的工作包文件；其他工作包首次开始时只创建自己的文件。

## 文件格式与命名

Agent 撰写内容使用 UTF-8 Markdown；规则化层级数据使用 UTF-8 JSON；表格使用 UTF-8 CSV；许可允许时原始材料保留原格式，不能保存时记录来源定位、获取时间、内容类型和原因。命名规则：

- 原始材料：`<dataset>--<source-id>--<captured-at>--<hash8>.<ext>`，存入 `artifacts/raw/<source-id>/`。
- 标准化产物：`<dataset>--<source-id>--<period-or-asof>--schema-v<version>.<json|csv>`，存入 `artifacts/normalized/<work-package-id>/`。
- 派生产物：`<calculation-or-check>--<input-hash8>--calc-v<version>.json`，存入 `artifacts/derived/<work-package-id>/`。
- 中间脚本：`<purpose>--<work-package-id>--<created-at>--<hash8>.<ext>`，存入 `artifacts/scripts/<work-package-id>/`。

不同来源、期间、Schema、口径或尝试必须形成不同文件；重试不得静默覆盖，相同内容可以复用哈希但仍记录本次尝试。`source-id`、数据集名、Schema 版本和计算版本来自受控清单，不得同名异义。标准化和派生数据保留原始输入定位、主体、期间或基准日、范围、单位、来源时间、获取时间和 `as_of` 判断。命名模板用于提高可读性；manifest 中的完整路径、类型、版本、输入关系和 SHA-256 才是机械身份依据，等价但未满足模板的文件名只记录 warning，不否定内容质量。

## manifest.json 最小 Schema

`manifest.json` 是产物索引，不是事实、工作包状态或发布许可。最小顶层结构为：

```json
{
  "schema_version": "1.0",
  "run": {
    "run_id": "...",
    "requested_security": "...",
    "verified_security": "...",
    "as_of": "...",
    "data_mode": "public",
    "requested_depth": "standard",
    "model": {
      "id": "...",
      "reasoning_depth": "未暴露",
      "reported_by": "host"
    },
    "runtime_skill": {
      "version": "...",
      "sha256": "..."
    },
    "created_at": "..."
  },
  "artifacts": []
}
```

每条 `artifacts` 记录由基础键和条件键构成；基础键对全部条目必须出现且不得改名，条件键只在所属条件下出现、其余情形禁止出现；不得使用下述集合之外的任何键（扩展须先修订本合同）：

```json
{
  "path": "artifacts/raw/<source-id>/<file>",
  "type": "raw | normalized | derived | script",
  "media_format": "json | csv | md | txt | py | <原格式>",
  "work_package": "W0-W10 之一；研究级产物记 W0",
  "sha256": "<该文件 SHA-256 十六进制>",
  "source_id": "<受控来源标识；script 类为 null>",
  "period_or_asof": "<期间或基准日；不适用时写 not_applicable>",
  "created_at": "<ISO 8601 含时区>",
  "schema_version": "<normalized 的 Schema 版本或 derived 的 calc 版本；raw 为 null>",
  "inputs": [
    {
      "path": "<研究根内相对路径>",
      "sha256": "<该输入文件 SHA-256 十六进制>"
    }
  ],
  "status": "adopted | superseded | failed | not_adopted"
}
```

基础键为示例中的全部键；其中 `source_id`、`schema_version`、`period_or_asof` 不适用时显式写 `null` 或 `not_applicable`，不得缺键。

条件键仅以下两个：

- `"script": { … }`：仅 `type=script` 条目必须出现且子键全部必填（`purpose`、`safe_call`、`dependencies`、`environment`、`input`、`output`、`exit_status`、`executed_at`；`dependencies` 可以是非空字符串或非空字符串数组；`status=failed` 的脚本同样完整登记，`exit_status` 记实际非零值）；非 script 条目**不得包含**该键（不写 null）。
- `"failure": "<失败分类与原因>"`：仅 `status=failed` 条目必须出现；非 failed 条目**不得包含**该键（不写 null）。

`inputs` 数组记录该产物的全部本地 artifact 输入：raw 首次采集和直接访问外部端点的采集脚本可以为空数组 `[]`；外部请求的端点、参数或用途写入脚本元数据，不伪造本地输入路径。`normalized`、`derived` 与有本地输入的 `script` 至少一条，每条同时给出 `path` 与 `sha256`；确定性计算不得遗漏实际输入。`script.input` 声明的本地路径必须出现在 `inputs` 数组中；`script.output` 必须明确指向本 manifest 已登记的 raw、normalized 或 derived 产物，不能使用“输出另列”等占位文字。派生文件名中的 hash8 仅为可读提示，manifest 完整输入哈希为权威身份。

SHA-256 只用于验证同一封存版本后来是否变化、manifest artifact 身份、确定性工具输入输出和实际 runtime/Skill/checker 身份。不同运行的时间戳、`as_of`、请求和报告本来可以不同，不比较其报告哈希，不用哈希评分或判断语义等价。

相对路径不得包含 `..`，也不得指向研究根外。目录、哈希或 `manifest.json` 不能可靠写入时属于技术阻断，不交付。

## 产物状态与失败保留

- `adopted`：本次运行采用为当前用途的产物。
- `superseded`：被更新版本替代；旧条目保留并可追溯。
- `failed`：执行失败的脚本或残缺产物；保存输入、输出和退出状态，不静默覆盖。
- `not_adopted`：已登记但不采用的产物（含保留待审的候选脚本）。

失败产物同样登记；重试形成新文件而不是覆盖旧文件。删除、静默覆盖或只保留最终数据都违反本合同。

## 脚本留存逐字提示

凡为生成报告而创建或修改的脚本均按以下提示处理：

> **保留中间脚本：** 本次运行用于二期质量加固实测。凡为生成报告而创建或修改的采集、解析、
> 清洗、计算、校验、修复或补采脚本，均须把实际执行版本和失败版本保存到
> `artifacts/scripts/<work-package-id>/`，并在 `manifest.json` 登记用途、安全调用方式、输入输出、
> 环境与依赖、退出状态和哈希；不得删除、静默覆盖或只保留最终数据。保留不代表批准接入
> canonical Skill。

无脚本时在工作包清单中明确记录"本工作包未创建或修改中间脚本"。安全边界优先于留存：脚本不得包含 secret、绕过代码或无关用户数据；含此类内容的不得保存或执行，并记录安全原因。报告锁定前不得清理、改写或用整理稿替代实际脚本；锁定后不得依据评审结论补写"更可复用"的版本。

## 机械检查器输入/输出

阶段 05 起可用无状态检查器 `scripts/check-run-artifacts.py` 对一次已完成研究做单次机械检查：输入为研究根、最终消息副本与 lock record（定位见 repo 级 `scripts/phase2_lock_run.py`）；输出 `{schema_version, mechanical_status, message_input_status, checks, issues, warnings}`，每条记录含 `code`、`path`、`message`。`issues` 只包含文件缺失或不可读、不安全路径、同一版本哈希不一致、manifest 不能解析、采用产物完全无法定位等硬错误；`warnings` 包含命名风格、等价表头、章节简称或范围、机器追溯不完整等不直接证明内容错误的差异。只有 `issues`、未锁定消息或失败硬检查使 `mechanical_status=FAIL`。检查器不判断自然语言真假、是否应当建表或来源适用性；`PASS` 只是机械硬门禁通过，不是发布许可。

## 报告到来源的追溯链

报告关键事实至少形成以下链条，内部映射集中在 W10：

```text
report.md 章节或关键主张
  → W10 报告映射表
  → 实际 owner 的 W*.md
  → evidence.md 证据项
  → manifest.json 相对路径与哈希
  → raw / normalized / derived / 来源
```

W10 映射表至少记录报告章节、关键主张定位、owner 工作包、证据定位和采用状态。报告章节可以使用完整标题、编号或连续编号范围；owner 可以使用短标识列表或连续范围。关键主张定位应能在目标章节找到，证据定位使用 `evidence.md` 中存在的 `E/F/C/J/U` 编号、`evidence.md#<标题>` 或 manifest 已登记的 `artifacts/` 路径。采用状态使用 manifest 四态或等价中文受控值。确定性计算还须链接 `derived/`、计算版本和实际输入哈希。内部证据 ID 可以用于此链，但不能替代报告里的用户可读引用。核心采用主张完全找不到来源时阻断；人可读来源存在但机器 artifact 定位不完整时记录 warning 并回访 owner。

机械闭合时，映射表表头使用“报告章节、关键主张定位、owner 工作包、证据定位、采用状态”并紧跟
Markdown 分隔行。解析器接受 `1`、`第 1 章`、`1. 任务与时点`、`2–3` 等等价章节形式，以及
`W2–W9` 范围。关键主张定位应是目标章节可搜索原文；证据定位应使用存在的证据编号、fragment 或
已登记 `artifacts/` 路径。表述等价但机器定位不足时记录 warning，不预设业务结论或裁决答案。
映射表只登记能够闭合到 manifest 产物的关键研究主张；纯任务元数据或没有 manifest 产物链的边界说明不要写入映射表。缺口或“未取得”主张如需登记，其 `evidence.md` 证据块必须直接引用已检查但不足的 manifest 产物，或引用另一个能够闭合到该产物的证据编号。

## 回访修订与禁止覆盖

工作包回访时更新当前结论，同时在"修订记录"追加旧值、新值、原因、新证据和受影响下游；不得删除已被下游采用的旧事实、失败或冲突。W10 发现上游问题时通知 owner 回访，不能直接修改上游。上游证据或计算改变时，回访 owner、保留修订记录并递归复核受影响下游。

## 安全边界

`artifacts/` 只保存合法取得、许可允许、与当前任务相关且有复核价值的材料；不保存登录信息、secret、超出授权范围的原文、可执行下载或无关文件。`authorized` 材料的受限 locator、账户、令牌和内部对象路径不得进入任何产物或报告。只读写当前任务明确需要且宿主允许的位置，不扫描无关目录，不覆盖用户文件。

# 五期阶段 01：观测、隔离与及时存证

> 文档版本：v1.0
> 文档版本：v1.0
> 文档状态：已批准；2026-09-08 三轮评审退回（findings S1–S6/R1–R4、recheck S-A–S-D/R-A–R-C、recheck2 S-E–S-G/R-D–R-F），第三轮修复后判定口径收紧：交付尾部与隔离覆盖缺口使结果为"小计＋缺口"，不再宣称完整计量成立，等待第三轮复评。遗留：Codex/OpenCode 接入未完成（未勾选）
> 批准日期：2026-09-08
> 创建／修订日期：2026-09-08
> 适用范围：确定性计量、宿主原生观察、隔离验证及证据保存
> 上级：[路线图](README.md)；设计：[§2.1、§2.6、§2.7、§6](../../2026-09-06-stock-analysis-workflow-v1-phase-5-design.md)
> 执行方式：使用 `superpowers:executing-plans`，各任务按复选框推进。

## 1. 阶段结果、范围与前置条件

建立可以离线复算的观察入口，先消除重复统计、范围遗漏和日志轮转丢证据，再允许采集基线。
计划须已批准；本阶段不优化正式研究、不安装用户生产 Skill、不启动完整股票分析。
宿主探针只做明确能力验证，标注真实模型调用及用量；无法探测的宿主保留缺口。

| 要求 | 任务 | 直接验证 |
|---|---|---|
| 五期 3.1.1–3、长期 3.9.1 | 01.2、01.3 | 请求至交付边界、等待分母、区间并集和未知区间 |
| 五期 3.4.1–4、长期 3.9.6、设计 2.6.1 全表 | 01.2 | 去重、累计重置、父子包含、协调归属、缓存缺失、冲突和独立复算 |
| 长期 2.6.6、3.3 存证、设计 6.1.1 | 01.3 | 实际上下文加载与访问边界，不能靠开关／新目录通过 |
| 长期 3.6.1、5.2.2–5、设计 8.3 | 01.1 | 既有证据映射、case 固定、答案隔离、最小补验及授权清单 |

## 2. 文件与接口

新增 `scripts/host_acceptance.py`、`tests/product/validation/test_host_acceptance.py`、
`tests/product/fixtures/host_acceptance/` 下的脱敏或合成观测文件。
复用 `scripts/phase2_lock_run.py` 的锁定能力和现有产物检查器；本阶段不改其研究语义。
`docs/agent-skill-usage.md` 增加观察入口及局限说明。输入、观察与评审记录写在
`.hetu/validation/phase-5/` 的新执行目录，不写进研究根或覆盖历史收尾目录。

本阶段定义后续共用的外部接口，内部实现保持普通函数，先不拆通用框架：

```text
python scripts/host_acceptance.py probe --host zcode --output <新的观察目录>
python scripts/host_acceptance.py run --host zcode --case <研究侧 case.json> --output <新的观察目录>
python scripts/host_acceptance.py check --evidence <已有观察目录> --output <新的检查结果.json>
```

`probe` 核对原生能力，不分析股票；`run` 只接受一个显式 case，通过原生入口发送自然语言请求、
观察及转达已约定用户操作，不在程序中决定研究域或 W0–W10 下一步；`check` 离线、不启动宿主。
host 值沿用 `codex|claude|opencode` 并增加 `zcode`。成功退出 0，输入／覆盖／能力不成立退出
非零并给出原因；已有输出拒绝覆盖。case 中不允许任意 shell 命令或评审答案。

`run` 接收的 case 只含 case ID、证券、中性请求、`as_of`、模式、深度、复用开关、获准材料
定位及必要的受控触发条件；审批依据和评分期望在观察侧登记。执行者核实当前授权，JSON 中的
一个字段不等于用户批准。原生子进程使用 argv，保留宿主授权及沙箱，不能拼接任意命令。

## 3. 任务 01.1：先映射证据和固定测试输入

- [x] 阅读已批准设计及路线图硬约束；记录工作树用户改动，后续只改列出的文件。
- [x] 从[四期索引](../../../.hetu/phase4-acceptance/index.md)、
  [订正收尾](../../../.hetu/validation/phase-5/20260907-baseline-closeout/closeout.md)、
  [质量评分](../../../.hetu/validation/phase-5/20260907T2306+0800-quality-closeout/quality-scores.md)
  逐项登记质量、深度、计时、用量、归属、隔离各自可用范围，不将索引的当前 PASS 数冒充独立尝试通过率。
- [x] 在新验收目录保存 `coverage.md` 和研究／评审分离的 case 清单。M01–M06、F01–F05 保留
  原请求和期望；B01–B05 仅使用可证部分，不把修复后的质量倒填到原耗时。新增 case 在任何
  受影响研究行为修改前固定证券、时点、L0 定位、模式、深度、故障及期望。
- [x] 核实设计 §6.3 尚列缺口的北交所、消费、券商和特殊状态具名输入：先取已有合法材料，
  记录官方证据及其时点；可只做 L0 材料核对，不执行完整研究。实际状态不成立则该候选不能
  覆盖此维度，不能猜造。无法补齐的特定 case 保留未就绪，先推进不依赖它的观测工具。
- [x] 对每项待补证据注明“离线复算／局部行为／新增完整研究”及最小范围。任何新增完整分析
  移交 02／07 的授权清单；本任务没有完整分析启动次数。

交付：证据映射及研究侧输入与评审侧期望的隔离清单。全套评分量表和样本预期沿用设计，不在
执行后改变；具名输入证据不足不伪装为已齐，不将数值校准重新设为计划批准前置条件。

## 4. 任务 01.2：实现可复算计量与及时落盘

新增纯函数 `summarize_usage(events, *, expected_scopes)`；`events` 是原字段经客户端定义解释
后的字典列表，`expected_scopes` 是本次需覆盖的范围集合。返回 `input_tokens`、`output_tokens`、
`cached_input_tokens`、`complete`、`gaps` 及逐段核对明细；未知为 `null`，可证部分标小计。
最小事件保留来源宿主、会话／请求／消息／工具 ID、段和范围、父子包含、实际模型、角色、
起止、原 usage、累计／增量／最终语义及缓存口径。正文不进入 usage 文件。

- [x] 在 `test_host_acceptance.py` 用 `runpy.run_path` 加载脚本，先加入以下去重和缺范围反例。
  `kind=message_final` 表示每条消息最终值，`input_includes_cache=True` 表示缓存是输入子集。

```python
import runpy

def test_message_blocks_count_once_and_missing_scope_is_visible():
    summarize = runpy.run_path("scripts/host_acceptance.py")["summarize_usage"]
    event = {
        "source": "claude", "session_id": "s1", "segment_id": "g1",
        "message_id": "m1", "scope": "review", "kind": "message_final",
        "input_tokens": 100, "output_tokens": 7, "cached_input_tokens": 80,
        "input_includes_cache": True,
    }
    result = summarize([event, dict(event)], expected_scopes={"review", "research"})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 7
    assert result["cached_input_tokens"] == 80
    assert result["complete"] is False
    assert result["gaps"]
```

- [x] 运行定向 pytest，确认首次失败来自缺失实现；再增加同 ID 不同值、累计 10→30 只能计
  30、恢复重置、父含子／父不含子、核对和修正缺段、混合协调不可拆、缓存缺失与缓存未含输入
  场景。用独立预期值断言，不调用生产汇总函数计算期望。
- [x] 实现最小汇总：先按来源会话及消息身份去重，再按原生语义算段值；冲突无法解释、父子关系
  不明或累计重置无段界则显示缺口，不盲取末值。研究、核对、必要协调都计入，纯观测和锁后
  评分另列；混合不可拆单列。总工具按 tool ID 去重，派发包装不重复加，外部调用子集单列。
- [x] 白名单事件增量写入观察目录，按事件落盘并在段末刷盘；测试写入中断后只能恢复完整记录，
  残行留诊断，不把截断数据当完整。新增时段保存累计快照与语义，不全量复制客户端日志。
- [x] 用存活脱敏证据做真实格式回放：B04 核对 83 行／20 消息应为 33,319 输出 token、46 次
  tool_use；数据定位从 `verifier-usage-events.jsonl` 与 `recount.json` 核实，工具 ID 不在副本
  中时不得从 46 这个答案补造事件。token 和工具计量可分别验证，缺原字段的部分明示未覆盖。
- [x] 加入 `summarize_timing(intervals, *, request_at, delivered_at)`：区间带类别和起止，计算
  各类并集及与总等待的比例；交叠 `[0, 10]`、`[5, 15]` 的并集为 15，不是 20。跨时钟不能
  对齐时不推算。由同一计算结果生成 JSON 和展示表，不再手抄数值、单位或比例。
- [x] 核对者独立用原字段重算小样本；不是复读生成的汇总表。记录输入范围、公式、差额及原因。

验证命令：`.venv/bin/python -m pytest -q tests/product/validation/test_host_acceptance.py`。
预期：重复不增量、冲突不完整、缺失不填零、缓存不重复计入；读取原生日志失败不吞掉错误。

## 5. 任务 01.3：接入原生宿主，证明隔离与留证

- [ ] 实现上述 `probe/run/check`：已完成 ZCode（多会话多 scope attach 观察＋轮转前快照观察＋隔离证据生成）与 claude（无头探针＋隔离探针）的原生接入；**Codex app-server 与 OpenCode 本地服务仍未接入，本项未完成**，不勾选。接口不可自动化时使用可核查的原生交互证据，不静默改 SDK 或杀进程模拟自然语言控制。
- [x] 用原生能力确认研究、核对和必要协调的用量覆盖与父子包含。嵌套研究者不能派发独立核对时，
  由有能力的协调层建立新上下文；观察器不决定核对内容，也不替作者修改结果。
  （2026-09-08 第三轮修复后：受控链路探针实证研究/核对/修正/协调四 scope 全程采集与工具计量；
  交付尾部未采齐被机器判定为缺口，结果为"小计＋缺口"而非完整计量，证据见
  `../../../.hetu/validation/phase-5/20260908-stage01-fix3/chain-probe/`）
- [x] 为从零探针建立独立研究上下文，限制旧研究根访问并核对实际加载的指令／记忆范围；保留
  正式工具、Skill、依赖与授权。用临时旧资料中的无价值标记测试加载及访问，不能删除用户历史。
- [x] 将隔离证据与 usage 分开保存：仅保留获准路径的脱敏别名、动作、上下文来源清单、时间和
  必要加载依据，避免复制完整上下文。无法观察自动注入时标证据不足；usage-only 轨迹不能证明隔离。
- [x] 测试关闭观察后正常研究入口仍可用（本阶段未改 skills/ 与研究行为，git diff 佐证）；丢段/截断时 `check` 返回非零并保留截断行诊断；隔离两案例探针（受限例须 hold、无限制例须检出违规，含反例）已在 2026-09-08 修复中实现并运行：claude 判定 fail＝该宿主无可用路径隔离（如实记录，未取得新证据前维持）；zcode 隔离证据按旧任务数据边界核验研究/核对/修正三个参与上下文的初始加载、历史注入与资料访问控制，verdict=pass（证明范围限于受控种子标记设置，逐次采样须重立；见 `../../../.hetu/validation/phase-5/20260908-stage01-fix3/chain-probe/isolation-evidence-zcode.json`）。check 现逐项判定截断、collection gap（含未消费残缺尾行与未观察的交付端点）、输入/输出总量资格、缓存口径未知、必需范围声明、实际发生而未声明的范围、隔离证据实存性与参与上下文覆盖（S-A–S-E/R-A/R-D–R-F 修复，80 项定向测试）。探针通过与完整基线资格分列输出。删除测试客户端日志后，已保存范围仍可离线复算。
- [x] 文档写明各宿主观测已验证／缺失能力，实际模型与 CLI 分列。按路线图运行阶段工程门禁。

接口产出：02 可用的单 case 观察入口、覆盖判定和持久证据；03／07 可用的宿主操作与访问记录。
没有完整隔离和计量的宿主不能进入正式从零基线采集；其他已验证能力不因此追溯作废。

## 6. 停止、回退与完成

遇到原生能力缺失，只停止该能力认证及依赖它的采样，继续离线实现；未完成 probe 不显示通过。
存证或统计实现错误只回放受影响记录，不重跑原研究。回退限本次观察器与测试改动，保存原始
事件和失败检查输出，不删除研究或全局日志。阶段交付必须同时具备覆盖映射、定向测试结果、
独立复算与真实小探针证据；工程通过不能替代宿主能力证明。

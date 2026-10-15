# material_cases：B01–B18＋三档分级合成场景材料的当前入口

> 用途：为 18 个 case 及其增量变体提供单一维护的原文与当次输入生成；本目录取代
> 已删除的 `phase6_materials/`（原件回源见下节）。
> 维护规范：[测试材料复用与维护方法论](../../../../docs/engineering/test-fixtures-methodology.md)
> 基线：`zn_dev`，`c9ddfaa69547d38f091e9b080f016a6f59343a7a`

## 当前选择与调用

catalog.json 登记 66 个（原 56 个保持，新增 10 个） `case/variant/stage` 选择：B01–B18 的 48 个既有选择
（main 及 B16/B18 的 integration、B17 的 restore/copy/closed/industry/
industry-closed/integration-on/integration-off），加三档分级新增的 8 个选择——
`DQ01/main/01`、`DQ02/missing/01`、`DQ02/model/01`、`DS01/main/01`、`DS01/main/02`、
`DS02/covered/01`、`DS02/missing/01`、`DS02/downgrade/01`（事实在
`inputs/depth/quick.md` 与 `inputs/depth/standard.md`，丙公司 600003.SH 合成主体；
DS01/main/02 为 independent 上下文、as_of 2026-07-02，其余 new 上下文、as_of
2026-06-30）。离线生成当次输入：

~~~bash
.venv/bin/python -m tests.product.skill.material_fixtures B16 main 02 .hetu/validation/fixture-cleanup/inputs/B16-main-02
~~~

生成路径必须是不存在的新目录（已存在即失败）。返回的 `task.md`、`material.md`、
`selection.json` 和当次附件是执行者的全部可读输入；不把源 bundle、`catalog.json`、
`shared.md`、其他变体或 `review-expectations/` 加入执行者清单。读取 canonical 规则的
既有权限保持；helper 只拼接明确选中的原文，不解释内容、不做变量替换或条件执行，
不推断模型深度，也不替代 owner 判断或派发 Agent。

`selection.json` 的 `context` 说明上下文要求：`new`＝独立新上下文仅给当次输入，
`continue`＝同会话续派、保留已收到的前阶段资料，`independent`＝独立新时点任务
（如 B11/main/02 的 2026-07-02、B17/copy/01 的 2026-07-01）。`as_of` 为该选择的
研究截止；B14 为双截面（3-31／6-30）。附件仅随登记它的阶段提供：
`market-cap.json` 只在 B13/main/03，三个故障 PDF 只在 B15/main/01（原字节复制）。

## 评审入口与答案隔离

`review-expectations/B01.md`–`B18.md` 与 `DQ01/DQ02/DS01/DS02.md` 与 `QS01/QS02/SS01/SS02/SS03/SS04.md` 是各 case 的
独立评分依据（含全部判定标准、失败触发与增量节），仅供评审侧使用；执行上下文不可读，`materialize_input` 拒绝把
评审文件路由进输入（`test_fixture_inputs.py` 覆盖）。B17 整合变体 A 的当前输入为
`B17/integration-on/01`（a2 修正版）；歧义初版 `integration-variant-a.md` 不再活跃，
仅历史回源，其“复制时间未证明”的历史限定继续有效。

## 原件回源与保留理由

全部 90 份原文件可从固定提交
`c9ddfaa69547d38f091e9b080f016a6f59343a7a` 按 `tests/product/fixtures/phase6_materials/`
原路径回取（该提交只记录历史来源，不代表远端同步或触发过期）。例：

~~~bash
git show c9ddfaa69547d38f091e9b080f016a6f59343a7a:tests/product/fixtures/phase6_materials/inputs/B17/integration-variant-a.md
~~~

`publication-review/` 九份（发布前独立核对场景）与 `phase5_execution/` 六份
（副本五键 provenance、独立原文、失败信封）内容与其他 case 并非同一原件，
按原字节保留在各自原路径，不为减少文件数强行参数化。

## 工作流范围调整的六组十选择

原56项的对象、事实、附件与评分保持。本批新增10项，总计66项，均context=new：

| 组／选择 | 用途／来源 | as_of |
| --- | --- | --- |
| L1 `QS01/main/01` | 真实Quick明细局部九章呈现；scope-quick.md逐文件允许清单，排除旧报告／评分／修复答案；真实`.hetu`资料为宿主外部依赖，CI不需包含 | 2026-10-11T18:00:00+08:00 |
| L2 `QS02/base/01`、`QS02/missing/01` | 独立合成青禾证券局部指标与资料边界；missing仅复制latest-balance.txt与missing-response.json，无base数值 | 2026-06-30T12:00:00+08:00 |
| L3 `SS01/industrial/01`、`SS01/financial/01` | 青原制造／青泉银行独立合成主体，经营模型与重大风险 | 同上 |
| L4 `SS02/income/01`、`SS02/goodwill/01` | 合成青岭集团交易用途、增长桥与商誉风险；互斥附注 | 同上 |
| L5 `SS03/base/01`、`SS03/missing/01` | 合成青帆局部owner事实，用于自行建立三情景和基础参照／缺数处理 | 同上 |
| L6 `SS04/main/01` | 合成青桐原文条件及人工复查 | 同上 |

SS材料在inputs/depth/scope-standard.md；QS在scope-quick.md。所有生成仅为当次输入，不执行模型、联网或完整股票研究；执行者只读当次目录、其明确许可文件与正常规则，不读本README、源bundle或评分文件。QS01旧根仅为回源；真实Standard复制inputs须指向本次本地副本，遵循artifact-contract五键provenance。

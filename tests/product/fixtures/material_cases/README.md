# material_cases：B01–B18 合成场景材料的当前入口

> 用途：为 18 个 case 及其增量变体提供单一维护的原文与当次输入生成；本目录取代
> 已删除的 `phase6_materials/`（原件回源见下节）。
> 基线：`zn_dev`，`c9ddfaa69547d38f091e9b080f016a6f59343a7a`

## 当前选择与调用

catalog.json 登记 48 个 `case/variant/stage` 选择（18 个 case：B01–B18 的 main 及
B16/B18 的 integration、B17 的 restore/copy/closed/industry/industry-closed/
integration-on/integration-off）。离线生成当次输入：

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

`review-expectations/B01.md`–`B18.md` 是各 case 的独立评分依据（含全部判定标准、
失败触发与增量节），仅供评审侧使用；执行上下文不可读，`materialize_input` 拒绝把
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

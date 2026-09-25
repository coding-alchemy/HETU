# 阶段 03：最终工程验证与四项状态重判

> 文档版本：v1.0
> 文档状态：已执行完成（2026-09-24）；结果见文末补记
> 创建／修订日期：2026-09-24
> 范围：本批修复的可移植性、一次最终门禁及交接
> 依据：[路线图](README.md)、[修复设计 §6–8](../../2026-09-24-zn-dev-remaining-groups-fix-design.md)
> 前序：[01](2026-09-24-01-metering-integrity.md)、[02](2026-09-24-02-extension-and-portability.md)

## 1. 先核对批准映射

- [ ] 对照设计表逐项填修复位置、先失败后通过证据、必要原有回归、仍不可证明的边界。
  6-A1 正常关联可工作，6-A2 未知不被过滤成完整，5-A2 错误不先写后报错，8-B1 只跳
  缺证据的历史审计；任一不成立不得把该项勾成完成。
- [ ] 检查未引入 Skill/CLI 新能力、workflow 变化或可选清理。主线及备份不动；设计批准
  不使第 7、8 组认证与性能专项恢复执行。既有未提交文档保持独立。

## 2. 干净副本的必要检查

先把本批实现/测试的最终修改精确提交到 zn_dev（授权来自获批计划），记录该修复 HEAD。
仅为测试创建 git archive 临时源码副本，不用 worktree、不导出真实 `.hetu/`。

```bash
review_repo="$PWD"
review_snapshot="$(mktemp -d /private/tmp/hetu-remaining-check-XXXXXX)"
git archive HEAD | tar -x -C "$review_snapshot"
test ! -e "$review_snapshot/.hetu"
(cd "$review_snapshot" && PYTHONPATH="$review_snapshot/src" "$review_repo/.venv/bin/python" -m pytest -q -rs tests/product/skill/test_phase5_parallel_contract.py)
```

- [ ] 保存快照路径与提交；检查执行文件确来自副本，现有虚拟环境只供 Python/依赖。
  七项历史审计因缺文件跳过，其他 parallel 功能测试执行通过；不把基线宿主 skip 计入这七项。
- [ ] 在同一临时副本创建 `.hetu/validation/phase-5/20260909-stage02-calibration/impact-map.md`，
  仅写 `invalid audit fixture`，运行单一历史审计负例：

```bash
(cd "$review_snapshot" && PYTHONPATH="$review_snapshot/src" "$review_repo/.venv/bin/python" -m pytest -q tests/product/skill/test_phase5_parallel_contract.py::test_impact_map_writeback_is_append_only_new_section)
```

预期非零且为断言失败，不是 skip；这是刻意负例，单独记录。不得把该文件复制到真实证据目录。
路径存在但不是普通文件时也不得被缺文件条件跳过，可在此副本替换该合成路径再检验一次。
除这些直接受影响检查外，不额外跑两套全量测试。

## 3. 一次最终门禁

在原 zn_dev 上完成必要修复后：

```bash
env -u HETU_HOST_EVIDENCE bash scripts/check.sh
git diff --check
.venv/bin/python scripts/check_docs.py
git status --short --branch
```

check.sh 已含文档检查；若执行前已无文档补记，上述独立 check_docs 可省略；若门禁后只
补记结果，则只运行文档检查与 diff 检查，不重复全量。日志保存 /private/tmp 并记录路径。
MANIFEST 本批不应改变；若生成漂移先说明原因，不把未知变更夹入提交。

- [ ] 记录实际 passed/failed/skip；每类 skip 说明环境条件；未知失败与既有限制分列。
- [ ] 门禁失败先定位直接影响，仅修获批问题并重验受影响项；不启动模型补验或扩大要求。
  无新语义变化或未解失败时不重复检查。
- [ ] 结果补记放对应阶段及路线图，更新总索引真实状态；完整完成才移入已完成计划。
  没完成的保留未关闭项，不为了清理文档删除有效要求，不触碰历史原始证据。

## 4. 重新判断四项状态并交付

| 原组 | 必须分别回答 |
|---|---|
| 5 扩展管理 | 生命周期实现、修复验证、E3A/缺工具/E1 限制、是否具备迁移条件 |
| 6 计量与宿主验收工具 | 正确关联与结算能力、实际支持的结构、未知拒绝边界、迁移阻断 |
| 7 质量与宿主认证 | 沿用有效支持声明，暂缓组合与证据缺口，不新增通过声明 |
| 8 性能与校准 | 历史审计可移植性、采样/校准/达标分别登记，不将 skip 当性能通过 |

输出本地提交、实际测试、未关闭问题和迁移建议。修复完成、具备迁移条件、认证通过、性能
达标四种结论分别判断。用户复评前不自行迁移、合并、推送或强推；本轮最终停止于交接。

## 5. 执行结果补记（2026-09-24）

- 批准映射逐项核对成立：6-A1 正常关联可导出（含真实库只读冒烟）、6-A2 未知不被过滤成
  完整、5-A2 错误不先写后报错、8-B1 只跳缺证据的历史审计。未引入 Skill/CLI 新能力、
  workflow 或可选清理；main、备份、真实安装与 `.hetu/` 原始证据未动；上一轮
  execution-implementation.md 状态订正保持未提交、未混入本批。
- 干净副本检查（git archive 临时副本 `/private/tmp/hetu-remaining-check-*`，用后已删）：
  无 `.hetu/` 时恰七项 writeback 带理由 skip、其余 29 项通过；损坏内容负例
  （`invalid audit fixture`）与“路径存在但为目录”负例均失败（非 skip）。
- 最终门禁（原 zn_dev，`env -u HETU_HOST_EVIDENCE bash scripts/check.sh`，日志
  `/private/tmp/hetu-final-gate-20260924.log`）：**1592 passed, 6 skipped**，exit 0
  （ruff/mypy/文档链接/MANIFEST/Skill validate 全过）。skip 分类：zcode 夹具
  unavailable 1 项、zcode 未安装 2 项、三宿主已捕获致未安装约束不适用 3 项——均为带理由
  的宿主可用性条件 skip；七项 writeback 在本机有证据，照常执行通过，不计入 skip。
  `git diff --check` 通过。MANIFEST 无漂移。
- 门禁后仅补记本文档与索引，按约定只重跑文档检查与 diff 检查。

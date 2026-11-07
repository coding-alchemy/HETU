# E/P 价值策略卡（第二优先，有条件进入）

> 状态：条件准备；仅在免费历史财务时点合格后进入测试。当前 PE/TTM 接口不能静默替代原口径。

## 来源

- Liu, Stambaugh, Yuan, *Size and Value in China*（2019）。
  原文：https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2019/04/01-18-Stambaugh.Published.BlumePrize.pdf
- 原文样本：2000–2016 年 A 股；E/P 在所比较估值指标中更有解释力；原文结论主要为因子层面证据。

## 原规则（以原论文定义为参照）

- 样本：剔除最小市值 30% 的股票等原文样本规则。
- 信号：最近已报告的扣除非经常性损益后净利润 / 总市值（E/P）。
- 排序与持有：按月排序；原文多空因子证据不能直接当作多头净收益。

## 本地适配

| 项目 | 原方法 | 本地适配 | 理由 |
| --- | --- | --- | --- |
| 股票池 | 剔除最小市值 30% | 保留同一规则，按当时可知总市值实现 | 依赖合格历史市值 |
| 盈利字段 | 最近已报告扣非净利润 | 只使用公告时点不晚于截止的历史版本；晚修订不覆盖过去已知值 | 信息时点约束 |
| 方向 | 原文多空证据 | 一期只输出 E/P 排序信号，暂不产生目标权重 | 不解释为可实现收益 |

## 数据缺口与进入条件

- 需要历史原始财务、公告时点与修订可得时点（`cn_a_financial_reports`），以及时点市值（`cn_a_market_cap`）。
- 缺公告时点、期间不可证或只有当前 PE/TTM 时返回受阻，保留待补候选；不用当前财务回填历史。
- E/P 不合格只限制自身，不阻断数据已合格的低波动工作。

## 运行入口

```bash
quant/.venv/bin/python quant/research/ep_value.py \
  --data-root .hetu/quant --snapshot-id <已合格快照> \
  --as-of "<信息截止时间>" --signal-date <月末交易日> \
  --out .hetu/quant/research/<实验名>
```

## 当前真实状态与下一步

- 状态：`preparation_only`；`economic_status` 固定 `not_evaluated`。
- 2026-10-16 来源核验结论（证据：`.hetu/quant/qualification/capability-gap-20261016/ep-financials-probe.json`、`em-mainfina-600519.json`）：
  - 可得部分：东财 F10 主要指标（RPT_F10_FINANCE_MAINFINADATA）提供每报告期
    扣非净利润（`KCFJCXSYJLR`，元）与公告日期（`NOTICE_DATE`）；业绩报表
    `stock_yjbb_em` 按报告期一次返回全市场（20260630 期 11,444 行含北交所）。
  - 不可得部分（进入条件不满足）：两接口每报告期仅保留一行最新值，**修订版本链
    不保留**——修订后原值不可回取，"原始公告、修订版本及各自可得时点"无法证明。
    E/P 维持 blocked；缺的是修订时点证据，非免费来源本身。
- 下一步：出现可证明修订链的免费来源（如逐公告快照存档）后再评估进入测试；
  完整经济验证在二期。

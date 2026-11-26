# 板块换手历史分位

标识 `sector_turnover_percentile`，定义版本 1。来源为《A股日线量化策略全景分析》§4.7。观察对象为市场、行业、主题，按所选交易日计算，结果单位为百分点。

## 介绍、公式与口径

估值用当期行业总市值／过去十二个月归母净利合计（非正净利缺证），在该行业过去 L 期各自在对应时点可知的同口径估值中取分位。成交额占比为行业额／可比市场额，输出 T 与 T−k 的差。行业换手以成交股数／可交易流通股数汇总后取自身历史分位。资金流代理取已披露可比净流入额／同期成交额，保留供应商定义，不从日 K 线猜买卖方向。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 membership_coverage、daily_bars、memberships、shares、calendar、security_status。工作台显示本次实际参数；默认参数为 `{"window": 2}`。

## 教学演算与易误读之处

汇总股数得前期 10/100、当期 9/100，当期分位 0 点；同为 10/100 时 50 点。

已执行的公式反例为 当期股本仍 20，把当前量 1.8 改为 2，同 .10 取中位 50。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.sector_etf.sector_turnover_percentile`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `sector_turnover_percentile.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[sector_turnover_percentile]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

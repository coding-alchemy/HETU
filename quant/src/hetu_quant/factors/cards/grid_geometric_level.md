# 等比网格

标识 `grid_geometric_level`，定义版本 1。来源为《A股日线量化策略全景分析》§4.11。观察对象为个股、ETF、组合，按所选交易日计算，结果单位为元/股。

## 介绍、公式与口径

对给定 G₀、合法层数 k 及步长 Δ 或比例 g，分别输出 G₀+kΔ、G₀(1+g)^k，可由当前收盘定位所在相邻两档。等比基准和比例、等距步长须为正。超过已设边界标出，不推断成交、库存或盈亏。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、security_status。工作台显示本次实际参数；默认参数为 `{"base": 100, "step": 0.1, "level": 2, "max_levels": 5}`。

## 教学演算与易误读之处

基准 100，g=0.1，k=2 得 121；现价 115 的两档为 110、121，k=−1 得 100/1.1；g≤0 报参数缺口。

已执行的公式反例为 第负一层按除以 1.1。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.risk.grid_geometric_level`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `grid_geometric_level.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[grid_geometric_level]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

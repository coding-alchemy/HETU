# 风险预算波动权重

标识 `risk_budget_vol_weight`，定义版本 1。来源为《A股日线量化策略全景分析》§4.1、4.8、4.12、§5.1。观察对象为个股、ETF、组合，按所选交易日计算，结果单位为比例。

## 介绍、公式与口径

ATR 取最近 n 日 max(高−低、abs(高−前收)、abs(低−前收)) 的算术平均，OHLC 用可比价格尺度。实现波动复用 `realized_volatility`。给定组合收益序列计算波动后取 min(1,目标波动／(估计波动+ε))；ETF 正得分／(自身波动+ε)、策略风险预算／自身波动分别输出未归一比例。ε、单位、现金／上限与归一选择在示例中固定，因子库不产生委托。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、security_status。工作台显示本次实际参数；默认参数为 `{"vol_window": 2, "risk_budget": 0.1}`。

## 教学演算与易误读之处

风险预算 0.1、自身波动 0.2，未归一比例 0.5；波动变 0.4 得 0.25，零波动给无效分母。

已执行的公式反例为 年化波动翻倍至 0.4，风险预算仍 0.1。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.risk.risk_budget_vol_weight`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `risk_budget_vol_weight.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[risk_budget_vol_weight]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

# ATR风险股数

标识 `atr_position_size`，定义版本 1。来源为《A股日线量化策略全景分析》§4.1。观察对象为个股、ETF、组合，按所选交易日计算，结果单位为股。

## 介绍、公式与口径

第一项输出趋势指示值乘 min(上限, 目标波动／当时估计波动) 的理论比例。ATR 仓位的本地约定为方向 × 每笔风险额／（每股 ATR × 指定倍数），单位为股。均不生成交易委托。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、security_status。工作台显示本次实际参数；默认参数为 `{"window": 2, "direction": 1, "risk_amount": 1000, "multiple": 2}`。

## 教学演算与易误读之处

方向 +1、风险额 1000 元、每股 ATR 为 2 元、倍数 2，得 +250 股；方向 −1 得 −250 股，ATR 或倍数为零给缺口。

已执行的公式反例为 方向反转，风险额和 ATR 不变。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.risk.atr_position_size`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `atr_position_size.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[atr_position_size]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

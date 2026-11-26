# 总市值

标识 `total_market_cap`，定义版本 1。来源为《A股日线量化策略全景分析》§4.5。观察对象为单只证券，按所选交易日计算，结果单位为人民币元。

## 介绍、公式与口径

T 可知的总股本、自由流通股本分别乘同期原收盘；可用同口径已证市值。自由流通股本的定义须由来源明确。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、shares、market_cap、security_status。工作台显示本次实际参数；默认参数为 `{}`。

## 教学演算与易误读之处

T 原价 10、同日可知总股本 100 万股，市值 1,000 万元，直接同口径市值凭据亦可核对

边界对照为 只剩后披露股本时给时点缺口；股本以“万股”供给时验证换算

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.market_activity.total_market_cap`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `total_market_cap.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[total_market_cap]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

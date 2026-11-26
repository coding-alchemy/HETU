# 上涨家数占比

标识 `breadth_advance_share`，定义版本 1。来源为《A股日线量化策略全景分析》§4.7、4.10。观察对象为市场、行业、主题，按所选交易日计算，结果单位为比例。

## 介绍、公式与口径

在各自 T 日有据成员中，上涨、下跌股数分别除以有效可比数（平盘进分母）；当日可比收盘严格高／低于此前 N 日收盘极值的家数；当日收盘严格高于含 T 的长期均线的比例（原文示例 200 日）。每项附各自有效数、所需历史条数及停牌／缺证。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、memberships、membership_coverage、security_status。工作台显示本次实际参数；默认参数为 `{"window": 2}`。

## 教学演算与易误读之处

五只可比证券 3 涨、1 跌、1 平，3/5=0.6；将一只上涨改平得 2/5，缺全集时检查范围标签。

已执行的公式反例为 复算时按测试修改 `daily_bars` 的原始记录，期望读数为 `0.4`。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.breadth.breadth_advance_share`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `breadth_advance_share.json`。公式反例节点为 `quant/tests/test_factor_sensitivity.py::test_changed_known_fact[breadth_advance_share]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

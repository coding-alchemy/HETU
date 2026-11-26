# 炸板代理比例

标识 `failed_limit_up_share`，定义版本 1。来源为《A股日线量化策略全景分析》§4.10。观察对象为市场、行业、主题，按所选交易日计算，结果单位为比例。

## 介绍、公式与口径

以前收、历史板块规则、证券状态、除权与价位精度确定当日上下限，分别计封在上下限的收盘家数；按连续交易日收盘封涨停的天数输出家数分布。炸板代理为最高价触及涨停但收盘未封数／日内最高价触及涨停数。缺可靠限价、无涨跌幅限制时分别说明，日线不能给出盘中开板次数。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、price_limits、memberships、membership_coverage、security_status。工作台显示本次实际参数；默认参数为 `{"window": 3}`。

## 教学演算与易误读之处

A、B 最高价均触上限，仅 B 收盘未封，得 1/2；两者均收封得有效 0，无一触及时分母无效。

已执行的公式反例为 复算时按测试修改 `daily_bars` 的原始记录，期望读数为 `0`。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.breadth.failed_limit_up_share`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `failed_limit_up_share.json`。公式反例节点为 `quant/tests/test_factor_sensitivity.py::test_changed_known_fact[failed_limit_up_share]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

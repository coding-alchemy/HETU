# ETF双动量

标识 `etf_dual_momentum`，定义版本 1。来源为《A股日线量化策略全景分析》§4.8。观察对象为ETF，按所选交易日计算，结果单位为百分点。

## 介绍、公式与口径

在 T 当时已上市的指定 ETF 池对 L 日风险调整收益排序，同时返回通过／未通过的绝对趋势过滤。过滤参数明确为自身收盘高于长期均线或区间收益大于零，档案记录实际选项。复用单项公式，保留排名和过滤两个分量。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、memberships、membership_coverage、security_status。工作台显示本次实际参数；默认参数为 `{"window": 2, "vol_window": 2, "filter": "positive_return", "filter_window": 3, "epsilon": 0.001, "group": "ETF"}`。

## 教学演算与易误读之处

A 收盘 100、101、103.02，B 为 100、99、97.02，风险调整收益由低至高为 B、A，分位 0、100，区间收益过滤 B 假 A 真，保留名次。另设 100、110、105，正区间收益为真，严格高于三日均线为假；T 后上市者不入池。

已执行的公式反例为 A、B 都为正收益，B 因较低波动排名第一，两者正回报过滤均通过。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.sector_etf.etf_dual_momentum`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `etf_dual_momentum.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[etf_dual_momentum]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

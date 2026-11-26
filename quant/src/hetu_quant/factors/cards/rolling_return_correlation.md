# 滚动收益相关

标识 `rolling_return_correlation`，定义版本 1。来源为《A股日线量化策略全景分析》§4.8、4.12、§5.1。观察对象为配对，按所选交易日计算，结果单位为相关系数。

## 介绍、公式与口径

对两个指定序列的 n 个同步可比日收益计算样本相关系数，输出估计窗口及样本数。任一侧常数、跨市场日历或币种不能按声明口径对齐时报缺口。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、security_status。工作台显示本次实际参数；默认参数为 `{"window": 3}`。

## 教学演算与易误读之处

同步三日两序列分别为 (0,1%,2%)、(0,2%,4%)，相关 +1、样本数 3；第二腿改为 (0,−2%,−4%) 得 −1，常数或交易日对不齐返回相应缺口。

已执行的公式反例为 第二腿报价用 USD，第一腿人民币。

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.risk.rolling_return_correlation`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `rolling_return_correlation.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[rolling_return_correlation]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

# 行业规模中性化

标识 `neutralized_score`，定义版本 1。来源为《A股日线量化策略全景分析》§4.5。观察对象为当日原池或指定分组，按所选交易日计算，结果单位为人民币元。

## 介绍、公式与口径

在当期可证截面按事前给定的上下分位截尾，使用线性分位插值。中性化用含截距、历史行业哑变量（留一基类）与时点总市值对数作等权回归，返回因子值残差，可指定仅行业或仅规模。秩不足或覆盖未知时给出具体原因。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、memberships、membership_coverage、market_cap、classifications、security_status。工作台显示本次实际参数；默认参数为 `{"neutralize": "both", "component": "traded_amount", "mode": "daily", "group": "POOL"}`。

## 教学演算与易误读之处

两行业 A,A,B,B，对数市值 (1,2,1,2) 来自原价×股本，金额 (6,7,14,19)。含截距、一个行业哑变量和对数规模的等权 OLS 残差为 (1,−1,−1,1)。仅行业为 (−.5,.5,−2.5,2.5)，仅规模为 (−4,−6,4,6)

边界对照为 令行业与规模完全共线则全模型报秩不足；未来才生效的行业或市值证据不能替代 T 的暴露资料

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.cross_section.neutralized_score`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `neutralized_score.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[neutralized_score]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

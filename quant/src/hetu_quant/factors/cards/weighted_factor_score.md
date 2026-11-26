# 加权因子分

标识 `weighted_factor_score`，定义版本 1。来源为《A股日线量化策略全景分析》§4.5、§5.2。观察对象为当日原池或指定分组，按所选交易日计算，结果单位为分数。

## 介绍、公式与口径

同一时点截面 (x−中位数)/(1.4826×MAD+ε)，MAD 使用绝对离差的中位数。合成分数为 Σω·z。对同一 T 日各必需分量作上述稳健截面标准化，然后按档案声明的方向统一高分端（每项符号只用一次），按事前权重求和。缺项给出受限分量。保留原始值、z 值、符号和权重。去极值、行业／规模中性化是可复核的前置变换。等权可直接算；历史 IC 权重和风险优化权重只作为带来源的既定输入使用，本期不估计效果或求解组合。

使用本次所选信息截止时已知的版本，默认截止为所选交易日北京时间 15:00。所需事实表为 daily_bars、calendar、shares、memberships、membership_coverage、security_status。工作台显示本次实际参数；默认参数为 `{"window": 1, "epsilon": 0.001, "components": {"price_momentum": {"weight": 0.75, "direction": 1}, "turnover_rate": {"weight": 0.25, "direction": -1}}, "group": "POOL"}`。

## 教学演算与易误读之处

三股价格动量及换手均为 (.01,.02,.03)，分别源自首日价 100、末日 101／102／103，成交 1／2／3 股、流通股各 100。令 a=.01/(1.4826×.01＋ε)。末股两项原始 z 都为 a、方向 +1／−1、权重 .75／.25，得 .5a，等权为 0

边界对照为 一项无原数则报告该分量；替换带来源的事前权重 .25／.75 得 −.5a。回显各原值、z、方向和权重；历史 IC／风险优化权重只读取现有明确输入

## 代码、逻辑自测和效果

实际 Python 函数为 `hetu_quant.factors.cross_section.weighted_factor_score`。证据中的实际输入、组件和结果可随教学样例复算。真实源缺少表、期间或可知字段时按本次计算显示具体缺口。该读数可作为未来同类对象相对同期基准研究的候选解释量，目前效果未验证，证据引用为空。

工作台选择「教学样例」可下载 `weighted_factor_score.json`。公式反例节点为 `quant/tests/test_factor_formula_evidence.py::test_formula_discriminator[weighted_factor_score]`。反例基于该原始样例变更，收据列出变化、独立预期与实际结果。

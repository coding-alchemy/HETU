# 行业来源导航

按问题读取相关条目；具体统计表、历史及方法须当次核对，不因目录缺席拒绝合理取证。研究规则见[研究规则](research-rules.md)，证据记录及实际交付见[报告约定](report-contract.md)。公开网页／官方下载优先，API不是必经步骤；只用公开或明确授权资料，不注册试用、采购或绕过访问限制。

## 1. 既有入口与定位动作

以下保留2026-10-18历史记录，仅说明当时取得范围，所有API均未实测；不是已接入清单。后续核验另记时点、目标表／系列、实际数据／方法、覆盖及限制，不能把方法页升级为历史数值可得。

| 来源 | 定位与核对 | 2026-10-18状态及边界 |
| --- | --- | --- |
| [国家统计局发布](https://www.stats.gov.cn/sj/zxfb/)、[海关查询](https://online.customs.gov.cn/mySearch/) | 按行业／产品找生产、利用率、进出口数量和金额原表、分类及历史附表；核行业／品目、单位、累计／单期、季调及可比修订 | 发布／服务目录已读，细分表待选；海关统计子站此前读取受限。总量不代目标品种，价值口径利用率不当实物能力 |
| [上期所](https://www.shfe.com.cn/)、[大商所](https://www.dce.com.cn/)、[郑商所](https://www.czce.com.cn/) | 从统计／公告／行情和库存栏目实际链接找目标品种价格、仓单／库存历史及交易规则，核规格、时点、地点、税运、报价／成交和覆盖 | 候选，具体表、许可及更新方式待核；交易所库存不等于全市场，现货／期货产品不能静默替换 |
| [USDA WASDE](https://www.fas.usda.gov/data/wasde)、[农业农村部](https://www.moa.gov.cn/) | 按作物／养殖及地区找发布期供需附表与历史版本，核作物年度、单位、预测／实绩、单产／面积及修订 | WASDE仅定位、未完整下载；农业农村部候选，不能因目录列出便称已核目标数据 |
| [上海航运交易所](https://www.sse.net.cn/) | 找所选航线运价／指数实际历史及编制方法，核船型、航线、币种、运价覆盖与时点；运力和货量另核原源 | 候选，航线、方法和数据权限待核；指数不能单独说明供需原因或实现收益 |
| [EIA Open Data](https://www.eia.gov/opendata/)、[国家能源局电力工业统计](https://www.nea.gov.cn/20250723/d7a5d6cc916e40bcb04c7d7e74d03213/c.html) | 能源按地区／品类定位生产、消费、库存及价格原表；电力核装机、发电、利用小时、观察期及覆盖，不将装机当发电 | EIA目录和API说明已读，API需注册、未实测，不代表公开网页都须key；能源局该表已读，不能外推全部能源系列 |
| [中国气象局共享目录](https://www.cma.gov.cn/zfxxgk/gknr/wjgk/qtwj/202302/P020230224377864419023.pdf) | 有农业／运输命题才选地区、期间、观测定义及具体数据集，核空间覆盖和发布／获取时点 | 目录已读，具体序列未得、数据集候选；目录不证明行业损失 |

## 2. 美国原油定位

从上述EIA入口的Petroleum Supply Monthly、Petroleum & Other Liquids栏目实际链接找月度原油生产、进口、出口、炼厂原油投入、商业库存与Cushing WTI现货价格历史及方法；先读元数据再采用表号或系列代码，不猜接口。公开网页、已链下载与可追溯转载可按定义使用，API需权限时不视作全部渠道必经。

分别核美国地域、原油／凝析油覆盖、日均／月总量、月末库存、是否排除SPR；炼厂原油投入是加工需求，不叫成品油终端消费。WTI核原发布方、Cushing、美元／桶、月均现货及汇总定义，不能用期货收益或其他产品替换。各序列保留末期和必要窗外基数，日均跨月累计按天数转换，库存及价格不加总。

本节初始为候选定位，尚未确认具体URL、表号、目标历史窗口和方法；实际读取后在此追加成功路径与适用范围，不把候选写成来源通过。具体路径失败仅限制实际目标，有所链原表或新线索才继续，真实无新线索／权限／预算限制时准确收尾，不重复泛查或预设商业采购。

## 3. 美国原油公开月表实读（2026-11-07）

已实际读取EIA官网原表、历史HTML及适用定义，未使用EIA Open Data API、未注册、未付费。该次[Petroleum Supply Monthly](https://www.eia.gov/petroleum/supply/monthly/)明确“Data for August 2026”、2026-10-30发布，供给／贸易／炼厂／库存共同末月为2026-08；后续研究仍按自身截止核发布期，不继承旧末月或旧数值。

从[美国原油供需平衡月表](https://www.eia.gov/dnav/pet/pet_sum_crdsnd_k_m.htm)实际链接的历史列进入，核页题、月频和单位，再选本题窗口及同比／相邻基数。历史HTML可能整页带长历史，只处理当前问题所需期间，不逐年追加全历史请求。该次展示2025-09—2026-08、比较输入选2024-09—2026-08；六个目标的原文和定义均可回读。

| 目标与已实读历史入口 | 原表字段与读法 |
| --- | --- |
| [美国原油产量](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?f=M&n=PET&s=MCRFPUS2) | `MCRFPUS2`，Field Production of Crude Oil，千桶／日；另已读同表月总量`MCRFPUS1`可核日均转换。核原油／lease condensate定义，产量不是有效产能 |
| [原油进口](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCRIMUS1&f=M) | `MCRIMUS1`，千桶／月；与产量日均不同，需除该月实际天数才比较日均。贸易覆盖为50州及DC与境外／属地间，按原定义使用 |
| [原油出口](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCREXUS1&f=M) | `MCREXUS1`，千桶／月；出口不是美国国内消费，与进口分列，不将贸易流直接当终端需求 |
| [炼厂和调和商原油净投入](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCRRIUS1&f=M) | `MCRRIUS1`，Refinery and Blender Net Input of Crude Oil，千桶／月；用于加工需求，不改称全部石油消费或成品油终端需求 |
| [不含SPR的商业原油月末库存](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=MCESTUS1&f=M) | `MCESTUS1`，Ending Stocks excluding SPR，千桶、月末存量；不能累计，不当含战略储备总库存，具体地域／库存覆盖仍回表注 |
| [Cushing WTI月均现货价](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?f=M&n=PET&s=RWTC) | `RWTC`月频，Cushing, OK WTI Spot Price FOB，美元／桶；不是期货收益、储备估值或其他油品。原发布方页面与具体价格底层来源分别标明 |

供需定义可从[月表Definitions](https://www.eia.gov/dnav/pet/TblDefs/pet_sum_crdsnd_tbldef2.asp)和[本期PSM Explanatory Notes](https://www.eia.gov/petroleum/supply/monthly/pdf/psmnotes.pdf)读取：原油／凝析油、炼厂投入、月末primary stocks、调整与地域定义均须匹配实际系列，通用库存定义含SPR不能盖过目标字段明确的excluding SPR。月总量、千桶／日及百万桶先作单位／天数转换；不要把部分分量差额无条件叫完整供需缺口。

价格从[Spot Prices](https://www.eia.gov/dnav/pet/pet_pri_spt_s1_m.htm)及[定义](https://www.eia.gov/dnav/pet/TblDefs/pet_pri_spt_tbldef2.asp)核现货／FOB。旧页面可读但其静态正文不一定展开平均方法；该次实际沿官网加载的[footnotes.js](https://www.eia.gov/petroleum/prices/js/footnotes.js)读到[价格footnotes.json](https://www.eia.gov/petroleum/prices/data/footnotes.json)：`_groups.spot_prices`标底层Refinitiv，EIA按相应期间日收盘现货价格的非加权平均计算周／月／年价。应记录该元数据与适用产品，不能据一条series代码省略方法核对。

必要更新可从[Weekly Petroleum Status Report](https://www.eia.gov/petroleum/supply/weekly/)实际所链[overview](https://ir.eia.gov/wpsr/overview.pdf)、[Table 1 CSV](https://ir.eia.gov/wpsr/table1.csv)及[WTI日价](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=pet&s=rwtc&f=d)取得；本次已读取，仅用于明确日期的更新事实，不混为完整月度观察，也不据月／周不同估计精确归因。

上述只证明这些公开HTML、PDF、CSV和方法路径的当次取得范围，所有API仍未实测，不认证EIA全部产品、完整初值／修订史、其他行业或其他宿主。未来查询仍须检查具体返回、版本及截止时点；供应调整、SPR或周报只有在本题需要时采用，不变成每篇行业报告全取清单。

## 4. 需求兑现、资金周转与财务库存的定位线索

仅在研究规则第2节对应命题触发时选取以下资料，已有数据仍沿用历史表、适用比较、释义和图形规则。以下为未实测的候选定位动作，具体表、附注、历史版本和访问权限须当次核对；不保证已取得或已接入。

| 命题 | 应定位的原表／披露与核对动作 |
| --- | --- |
| 特定下游、客户群或投资项目驱动需求 | 从主管部门／行业统计的分用途、地区、客户群或渠道表取规模及兼容分母；必要时沿下游年度／中期业务披露查资本开支指引、实际购建支出、采购与交付表，结合本题所需的项目进度、投运及利用资料。核年度指引与年内实绩的期间差别、项目身份及环节，行业份额与企业客户占比分开；对经销采购、供应商交付及终端验收按项目／批次识别重复覆盖，避免重复计需求 |
| 利润、回款、扩产或融资传导 | 优先定位统计机构分行业财务状况表及调查制度，核收入、营业利润／利润总额／净利润定义、应收和库存覆盖。缺适用行业项时，选覆盖明确的企业样本，沿利润表、现金流量表及应收、合同资产、现金流调节、付款条件等相关附注核回款与周转；沿项目融资披露、资金到账及投资进度核宣布与执行。区分销售收现和经营现金流净额、合并范围及样本变化，不把样本金额放大为行业总体，不由差异直接归责 |
| 企业财务库存或混合库存指向供给／积压 | 定位存货分类及计量附注，提取原材料、在制品／委托加工、产成品的期初期末账面余额、减值准备和净额；实物量另找同覆盖库存／经营表。核成本方法、价格／汇率／范围变动、库位、货权、订单占用、加工及可交付状态；缺少相应数量或状态时，限制实物变化或可售供给结论，不以账面总额代替 |

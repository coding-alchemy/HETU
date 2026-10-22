# 个人 A 股量化研究数据方案评估报告

> 文档版本：v0.4；状态：信息源核查资料，采购决策暂缓
> 初稿／修订日期：2026-10-18／2026-10-21；基础资料核查于2026-10-19，行业／主题及日线／分钟扩展资料核查于2026-10-19—20，必要供应商事实复核于2026-10-21，具体范围见§1.1、§10.3
> 文档职责：维护供应商接口、价格、调用限制、历史覆盖及证据；项目选型、容量、存储和更新方案以[系统数据与数据源方案](A股量化系统数据与数据源方案.md)为阅读入口
> 历史案例背景：沪深 A 股（主板／创业板／科创板），含退市股，2016-04-29至2026-10-13；36个月月收益低波动多头、市值加权、月调仓（30／42个月对照），RQAlpha回测；不代表新增沪深北二十年归档已核验
> 证据分级：**【官方文档】**=公开说明或源码，尚不等于本项目实测；**【项目实测】**=本项目保存的响应及证据；**【社区反馈】**=用户报告，未必经维护方确认或当前复现；**【待验证】**=公开证据不足；**【估算】**=注明假设的离线量级计算
> 本次边界：v0.3仅归集既有资料；v0.4只复核Tushare、RQData、JQData与历史池、PIT股本市值、公司行动相关的官方网页／源码，未扩查二十年分钟。未使用账号／Token、调用证券数据、注册／申请试用／购买／联系供应商或运行研究，不改变现有0元数据支出上限

---

## 0. 结论摘要

1. **存在明确PIT股本处理的市值字段。** RQData `market_cap_3`明确在公告公布后调整股本，不覆盖报告截止日至公布之间的历史值；普通`market_cap`不能与之混用。这仍不等于全部历史修订均可审计。[官方说明](https://www.ricequant.com/doc/rqdata/python/stock-mod)
2. **历史退市覆盖须按产品/接口核对。** Tushare `stock_basic(list_status='D')`有退市列表，但不能据此保证所有退市对象在每个行情、股本、行动端点均完整。2000积分档为付费候选，不应称为免费档。
3. **BaoStock涨跌幅复权不等于本项目账户总收益，但不能推断“现金分红未进入调整”或“系统性低估”。** 应按原价、行动和研究再投资约定核对，见第2.2节。[官方算法](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)
4. **Tushare存在应验证的分红滞后反馈。** Issue #1910有2026-08-05用户报告，未据此声称本项目已复现或维护方已确认当前状态。方案进度、实施日期与特别分红仍需样例核对。[原反馈](https://github.com/waditu/tushare/issues/1910)
5. **先补齐信息，再采购。** Tushare个人200／500／1000元年档和独立分钟2000元年权限已有公开价（§8.1）；RQData/JQData适用套餐仍需报价。调用方式、行数/字节/频次、历史批量与缓存权分别核对，不能以SDK同源推定零适配，也不以未经验证的工期或“小误差可接受”作采购依据。

---

## 1. 覆盖范围与核查状态

### 1.1 历史案例与新增范围分开登记

| 范围 | 已有证据及核查时点 | 尚未证明的范围 |
| --- | --- | --- |
| 原沪深历史研究：2016-04-29至2026-10-13 | 2026-10-19核对下表及§2—3、§8的文档／源码；少数项目问题有历史实测记录 | 文档可查不等于原池全窗合格；市值公开版本、行动完整性、退市跨端点覆盖仍须分别核验 |
| 新增二十年全A股日线：沪／深／北及窗口内退市对象 | 2026-10-19—20核对日线、身份、因子接口；`stock_basic`文档列SSE／SZSE／BSE，日线支持历史查询 | 未完成二十年逐市场、逐对象样例与覆盖验收；目录有BSE不证明日线／因子全覆盖，也不能以2016年起的`bak_basic`证明更早身份 |
| 行业／主题及历史成员 | 2026-10-19核对§8.5所列接口、权限、行限和历史起点 | 不同体系、公开时点、成员版本及更早历史待核；当前成员不能外推历史 |
| 分钟1／3／10／20年、1／5／15／30／60分钟 | 2026-10-20核对Tushare分钟价目、字段／限额与RQData行情文档；RQData2005年起点仅有历史简介材料，见§8.6 | 未进行分钟账号样例或全A股覆盖验证；Tushare2009年起点不足截至2026年的完整二十年；RQData当前账号、早期退市及北交所覆盖待核 |
| 当期价格、账号额度与离线使用权 | 已有公开价和文档额度见§8；未用账号实测 | RQData／JQData适用报价、试用权限、实际扣额及订阅到期使用权仍未核齐 |
| 历史池、PIT股本市值与行动的必要供应商事实 | 2026-10-21复核Tushare积分价目与相关端点、RQData字段／身份／配额文档、JQData官方SDK源码；结果见§10.3 | 仅网页核查，未取得账号响应或新增数据资格；既有其他范围的核查时点保留 |

本次扩展只补充来源资料，**不将原范围的核查结论升级为新范围已通过**。各K线粒度是否有量额字段见§8.6；系统容量估算不属于来源交付证明。

### 1.2 历史研究所需字段与接口

标记：✅ 对应接口/字段有官方说明（不是本项目已验收）｜🟡 证据不足/历史范围待核｜❌ 已明确不支持。未找到说明不等于明确不支持；不能从一个接口的缺失推断整个平台没有此数据。

| 需求域 | TuShare Pro(2000 分) | TuShare Pro(5000 分) | BaoStock | AKShare | JQData(聚宽) | RQData(米筐) | Wind/iFinD/Choice |
|---|---|---|---|---|---|---|---|
| A1 历史每日总市值 | ✅ `daily_basic.total_mv`（万元） | ✅ 同左；仍有单次/频次限制 | 🟡 本次未核得逐日市值接口 | ✅ `stock_value_em`/`stock_zh_valuation_baidu`；覆盖待核 | ✅ `valuation`/`get_valuation` | ✅ `get_factor` | 🟡 需具体产品/字段证明 |
| A2 历史股本（总/流通/自由流通） | ✅ `daily_basic`三口径 | ✅ | 🟡 本次未核得逐日三口径接口；`query_stock_industry`是行业接口 | ✅ 历史/候选接口存在，版本待核 | 🟡 逐字段口径核对 | ✅ `get_shares`列`total/circulation_a/free_circulation`；PIT及修订另核 | 🟡 需字段表 |
| A3 市值 PIT 口径说明 | 🟡 交易日期不能证明公告版本 | 🟡 | — | 🟡 未证完整PIT | 🟡 日期查询及修订语义待核 | ✅ `market_cap_3`有公告后调整说明；修订溯源仍待核 | 🟡 不作行业整体推断 |
| B1 退市股列表与身份 | ✅ `stock_basic(list_status='D')` | ✅ | 🟡 跨端点历史覆盖待核 | 🟡 某股本接口成功空不等于没有退市名录，须分别核对 | ✅ 历史日期筛选，实际覆盖待核 | ✅ 历史日期筛选，实际覆盖待核 | 🟡 需具体接口/覆盖 |
| B2 按历史日期取当时股票池 | 🟡 `list_date/delist_date`及公开版本须区分 | ✅ `bak_basic`有日期化列表，身份链另核 | 🟡 需版本/覆盖核对 | 🟡 需版本/覆盖核对 | ✅ `get_all_securities(date=...)` | ✅ `all_instruments(date=...)` | 🟡 需具体接口/覆盖 |
| C1 分红送转（含进度字段） | ✅ `dividend`含进度与多日期；滞后为社区反馈待复核 | ✅ | ✅ SDK含公告/登记/除权/派息/红股上市日期；内容覆盖另核 | ✅ 东财/巨潮/新浪候选 | ✅ `finance.STK_XR_XD`，版本待核 | ✅ `get_dividend`/`get_split`，覆盖待核 | 🟡 需具体字段/权限 |
| C2 配股 | 🟡 `dividend`不能当全部行动接口；其他产品待核 | 🟡 | 🟡 | ✅ 新浪配股接口；覆盖待核 | 🟡 需具体表 | 🟡 需具体接口 | 🟡 需具体接口 |
| C3 换股/吸收合并/退市补偿 | 🟡 待核 | 🟡 | 🟡 未证完整产品 | 🟡 可找原件，结构化范围待核 | 🟡 | 🟡 | 🟡 不凭品牌判完整 |
| C4 特别分红覆盖 | 🟡 按实际事件核对 | 🟡 | 🟡 本项目发现过漏报 | 🟡 | 🟡 | 🟡 | 🟡 |
| D1 未复权日线 OHLCV | ✅ `daily`（vol=手，amount=千元，pre_close为除权昨收） | ✅ | ✅ SDK接口存在；完整覆盖另核 | ✅ 多源接口存在 | ✅ | ✅ | 🟡 按具体数据产品核对 |
| D2 复权因子 | ✅ `adj_factor`，限制见第8节 | ✅ 仍须核接口限制 | ✅ 免费；未核得无限频保证；涨跌幅复权 | ✅ 对应因子/复权价口径需核 | ✅ factor | ✅ | 🟡 需具体产品 |
| D3 停复牌历史 | ✅ `suspend_d` | ✅ | ✅ 日线`tradestatus` | 🟡 需核对具体历史状态端点，不用缺行情猜停牌 | ✅ `get_price`含paused | ✅ | 🟡 需具体接口/覆盖 |
| D4 ST/风险警示历史 | 🟡 `stock_st`3000积分起，2000-01-01起历史 | ✅ `stock_st`，实际完整性待核 | ✅ 日线`isST`，不靠名称猜测；覆盖待核 | 🟡 | ✅ | ✅ | 🟡 需具体产品 |
| D5 历史涨跌停价 | ✅ `stk_limit`，盘前产品另核权限 | ✅ | 🟡 须结合历史规则/板块/ST/除权参考价，不能只套固定比例 | 🟡 | ✅ `get_price`含high/low_limit | ✅ | 🟡 需具体接口/覆盖 |
| D6 交易日历 | ✅ `trade_cal` | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 需具体数据产品 |
| E 财务 PIT（低波动不依赖，财务策略必需） | 🟡 有ann_date，全部修订保留待核 | 🟡 | 🟡 完整PIT未证 | 🟡 具体源另核 | 🟡 date查询不等于全部修订保留 | ✅ `get_pit_financials_ex(date, statements='all')`文档；实际覆盖待核 | 🟡 需具体产品，不推断最强 |

---

## 2. 候选方案对照

### 2.1 TuShare Pro（积分类付费，HTTP/Python API）

- **官方文档**：https://tushare.pro/document/1?doc_id=13（积分规则）；https://tushare.pro/document/2?doc_id=25（stock_basic）；doc_id=27（daily）；doc_id=32（daily_basic）；doc_id=103（dividend）
- **关键接口与字段**：
  - `stock_basic(list_status='L'/'D'/'P')`：含list_date、delist_date、market等。默认仅L，退市须另取D；按所需市场与普通股身份筛选，不把未来退出信息倒填过去。【官方文档】
  - `daily_basic`：`total_share/float_share/free_share`（万股）、`total_mv/circ_mv`（万元），2000积分起；5000档常规数据无每日总量上限，仍有单次行数和流控限制。【官方文档】
  - `bak_basic`（5000积分）：官方称2016年起的历史每日列表；可用于历史池重建候选，不直接等于身份公开版本链。股本单位为亿股，不能与`daily_basic`万股混用。【官方文档】
  - `dividend`：`div_proc`、record_date、ex_date、pay_date、div_listdate、imp_ann_date、cash_div_tax等；官方称2000年起。不能将这个分红送股接口扩展解释为全部配股、换股和退市处置均已覆盖。【官方文档】
- **已知缺陷**：
  - 【社区反馈】Issue #1910于2026-08-05报告600519分红实施记录更新滞后。保留为购买前样例，未做当前账号复测；行情跳空只能辅助对拍，不能单独证明派息事实。
  - 【社区反馈】Issue #1683的T00018/600018存在历史证券身份及跨端点映射争议，评论也指出二者可能是不同标的。未经权威定义，不自动改码，也不把投诉当已确诊错误。
  - `adj_factor`当前公开页未量化所谓“1次/小时”，不沿用旧社区限制；实际账号/接口限制见第8节与待验证表。
- **费用**：积分档与独立权限分别计费，不是按调用消耗积分；统一价格、额度与接口门槛见§8.1。
- **macOS/Python**：Python SDK/HTTP可本地调用，不强制其回测平台；本地缓存、批量导出及到期保留权须核对数据条款，删除原“缓存无限制”的无依据承诺。

### 2.2 BaoStock（免费，Python API）

- **官方文档**：https://www.baostock.com（知识库/接口文档）
- **覆盖**：Python匿名登录查询日线/交易日/证券资料/分红/复权等；`adjustflag=1/2/3`区分后复权/前复权/不复权。项目已有资产可复用，不能把查询默认日期当全部历史覆盖起点。
- **复权口径**：涨跌幅复权按除权参考价调整，不能等同本项目持仓现金/再投资收益，也不能推断完全忽略现金分红。按官方公式，合成前收100、派息10、除权参考价90、当日收99时，复权单日收益10%，持股加现金收益9%；这已反驳“总是低估”。应复用项目原价和行动口径核对，不重复加算现金。[官方算法](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)
- **缺口**：本次未核得满足要求的逐日市值/股本PIT接口；分红SDK有多日期但完整内容须核验，退市应逐端点核对。
- **费用/限制**：项目已执行数据费0元；没有“不限频”或永久可用的保证，项目曾遭接口风控。免费、免注册不能推导无限请求或不受使用条款约束。
- **定位**：免费层的日线+复权因子主力源 + 付费源的对拍源。

### 2.3 AKShare（免费，多源爬虫聚合）

- **官方文档**：https://akshare.akfamily.xyz（各接口均标注目标地址）
- **分红相关候选**：`stock_fhps_detail_em`（东财）、`stock_dividend_cninfo`（巨潮）、`stock_history_dividend_detail`（新浪分红/配股），具体字段及当前覆盖按官方端点文档核对，不将各端点字段合并成每个源均支持的承诺。【官方文档】
- **结构性风险**：
  - 本质是网页接口聚合，无 SLA、无历史版本承诺；字段语义随源站调整可能漂移；
  - 你已实测的"股本变动混入未完成计划""特别分红遗漏""退市返回空"均属于此类源的固有风险，换接口（东财→巨潮→新浪）只能部分缓解；
  - 有`stock_value_em`与`stock_zh_valuation_baidu`历史市值接口，不能写成只能重建；是否满足2016起、含退市及PIT仍须核验。项目已发现的问题限定为已测试来源，不扩大为所有AKShare端点均有同一缺陷。
- **定位**：历史市值、分红/配股和公告的免费候选或交叉核验源；依照具体上游判断，不先指定主源。[官方字段与历史查询说明](https://akshare.akfamily.xyz/data/stock/stock.html)

### 2.4 JQData（聚宽本地数据 SDK，付费）

- **官方入口**：https://www.joinquant.com/default/index/sdk；SDK：https://github.com/JoinQuant/jqdatasdk
- **能力**：`jqdatasdk` 本地调用（Win/Mac/Linux）；`get_price` 日线返回 paused、high_limit、low_limit、factor；`get_all_securities(date=...)` 支持按历史日期取当时上市证券范围（含退市），对应B域历史身份查询需求；`get_fundamentals` 提供逐日市值/估值截面。【官方】
- **PIT**：按date查询不等于全部历史修订保留。2019年官方FAQ说明部分报表采用最新差错覆盖，是否已升级须确认；不据该旧说明给2026全部市值产品归类。[历史FAQ](https://www.joinquant.com/community/post/detailMobile?postId=21247)
- **费用/试用**：当前适用公开金额、试用期限/历史范围/额度未核实，**需询价**；删除旧论坛“几千元”和旧试用范围作为现行依据。平台VIP不等于本地JQData数据额度。
- **注意**：SDK按数据条数核对配额；具体账号值与接口上限见第8节。代码可本地运行不等于缓存/到期保留权已确认。

### 2.5 RQData（米筐，付费）

- **官方入口**：https://www.ricequant.com/doc/rqdata/python/manual （账号/许可证、客户端及配额说明；当前试用资格和期限待确认）
- **能力**：`get_factor(..., 'market_cap_3')`有公告后调节股本说明；`all_instruments(type='CS', date=...)`、`get_price`、`get_dividend`、`get_split`分别提供候选所需域。不能用聚宽的`get_all_securities/get_fundamentals`代替米筐接口说明。[股票接口](https://www.ricequant.com/doc/rqdata/python/stock-mod)、[通用接口](https://www.ricequant.com/doc/rqdata/python/generic-api)
- **接入边界**：RQData与RQAlpha有关联，但本项目已有`LocalBundleDataSource`与独立准入、行动、费用语义，不能推定购买即零适配，也不需要从零重建已通过的引擎链路。
- **费用**：本次未核得个人所需字段组合的公开金额，**需询价**；不再填写未经证实的“数千元/年”。

### 2.6 掘金量化（MyQuant 终端，个人版免费）

- **官方页面**：https://www.myquant.cn/terminal
- **费用**：官方管理员FAQ介绍基础版0元，但适用权益、历史扩容及到期条件须确认；未核实的1900/38000元不作为当前本项目价格。[官方社区FAQ](https://bbs.myquant.cn/)
- **能力/缺口**：官方SDK资料存在每日市值/股本接口；当前Python和macOS支持、全历史及退市范围、批量导出和独立RQAlpha使用仍待核。免费基础版不能推导全部历史和离线落地均免费。

### 2.7 商业终端（Wind / iFinD / Choice）

- **Wind / iFinD / Choice**：分别核对终端、Client API、Server API和落地数据产品。当前个人字段组合金额及权限**需询价**，不能由旧终端报价推算API价格，不能未经核实断言个人不可购买或某家是唯一支持macOS的终端。
- 历史股本PIT、特别分红、换股/补偿、导出和配额均需具体产品说明，不凭品牌给“全部支持”或“最强”结论。[Wind接口服务](https://www.wind.com.cn/mobile/WDS/sapi/zh.html)、[Choice数据服务](https://choice.eastmoney.com/product/datacenter)

---

## 3. 历史时点与数据口径

### 3.1 市值的三种口径在本报告候选中的落位

| 口径 | 定义 | 可获得性 |
|---|---|---|
| ①当时最新已公开股本计算的市值 | 信号时点按公开版本形成市值 | RQData `market_cap_3`有明确处理说明；具体字段/历史覆盖/修订溯源须分别核验 |
| ②事后重建的实际股本市值 | 按实际有效股数重建历史 | 不能给所有平台整体归类；要求供应商说明具体字段，若与研究定义不同须取得取舍授权 |
| ③后来修订覆盖的历史值 | 当前响应覆盖了较早版本 | 任何日期化存储都不能自动排除此风险；保存历史日期不等于保存历史版本 |

### 3.2 对策略与接入的影响边界

- 市值版本差异会改变权重、基准及后续交易，未经样例对比不能断言影响只在小数点。披露差异不能代替既有准入，改变研究定义仍须用户批准。
- 退市缺失、行动版本错配及复权/总收益口径混用均需核验；仅行情跳空或接口成功不能证明完整行动事实。
- 本项目已有原价账户、行动、撮合和Parquet输入包，接新源应评估字段映射、证据及增量差异；删除无本项目依据的1–2天/3–5天承诺。

---

## 4. 项目组合决策入口

本文维护单个供应商与产品的可核事实，不再单独维护项目组合或采购排序。免费、低预算与PIT试用组合、价格预算和取舍集中在[系统方案§3.2](A股量化系统数据与数据源方案.md#32-推荐组合价格和额度)；该处价格为本文§8的选型摘要，供应商事实变化先更新本文，再同步受影响摘要。

原v0.2的组合条件已并入系统方案：免费多源不代表全PIT可用；2000积分不含所有状态；升级由字段与额度需要决定；RQData／JQData分别核验，不推定零适配或无依据工期。

---

## 5. 缺口分类

### 付费后有望解决的缺口
- 退市列表、日线、市值和行动获取的工程量有望降低，但须逐端点验证，不能写“200元即解”；
- 历史每日市值/股本截面（daily_basic / get_fundamentals）；
- 停复牌、涨跌停、交易日历的一站式获取；
- 数据字段接入与现有RQAlpha链路可复用，不因更换供应商自动归零。

### 仍需核验的能力及独立软件工作
- **PIT范围**：米筐有明确PIT市值字段，所有字段/所有修订可追溯的范围仍需确认；未满足原要求不得以声明近似代替；
- **分红更新/特别分红**：Tushare有社区反馈，其他源也需在已有难例上验证，不把未复测投诉写成当前已确诊；
- **预案误当实施**：必须依赖 `div_proc`/方案进度字段 + ex_date 与行情跳空互验，这是流程问题不是数据采购问题；
- **配股、换股、吸收合并、退市补偿**：分别询具体产品/字段；不推定机构终端覆盖最全。数据事实与本项目引擎支持分开；
- **订阅到期后数据保留权**：各家条款不明，必须询价书面确认。

---

## 6. 可直接发给供应商的询价问题与最小样例清单

### 6.1 询价问题（通用模板）

1. 历史每日总市值/总股本（先列所需起止日、市场及退市范围；原研究案例为2016-04起）：股本字段按"业务生效日"还是"公告日"入库？后来修订（如分红方案调整、回购注销）是否回填历史记录？能否提供某股某日的原始入库记录样例？
2. 退市股票：退市后其日线、复权因子、分红记录是否仍可查询？查询窗口有无时限？
3. 分红送股：是否覆盖特别分红（非年度常规分配）？实施方案修订后，旧版本记录是否保留可查？配股、换股、吸收合并、退市补偿分别在哪个接口/数据表？
4. 个人版价格：人民币金额、计费周期；是否有独立收费模块（如公司行动、历史导出）；每日调用额度与历史批量导出是否另计费。
5. 使用条款：是否允许本地缓存为 Parquet/CSV 离线研究？订阅到期后已下载数据能否继续保留使用？是否要求绑定终端/回测平台/证券账户？
6. 是否支持 macOS 与独立 Python 环境（不经由贵司回测平台）？
7. 二十年沪深北日线及1／3／10／20年分钟数据：各市场、粒度和退市对象分别从何时覆盖？能否提供分市场覆盖清单、缺失说明、集合竞价／盘后及量额单位定义？批量接口或文件交付的单次、分钟、日额度及费用分别是多少？不能只回复平台最早历史年份。

### 6.2 最小验证样例清单（购买前要求供应商演示或试用自查）

下表保留原沪深历史研究的难例，不能代表二十年全A股或分钟覆盖。新增范围还需选定早期年份、北交所上市期间、早期退市对象及分钟时段边界的有界样例，按实际对象生成请求清单；本节不构成执行授权。

| 样例 | 验证点 |
|---|---|
| 600519（贵州茅台） | 2022、2023特别分红是否完整；2025–2026回购注销的计划、实际完成、公开日期分别如何映射到股本/市值，不预设应按事后生效日而非公开版本调整 |
| 688001（华兴源创） | 2022 年可转债转股导致的股本逐日变化是否连续可查；2026 年分红方案调整后，旧版本是否仍可追溯 |
| 600005（武钢股份）、600068（葛洲坝） | 退市/换股对象的日线、最后交易日、退市日期、换股事实记录是否可查 |
| 000001（平安银行）、300001（特锐德） | 深市主板/创业板常规分红送转记录完整性；用作跨市场口径一致性基准 |
| 任选 3 只区间内退市股 | `daily_basic`/`get_price` 在退市后是否仍返回完整历史 |

---

## 7. 主要参考来源（基础核查2026-10-19；扩展资料见§8.5—6；旧反馈保留原日期）

**官方文档/官方页面**
- TuShare 平台积分规则：https://tushare.pro/document/1?doc_id=13
- TuShare stock_basic（含 list_status='D' 退市列表）：https://tushare.pro/document/2?doc_id=25
- TuShare daily_basic（股本与市值字段、2000/5000 积分门槛）：https://tushare.pro/document/2?doc_id=32
- TuShare dividend（分红送股、div_proc进度字段；不据此证明其他行动覆盖）：https://tushare.pro/document/2?doc_id=103
- TuShare stk_premarket（盘前股本/涨跌停、pre_close 除权口径）：https://tushare.pro/document/2?doc_id=353
- Tushare stock_st（3000积分起，2000年起历史）：https://tushare.pro/document/2?doc_id=397
- Tushare公开价格/通用频次：https://tushare.pro/document/2?doc_id=290
- BaoStock复权因子简介（算法与本项目账户收益分开）：https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf
- RQData PIT市值及行动字段：https://www.ricequant.com/doc/rqdata/python/stock-mod
- 聚宽 JQData 申请与 SDK：https://www.joinquant.com/default/index/sdk ；https://github.com/JoinQuant/jqdatasdk
- 掘金量化终端版本与数据商城：https://www.myquant.cn/terminal
- AKShare 官方文档（分红配股等接口及目标源站）：https://akshare.akfamily.xyz

**社区反馈（不是本次复测或维护方确认）**
- TuShare Issue #1910（分红数据更新滞后，600519 案例，2026-08）：https://github.com/waditu/tushare/issues/1910
- Tushare Issue #1683（历史证券代码身份/映射争议，不擅自改码）：https://github.com/waditu/tushare/issues/1683
- JQData 收费反馈（2023，"大几千一年"、试用仅限近一年数据）：https://coding.m.imooc.com/questiondetail?cid=494&qid=295934
- 聚宽官方雪球号（2018，"正式版一年几千元"）：https://xueqiu.com/9666063336/114288771

**旧价格资料（仅历史背景，不作当前金额或区间依据）**
- Wind 单账号 39,800 元/年报价（上海证券报报价单，2023-09）：https://k.sina.com.cn/article_1905628462_7195952e01901hzbq.html
- Wind/iFinD/Choice 机构价格对比（雪球转引上证报，2023-12）：https://xueqiu.com/2533768760/273051657

> 说明：本次未核得JQData、RQData、iFinD、Choice适用于本项目的当前个人金额，标注“需询价”；不据此断言它们在所有渠道均没有公开价，也不以论坛旧报价填现价。

---

## 8. 调用方式、返回单位及限制

以下为**文档级核对**，示例未执行。只示意数据调用，不代表字段已取得使用权限或已通过本项目准入。SDK一次调用、上游数据请求、网络尝试、返回记录和计费/扣额单位分别登记。

### 8.1 Tushare：按调用次数与端点行数分别限制

Python使用Token认证，返回DataFrame；同一账号的SDK和HTTP不能当两份配额。官方HTTP教程使用`http://api.tushare.pro`，POST JSON含`api_name/token/params/fields`，返回`code/msg/data.fields/data.items`。本次未核验HTTPS地址或发送任何Token。[Python教程](https://tushare.pro/document/1?doc_id=131)、[HTTP教程](https://tushare.pro/document/1?doc_id=130)

```python
# 仅方法示例，未执行；取得授权后再配置凭据和查询范围。
import os
import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
caps = pro.daily_basic(trade_date="20260930",
                       fields="ts_code,trade_date,total_share,total_mv,circ_mv")
```

**通用个人权限表：**

| 积分档 | 公开分钟频次 | 公开每日调用上限 | 个人价格 |
| --- | --- | --- | --- |
| 120 | 50次/分 | 8000次，仅非复权日线 | 0元/年 |
| 2000以上 | 200次/分 | 每API 100000次调用 | 200元/年 |
| 5000以上 | 500次/分 | 常规数据无每日总量上限 | 500元/年 |
| 10000以上 | 常规500次/分，特色300次/分 | 常规数据无每日总量上限；特色按产品核对 | 1000元/年 |

“次调用”不是“数据行”。单次行数另看下表；独立权限另计。通用流控的跨API共享方式、账号实际获批条件待核；端点有独立更严限制时，不能直接套通用值。[官方权限/价目](https://tushare.pro/document/2?doc_id=290)

2026-10-21复核发现，`daily`端点另写基础积分500次／分，与通用表免费档50次／分存在表述差异；实际账号适用频次仍待确认，不能据此将免费档提高到500次／分。[日线说明](https://tushare.pro/document/2?doc_id=27)

| 接口 | 最低积分 | 单次最大行数（公开页） | 接口独立说明/历史范围 |
| --- | --- | --- | --- |
| [`daily`](https://tushare.pro/document/2?doc_id=27) | 按账号权限，免费档仅此未复权日线 | 6000 | 可按交易日取全市场或按代码／区间查询；历史及各市场实际覆盖须核验，量额单位见§8.6 |
| [`trade_cal`](https://tushare.pro/document/2?doc_id=26) | 2000 | 本次未核得固定数值 | 按交易所／期间查询，保留交易与非交易日记录 |
| [`daily_basic`](https://tushare.pro/document/2?doc_id=32) | 2000 | 6000 | 每日15–17时更新；宣称全历史，未列精确最早日；其他独立频次未另列 |
| [`stock_basic`](https://tushare.pro/document/2?doc_id=25) | 2000 | 6000 | **50次/分**；文档列SSE／SZSE／BSE；默认仅L，须按范围另取D/P等并核对身份，不据目录推断其他产品覆盖 |
| [`bak_basic`](https://tushare.pro/document/2?doc_id=262) | 5000正式权限 | 7000 | 2016年起；股本亿股，daily_basic为万股 |
| [`dividend`](https://tushare.pro/document/2?doc_id=103) | 2000 | 2000 | 2000-01-01起，20–21时更新；按代码/公告/登记/除息/实施公告等条件查询 |
| [`adj_factor`](https://tushare.pro/document/2?doc_id=28) | 2000 | **未披露** | 支持单股全历史或单日全市场；5000可高频，独立数值未量化 |
| [`stock_st`](https://tushare.pro/document/2?doc_id=397) | 3000 | 1000 | 2000-01-01起，每日9:20更新；不是2000积分档已含 |
| [`suspend_d`](https://tushare.pro/document/2?doc_id=214) | 2000 | 5000 | 2000-01-03起；5000频次更高，独立数值未量化 |
| [`stk_limit`](https://tushare.pro/document/2?doc_id=183) | 2000 | 5800 | 文档称总量不限制，分钟流控未量化；含A/B股和基金，需筛实际研究对象 |

**A股历史分钟为独立权限：**公开2000元／年，不包含在上述积分档中；`stk_mins`支持1／5／15／30／60分钟，要求按单代码、频度及起止时间查询，返回OHLC、`vol`和`amount`。单次最多8000行、500次／分钟、**滚动24小时30万次**；不能套日线免费档或2000积分档的日额度。价目表标历史自2009年起，完整二十年及量额语义见§8.6。[权限／价格](https://tushare.pro/document/2?doc_id=290)、[分钟参数／返回／限额](https://tushare.pro/document/2?doc_id=370)

**价格仍需分清：**公告信息独立权限1000元/年（历史10年以上），盘前股本独立500元/年（较短历史，不默认覆盖2016起）。不因研究需要股本就默认购买盘前产品。3000／6000积分最低适用开通价格应向权限中心确认，不自行按比例推算；10000积分档虽达到6000门槛，不代表它是最低成本。缓存、离线回放、到期保留、退款及发票条件由实际条款核实。

### 8.2 RQData：按流量核额度，SDK与HTTP格式不同

Python在已配置的许可/账号下初始化，返回DataFrame等结构；可查询当日`bytes_limit/bytes_used/remaining_days/license_type`。[使用手册](https://www.ricequant.com/doc/rqdata/python/manual)

```python
# 仅方法示例，未执行；ids须按原历史池重建，不是手选研究池。
import rqdatac as rq
rq.init()
ids = ["600519.XSHG", "000001.XSHE"]
quota = rq.user.get_quota()
caps = rq.get_factor(ids, "market_cap_3", "2026-09-30", "2026-09-30")
pool = rq.all_instruments(type="CS", date="2026-09-30", market="cn")
actions = rq.get_dividend(ids, "2016-04-29", "2026-10-13")
splits = rq.get_split(ids, "2016-04-29", "2026-10-13")
```

HTTP文档：账号密码JSON POST至`https://rqdata.ricequant.com/auth`取得token，再向`https://rqdata.ricequant.com/api`提交包含`method`与方法参数的JSON，请求头放`token`。token有效2小时，新token使旧token失效。数据响应是**CSV文本**，不是通用JSON；HTTP忽略`expect_df`。SDK认证与此token不能混同。[HTTP认证/数据格式/限制](https://www.ricequant.com/doc/rqdata/http/data-process)

| 项目 | 已核信息 | 仍待确认 |
| --- | --- | --- |
| SDK额度单位 | 手册按实际压缩传输流量计量，配额字段单位为字节 | HTTP是否相同扣额；错误/重试/重复取数如何扣量 |
| 日额度 | 手册介绍试用每日1G；商城FAQ列示1G/日条件 | 实际个人套餐和试用资格；G的确切字节数以账号值为准，不能把示例当全部套餐 |
| 设备/并发 | 商城FAQ列单账号同时单电脑、最多3线程 | 是否适用所选套餐、Mac与独立引擎环境是否算多设备 |
| HTTP单请求 | 过大get_factor会拒绝；文档近似为1因子×全市场8年、CSV约250M | 非固定行数/250M保证；所选字段和代码的有效分块，其他端点上限 |
| 频次 | 本次未核得统一每秒/分钟数值 | 不写“不限次”；确认实际流控、并发和退避要求 |
| 价格 | 本次未核得适用字段组合的公开金额 | 个人年/月费、PIT/行动附加权限、批量导出/增量费用 |

`get_factor`没有月频或离散日期列表参数：要么按信号日分别查询，要么取连续日频数据后本地筛月末。CSV字节、压缩计量字节和本地Parquet大小不能互换估算。商城条款和价格应以对应报价确认，不由本表推定适用。[商城/询价入口](https://www.ricequant.com/welcome/store/)

### 8.3 JQData：额度按条数，客户端还可能展开分页和重试

本地`jqdatasdk.auth`认证，`get_query_count()`查询当日`total/spare`。它们是条数，不是调用数；实际每类产品、字段、空响应/失败的扣额规则仍须确认。[SDK](https://github.com/JoinQuant/jqdatasdk)

```python
# 仅方法示例，未执行；原价fq=None，覆盖和证据仍须核验。
import os
import jqdatasdk as jq
jq.auth(os.environ["JQDATA_USER"], os.environ["JQDATA_PASSWORD"])
quota = jq.get_query_count()
pool = jq.get_all_securities(["stock"], date="2026-09-30")
q = jq.query(jq.valuation.code, jq.valuation.market_cap)
caps = jq.get_fundamentals(q, date="2026-09-30")
```

另有`get_valuation(codes, start_date, end_date, fields=...)`历史序列接口；`market_cap`为亿元、`capitalization`为万股，需转换为本项目CNY/股。行情`get_price(..., fq=None)`、状态`get_extras('is_st', ...)`与行动`finance.STK_XR_XD`分域核验，不能以平台回测能运行替代导出字段证明。[当前API源码](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/api.py)

| 项目 | 本次可核值 | 适用边界/待核 |
| --- | --- | --- |
| `get_fundamentals` | 客户端单次10000行 | 应按明确的代码组/date条件切分，不假设返回无限行 |
| `finance.run_query` | 当前master客户端上限5000行 | 是检索时源码值，不是服务端或所有已安装版本保证；未核实前不沿用旧4000值 |
| `run_offset_query` | 源码每页10000，最多20页，达到边界有截断警告 | 一次Python调用可展开多次db_query；不据函数成功判全部取齐 |
| 首次元数据 | finance首次查询可能取得表名/schema | 另计，不能只统计业务函数次数 |
| 请求重试 | 当前源码默认3次尝试、超时300秒，可配置；429为流控错误 | 下游统计实际网络尝试；不把默认重试带入尚未获批的真实取数 |
| 账号条数 | `get_query_count()`可查 | 本次未取得实际账号额度/每类数据扣额公式 |
| 并发/频次 | 未核得可靠现行统一数值 | thread-local不表示无限并发；登录数/IP数/秒和分钟上限待确认 |
| 价格/试用 | 公开试用/购买页受区域限制，未绕过 | 当前金额、期限、历史范围和条数仍需确认；不套旧一年/100万条或平台VIP价格 |

源码依据：[SQL生成](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/finance_service.py)、[table.py](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/table.py)、[client.py](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/client.py)。源码值须在选定SDK版本及服务端上核实。试用/报价入口：[试用规则](https://www.joinquant.com/help/api/doc?name=logon&id=9830)、[购买方式](https://www.joinquant.com/help/api/doc?name=logon&id=9831)。[通用用户协议](https://cdn.joinquant.com/std/user/protocol/user.pdf)不足以单独确认JQData本地缓存和到期回放权。

### 8.4 免费源及其他商业源：不补造尚未公开的数值

| 来源 | 调用/返回方式 | 已知计数要点 | 尚未核齐 |
| --- | --- | --- | --- |
| BaoStock | Python `login`→query函数→遍历ResultData→DataFrame→`logout` | 查询可有上游分页；公司行动按代码和`operate`年度展开，登录/分页/重试另计 | 现行固定秒/分钟/日限、批量条数和风控规则；免费不等于不限频 |
| AKShare | 对应Python函数通常返回DataFrame；依各端点访问上游 | 一个函数可能发多页/多个HTTP；需分别记录函数调用和上游请求 | 没有可套所有源的统一配额/账号权益；实际端点历史、退市、单位、PIT和限频待核 |
| 掘金 | 官方旧版Python页说明`set_token`后可直接调数据函数，无需运行策略 | 该说明不能证明当前版本、全历史、导出和费用全部适用；非交易数据下载与账户成交导出分开 | 当前Python/macOS、必需字段、每日/单次/并发限制、扩容价及缓存权 |
| Wind | Client API与Server API分别核对，支持Python等调用 | 终端费用、接口权限和落地数据套餐不能合并计价 | 本项目具体接口/字段、认证、历史批量上限、个人价格及数据使用权 |
| iFinD | QuantAPI官网有SDK/数据服务入口 | 个人/机构及不同产品权限分别确认，不沿用“个人无法购买”推断 | 实际账号方式、字段、配额、Mac环境、价格、缓存/到期条款 |
| Choice | 数据服务/量化接口产品，按对应SDK/产品文档核对 | 客户端接口、数据库交付及终端不是同一产品 | 必需字段、历史版本、个人API费用、请求/流量限制和数据使用权 |

入口：[BaoStock](https://www.baostock.com/)、[AKShare股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)、[掘金Token调用说明](https://www.myquant.cn/docs/python/python_other_api)、[Wind Client API](https://www.wind.com.cn/mobile/ClientApi/en.html)、[iFinD QuantAPI](https://quantapi.51ifind.com/)、[Choice数据服务](https://choice.eastmoney.com/product/datacenter)。部分官方页本次无法完整访问，未知项保留，不用第三方承诺补齐。

### 8.5 行业／主题与成员接口

以下归集自系统方案2026-10-19的文档核查；没有新增账号实测。指数行情、成员关系和分类依据是不同产品，不能由一个接口存在推定其余内容合格。

| 产品／接口 | 调用方法与限额 | 历史边界 |
| --- | --- | --- |
| 申万行情 `sw_daily` | 按日期批量或代码＋区间；4000行／次，5000积分 | 默认申万2021版；指数历史不自动证明当时成员 |
| 行业成员 `index_member_all` | 行业层级／代码查询；2000行／次，2000积分 | 默认最新，历史必须取旧关系；纳入／剔除日不自动等于公开日 |
| 东财行情 `dc_daily` | 日期批量或代码＋区间；2000行／次，6000积分 | 2020年起，不能算完整十年主题行情 |
| 东财概览 `dc_index` | 日期化概览；5000行／次，6000积分 | 聚合涨跌家数不替代MA60宽度，领涨股不替代每板块五股筛选 |
| 东财成员 `dc_member` | 板块＋交易日／区间；5000行／次，6000积分 | 2024-12-20起，近期断更有补齐路径，更早历史仍缺 |
| 免费AKShare接口 | `index_hist_sw`、`stock_board_concept_hist_em`、当前目录／成员接口 | 一次函数可展开多次HTTP；申万历史接口无起止日期参数，可能重复返回全段；当前成员不可倒填历史 |

来源：[申万行情](https://tushare.pro/document/2?doc_id=327)、[行业成员](https://tushare.pro/document/2?doc_id=335)、[东财行情](https://tushare.pro/document/2?doc_id=382)、[概览](https://tushare.pro/document/2?doc_id=362)、[成员](https://tushare.pro/document/2?doc_id=363)、[AKShare指数](https://akshare.akfamily.xyz/data/index/index.html)、[AKShare股票](https://akshare.akfamily.xyz/data/stock/stock.html)。

### 8.6 日线／分钟的量额字段及历史覆盖

以下归集自系统方案2026-10-20的官方字段核查。**日线和所列分钟K线均含对应区间成交量、成交额字段**，无需为这两个字段购买逐笔成交；不代表全部历史对象无缺值。

| 来源／接口 | 粒度 | 成交量／成交额字段与来源单位 | 入库处理 |
| --- | --- | --- | --- |
| Tushare `daily` | 日线 | `vol`为手，`amount`为千元 | A股量×100转股、额×1000转元；保留原值和单位 |
| Tushare `stk_mins` | 1／5／15／30／60分钟 | `vol`为股，`amount`为元 | 已是标准量额单位，**不能复用日线的×100／×1000转换** |
| RQData `get_price` | 日线／分钟 | `volume`与`total_turnover`为K线量额；当前通用字段表未在该处列单位 | 按适用A股字段合同和样例确认单位；原始量价显式用`adjust_type='none'`，复权选项可能同时调整成交量 |

来源：[日线字段](https://tushare.pro/document/2?doc_id=27)、[分钟字段及限额](https://tushare.pro/document/2?doc_id=370)、[RQData行情与复权参数](https://www.ricequant.com/doc/rqdata/python/generic-api)。

Tushare `stk_mins`官方1分钟示例为09:30至15:00共241条，不能将240条容量假设作为删除竞价bar的依据。RQData `get_price`默认复权方式为前复权；原始行情须显式指定`adjust_type='none'`，复权选项可能同时调整成交量。RQData另有盘后行情接口，日线与分钟量额能否直接对拍须核同一交易时段和产品口径。[Tushare分钟示例](https://tushare.pro/document/2?doc_id=370)、[RQData行情接口](https://www.ricequant.com/doc/rqdata/python/generic-api)

**历史覆盖证据**：Tushare公开价目表标A股分钟自2009年起，当前二十年约从2006年开始，因此该源的2006—2008段没有已证明的供给路径。RQData官方方案简介（历史材料）列A股分钟自2005年起，可作为二十年询价／试用候选；该简介不证明当前账号权限、北交所或全部退市对象完整，仍需核验并取得适用报价。不能用分钟接口“支持某频度”推定该频度的所有年份均可获得。[Tushare历史起点](https://tushare.pro/document/2?doc_id=290)、[RQData方案简介第1页](https://assets.ricequant.com/welcome/%E7%B1%B3%E7%AD%90RQData%E9%87%91%E8%9E%8D%E6%95%B0%E6%8D%AE%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E7%AE%80%E4%BB%8B.a528f1bd.pdf)

分钟单位、时段及历史覆盖的账号验证仍未执行；条数、磁盘容量及不同年限请求估算集中在[系统方案§5.4](A股量化系统数据与数据源方案.md#54-全a股分钟k线131020年规模)和[§7.4](A股量化系统数据与数据源方案.md#74-分钟历史的请求量与可获取边界)。

## 9. 用量计量与项目估算入口

来源计量单位以§8为准：分别登记逻辑查询、SDK函数、展开后的上游产品请求、网络尝试、返回行数及配额字节；认证、分页、重试另计。单次上限只有与接口支持的完整拆分方式结合，才能换算实际请求量，不能从理论频次承诺完成天数。

日更、历史回溯、15日断更恢复及分钟请求量集中在[系统方案§7](A股量化系统数据与数据源方案.md#7-使用规模与预算分别计算记录请求扣额)。原5474对象、84信号日和行动待审数保留在其[§7.5历史案例](A股量化系统数据与数据源方案.md#75-历史研究调用估算2026-10-19案例)，不再与供应商事实共同维护，也不代表当前库存或执行授权。

## 10. 还缺哪些验证和补充

### 10.1 信息齐备程度

| 信息项 | 目前齐备情况 | 完成所需证据 |
| --- | --- | --- |
| 候选接口/调用方法 | Tushare、RQData、JQData主要路径已有文档/源码；免费源有项目路径 | 在选定SDK版本做最小接口/返回schema核对；其他商业源需具体产品文档 |
| 调用量 | 本文维护计量单位；项目假设及估算统一见系统方案§7 | 先扣已有覆盖，确认端点完整拆分和实际扣额，再生成请求/数据量清单 |
| 调用限制 | Tushare多项数值明确；RQData有流量/大请求说明；JQData有源码行数 | 账号实际限额、并发/IP/重试计费、服务端与SDK差异；免费源风控边界 |
| 价格 | Tushare200／500／1000元个人档、独立分钟2000元及部分其他独立权限明确；其他适用金额未知 | 官方适用报价及模块、周期、导出、续费/试用条件；不先采购 |
| 历史语义 | 米筐PIT市值有明确文字；其他重要字段版本范围未全证 | 原沪深研究的历史身份／公开版本／行动难例；不扩大原验收范围 |
| 二十年全A股日线 | 历史查询及沪深北目录有文档，逐产品实际覆盖未证 | 目标起止日、北交所实际上市期间、早期退市及跨端点完整性 |
| 分钟四档及量额 | 频度、字段与Tushare限额已核；二十年来源仅有候选 | 实际账号覆盖、最早可交付日期、量额单位／时段、独立报价及批量下载样例 |
| 本地与到期权利 | API可本地调用不等于数据许可已确认 | SDK/HTTP缓存、离线RQAlpha、订阅到期保留、多环境和账号设备条款 |

因此，**信息源已完成本轮归集与范围标注，尚未达到采购资料全部齐备**。缺少报价或账号实测时保留未知，不把文档示例写成实际已通过。

### 10.2 验证方式及边界

- **可继续做的公开资料补充：**具体产品字段表、版本说明、报价与条款页面；页面不可访问就明确记录。
- **需要官方答复的事项：**给定字段/历史范围的个人组合价、精确权限、扣额规则、到期保留及独立引擎用途。第6节是问题模板，本次没有对外发送。
- **需要账号或样例的事项：**先用第6.2节已有难例验证历史值/修订和特别分红，再核返回总数/分页、原始响应及查询前后额度差；不是启动全池或真实研究。
- **独立的软件事项：**事实核验接缝、数据字段映射及未支持换股处理，不能记作采购已解决；已通过机制和旧结果不重复验收。

本轮不注册/申请试用、不登录或调用数据、不联系销售、不改变免费边界；后续若需要这些动作，应明确具体样例、请求量和费用。当前不排序采购方案、不承诺能以某一套餐完成二期。

### 10.3 必要供应商事实复核（2026-10-21）

本节仅记录官方网页／源码核查及其边界，不是市场数据API请求、账号试用或研究验收。旧核查时点及既有有效结果保持原适用范围；下表不触发重取数据或重跑研究。

| 范围／原文链接 | 当前确认 | 仍需账号、样例或适用报价 |
| --- | --- | --- |
| Tushare[积分价目](https://tushare.pro/document/2?doc_id=290) | 个人200／500／1000元每年分别对应2000／5000／10000以上积分档；2000档200次／分、每API每日10万次，后两档常规500次／分且常规日总量不限；特色另有限制 | 端点独立限制仍适用；3000／6000门槛最低开通价、账号权限和缓存／到期使用权未知 |
| Tushare[日线](https://tushare.pro/document/2?doc_id=27)、[因子](https://tushare.pro/document/2?doc_id=28)、[每日指标](https://tushare.pro/document/2?doc_id=32) | `daily`是未复权日线，6000行／次；因子与`daily_basic`均2000积分起，后者6000行／次，含股本与市值；免费档只有未复权日线 | 端点和通用日线频次差异见§8.1；`trade_date`及三股本字段未证明公告版本PIT，完整历史覆盖仍须验证 |
| Tushare[基础身份](https://tushare.pro/document/2?doc_id=25)、[历史列表](https://tushare.pro/document/2?doc_id=262)、[分红](https://tushare.pro/document/2?doc_id=103)、[ST](https://tushare.pro/document/2?doc_id=397) | 身份默认L，D须另取；`bak_basic`2016年起、5000积分、7000行／次；`dividend`2000积分、2000行／次，含进度和实施／登记／除息／派息／红股上市日期；ST需3000积分 | 历史池完整性、代码变更与身份公开版本、特别分红和行动修订仍未取得账号证据；分红接口不能证明配股／换股／退市处置齐备 |
| Tushare[申万成员](https://tushare.pro/document/2?doc_id=335)、[申万日线](https://tushare.pro/document/2?doc_id=327)、[东财成员](https://tushare.pro/document/2?doc_id=363) | 申万成员2000积分、2000行／次，默认最新，列纳入／剔除日；申万行情5000积分、4000行／次；东财成员6000积分、5000行／次，2024-12-20起 | 成员日期不自动证明当时公开可得；更早历史、退市关系和版本覆盖未证。500元档不足6000门槛，1000元档达门槛但不代表最低价格 |
| RQData[股本、市值与行动字段](https://www.ricequant.com/doc/rqdata/python/stock-mod) | `market_cap_3`有公告后调整股本的PIT说明；`get_shares`列总、流通A股及自由流通股本，后者2005年起；`get_dividend`含现金到账日，`get_split`的`payable_date`指送转股上市日 | 不把`get_shares`日期序列自动视为完整PIT；全窗覆盖、修订留存、特别分红及其他行动须核。现金到账与送转股上市不可合并 |
| RQData[历史身份与详情](https://www.ricequant.com/doc/rqdata/python/generic-api) | `all_instruments(type='CS', date=...)`支持指定日期筛选；`instruments`明示返回最新详情，含上市／退市日期 | 历史筛选与最新详情不能合成已经成立的历史公开版本链；退市对象及跨端点映射须按目标池核验 |
| RQData[配额手册](https://www.ricequant.com/doc/rqdata/python/manual)、[商城](https://www.ricequant.com/welcome/store/) | SDK按压缩后的实际传输流量计量；`get_quota`返回字节上限／用量和许可期限，手册试用值为每日1G；商城本次未返回可读报价 | 适用字段组合价格、账号真实配额、权限／期限、批量导出及离线／到期许可仍未知，文档试用值不等于已取得试用资格 |
| JQData[官方SDK](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/api.py)、[购买入口](https://www.joinquant.com/help/api/doc?name=logon&id=9831)、[旧FAQ](https://www.joinquant.com/community/post/detailMobile?postId=21247) | 源码确认历史日期股票池、`get_fundamentals(date=...)`及`get_valuation`区间估值；市值亿元、股本万股；`get_query_count`按条数。购买页及旧FAQ本次均受地区限制 | 日期参数本身未证明股本按公开时点变化或全部修订保留；旧FAQ不升级为现行全产品结论。当前报价、账号额度、原池／行动覆盖及许可仍待确认 |

上述证据可用于界定日线、历史池、PIT市值和行动各自的有界用途验证；接口存在、公开价或PIT文字均不能替代保存响应、目标对象／期间覆盖与关键难例证据。本次没有取得新的【项目实测】结论。

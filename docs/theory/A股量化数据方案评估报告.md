# 个人 A 股量化研究数据方案评估报告

> 文档版本：v0.7；性质：可复用的数据源研究与事实索引
> 资料复核日期：2026-10-23。价格、配额和权限可能变化，适用账号与正式合同优先。
> 适用范围：A 股历史研究、行情建库及相关补充渠道；区分研究数据服务、交易接口和工具库。
> 文档分工：本文维护供应商能力、接口、价格、限制和来源；组合方法、容量与本地数据架构见[系统数据与数据源方案](A股量化系统数据与数据源方案.md)。
> 证据标记：**官方说明**指供应商文档、源码或服务券商说明；**社区线索**指作者经验和反馈；**待验证**指尚无充分产品、账号或样例证据。文档支持不等于实际覆盖已验证。

## 0. 结论摘要

- 先由市场、策略频率和历史语义确定数据，再比较质量、成本与接口。日线、分钟、实时行情、Level-2 和交易接入是不同产品，不能由一个渠道的优势推定全部适用。
- **Tushare 是个人 A 股研究主库的重要候选**：价格档位公开，日线、常用指标、参考数据和行业研究接口可按需组合；BaoStock、AKShare 用于免费起步、补缺与校验。具体档位差异见§8.1，不能只比较调用频次。
- 在能力满足需求的前提下，优先可用免费源与公开低价产品；RQData、JQData 等未取得适用公开价格的产品降为缺口备选，只有低成本路线不能满足关键数据语义时再询价。未公开报价不等于已证昂贵，也不应采用未经核实的低价传闻。付费不能自动证明 PIT、特别分红与退市覆盖。
- QMT、PTrade、CTP 主要属于行情和交易接入体系。开户门槛、券商权限、终端依赖与历史导出能力需要另核，不能把“软件免费”理解为“历史数据及 Level-2 全部免费”。
- 可核公开价格与社区报价分开维护。Tushare 官方个人积分档列 200／500／1000／1500 元每年，A 股历史分钟另为 2000 元每年；不存在由这张表支持的“2000 元全量权限”结论。其他组合报价未核实的标为待报价。[官方价目](https://tushare.pro/document/2?doc_id=290)
- 试用可以验证数据质量；能否用试用批量建库、到期保留和长期使用，须核实试用条款。跨源接续还需要统一标的、单位、复权和版本口径。

## 1. 研究所需数据与候选接口

下表的 ✅ 表示官方文档存在相应接口或字段，🟡 表示语义、版本或覆盖仍待核实；不代表某个套餐具备全部权限。没有找到说明也不等于供应商明确不支持。

| 需求域 | TuShare Pro(2000 分) | TuShare Pro(5000 分) | BaoStock | AKShare | JQData(聚宽) | RQData(米筐) | Wind/iFinD/Choice |
|---|---|---|---|---|---|---|---|
| A1 历史每日总市值 | ✅ `daily_basic.total_mv`（万元） | ✅ 同左；仍有单次/频次限制 | 🟡 未核得逐日市值接口 | ✅ `stock_value_em`/`stock_zh_valuation_baidu`；覆盖待核 | ✅ `valuation`/`get_valuation` | ✅ `get_factor` | 🟡 需具体产品/字段证明 |
| A2 历史股本（总/流通/自由流通） | ✅ `daily_basic`三口径 | ✅ | 🟡 未核得逐日三口径接口；`query_stock_industry`是行业接口 | ✅ 历史/候选接口存在，版本待核 | 🟡 逐字段口径核对 | ✅ `get_shares`列`total/circulation_a/free_circulation`；PIT及修订另核 | 🟡 需字段表 |
| A3 市值 PIT 口径说明 | 🟡 交易日期不能证明公告版本 | 🟡 | — | 🟡 未证完整PIT | 🟡 日期查询及修订语义待核 | ✅ `market_cap_3`有公告后调整说明；修订溯源仍待核 | 🟡 不作行业整体推断 |
| B1 退市股列表与身份 | ✅ `stock_basic(list_status='D')` | ✅ | 🟡 跨端点历史覆盖待核 | 🟡 某股本接口成功空不等于没有退市名录，须分别核对 | ✅ 历史日期筛选，实际覆盖待核 | ✅ 历史日期筛选，实际覆盖待核 | 🟡 需具体接口/覆盖 |
| B2 按历史日期取当时股票池 | 🟡 `list_date/delist_date`及公开版本须区分 | ✅ `bak_basic`有日期化列表，身份链另核 | 🟡 需版本/覆盖核对 | 🟡 需版本/覆盖核对 | ✅ `get_all_securities(date=...)` | ✅ `all_instruments(date=...)` | 🟡 需具体接口/覆盖 |
| C1 分红送转（含进度字段） | ✅ `dividend`含进度与多日期；滞后为社区反馈待复核 | ✅ | ✅ SDK含公告/登记/除权/派息/红股上市日期；内容覆盖另核 | ✅ 东财/巨潮/新浪候选 | ✅ `finance.STK_XR_XD`，版本待核 | ✅ `get_dividend`/`get_split`，覆盖待核 | 🟡 需具体字段/权限 |
| C2 配股 | 🟡 `dividend`不能当全部行动接口；其他产品待核 | 🟡 | 🟡 | ✅ 新浪配股接口；覆盖待核 | 🟡 需具体表 | 🟡 需具体接口 | 🟡 需具体接口 |
| C3 换股/吸收合并/退市补偿 | 🟡 待核 | 🟡 | 🟡 未证完整产品 | 🟡 可找原件，结构化范围待核 | 🟡 | 🟡 | 🟡 不凭品牌判完整 |
| C4 特别分红覆盖 | 🟡 按实际事件核对 | 🟡 | 🟡 完整性须以事件样本核对 | 🟡 | 🟡 | 🟡 | 🟡 |
| D1 未复权日线 OHLCV | ✅ `daily`（vol=手，amount=千元，pre_close为除权昨收） | ✅ | ✅ SDK接口存在；完整覆盖另核 | ✅ 多源接口存在 | ✅ | ✅ | 🟡 按具体数据产品核对 |
| D2 复权因子 | ✅ `adj_factor`，限制见第8节 | ✅ 仍须核接口限制 | ✅ 免费；未核得无限频保证；涨跌幅复权 | ✅ 对应因子/复权价口径需核 | ✅ factor | ✅ | 🟡 需具体产品 |
| D3 停复牌历史 | ✅ `suspend_d` | ✅ | ✅ 日线`tradestatus` | 🟡 需核对具体历史状态端点，不用缺行情猜停牌 | ✅ `get_price`含paused | ✅ | 🟡 需具体接口/覆盖 |
| D4 ST/风险警示历史 | 🟡 `stock_st`3000积分起，2000-01-01起历史 | ✅ `stock_st`，实际完整性待核 | ✅ 日线`isST`，不靠名称猜测；覆盖待核 | 🟡 | ✅ | ✅ | 🟡 需具体产品 |
| D5 历史涨跌停价 | ✅ `stk_limit`，盘前产品另核权限 | ✅ | 🟡 须结合历史规则/板块/ST/除权参考价，不能只套固定比例 | 🟡 | ✅ `get_price`含high/low_limit | ✅ | 🟡 需具体接口/覆盖 |
| D6 交易日历 | ✅ `trade_cal` | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 需具体数据产品 |
| E 财务 PIT（财务策略必需） | 🟡 有ann_date，全部修订保留待核 | 🟡 | 🟡 完整PIT未证 | 🟡 具体源另核 | 🟡 date查询不等于全部修订保留 | ✅ `get_pit_financials_ex(date, statements='all')`文档；实际覆盖待核 | 🟡 需具体产品，不推断最强 |

## 2. 渠道分类与适用边界

### 2.1 A 股研究与建库渠道

| 渠道 | 适合的用途 | 获取方式与关键边界 |
| --- | --- | --- |
| BaoStock | 免费历史日线、复权因子、证券资料和部分状态/行动 | Python 查询接口，以历史数据为主；逐日 PIT 市值和全部公司行动未获完整产品证明；免费不等于不限频。[官方入口](https://www.baostock.com/) |
| AKShare | 行情、估值、宏观、公告、龙虎榜、机构调研等补充与校验 | 按端点连接不同上游；历史长度、字段、单位、权限和稳定性应逐源判断，不能给全部接口统一 SLA 或 PIT 承诺。[股票文档](https://akshare.akfamily.xyz/data/stock/stock.html) |
| Tushare Pro | 公开价格、按需开通的 A 股研究主库重要候选：日线、参考数据、财务和板块 | Python/HTTP；各积分档新增能力与分钟等独立权限分别核对。日期化估值不是完整公告版本证明，`dividend`也不等于全部公司行动。详见§8.1。 |
| RQData | 行情、股本、市值、财务及历史身份的综合数据服务 | Python/HTTP；`market_cap_3`有公告后调整股本的 PIT 说明，历史修订审计与各端点覆盖仍须样例验证。详见§3、§8.2。 |
| JQData | 本地获取行情、估值、财务和历史证券范围 | 本地 SDK；与聚宽云平台/VIP分开核权限，`date`查询不能单独证明所有旧版本保留。详见§8.3。 |
| 优矿/通联数据 | 综合研究数据、PIT 数据和研究平台候选 | 官方产品介绍有研究数据与本地 SDK；免费注册不等于全部数据可免费导出，具体产品权限、配额和费用待核。[优矿](https://uqer.datayes.com/)、[通联数据](https://www.datayes.com/) |
| 掘金量化 | 行情、策略研究与后续交易对接 | 社区笔记及官方社区搜索摘要提示有基础免费版，但未取得可回查的套餐详情；当前费用、历史扩容和交易权限待产品页或账号确认。[官方社区入口](https://bbs.myquant.cn/)、[数据调用说明](https://www.myquant.cn/docs/python/python_other_api) |
| iFinD | 财务、行情、宏观和综合数据候选 | 官方数据接口提供 SDK、HTTP 与命令生成工具；个人套餐、字段、试用量、价格和缓存权须逐项确认。[官方部署说明](https://ftwc.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/deploy.html) |
| Choice | 基本面、财务、历史行情、宏观及板块数据候选 | 数据量化接口有截面、序列、专题、板块及流量查询；终端与 API 权限/费用分别核实。[官方手册](https://quantapi.eastmoney.com/Manual) |
| Wind | 多资产、财务、资讯和专业数据候选 | Client API、Server API、终端与数据交付是不同产品；不得用终端报价代替 API 和落地数据报价。[Client API](https://www.wind.com.cn/mobile/ClientApi/en.html)、[Server API](https://www.wind.com.cn/mobile/WDS/sapi/zh.html) |

### 2.2 行情与交易接口

| 渠道 | 可核能力 | 不能据此推出的结论 |
| --- | --- | --- |
| QMT / MiniQMT / XtQuant | 官方说明 XtData 提供历史/实时 K 线、分笔、财务、合约及板块数据；MiniQMT 模式须先启动客户端，XtTrader另负责交易。[迅投文档](https://dict.thinktrader.net/nativeApi/start_now.html) | 券商开通条件、Windows等部署要求、Level-2、完整历史长度、批量落地与到期权利须按具体版本核实；不能当作无需终端依赖的通用云 API |
| PTrade | 券商官方介绍涵盖策略研究、回测、交易，并要求办理协议及开通权限。[券商产品说明](https://www.cctgsc.com.cn/ptrade)、[开通说明](https://www.i618.com.cn/main/companybusi/wealth/quantitativetrading/ptrade/index.shtml) | 社区“部分券商免费 Level-2”未形成通用产品证明；须区别十档盘口、逐笔成交/委托、历史回放与导出权限 |
| CTP | 期货行情/交易接口；行情 API 以登录、订阅和推送为主。[上期技术接口手册](https://www.simnow.com.cn/DocumentDown/api_3/5_2_4/SFIT%2BCTP%2BMini%2BAPI-V1.7.0.pdf) | 实时行情接入不等于多年历史数据库，交易接口不能替代历史数据采购 |

### 2.3 期货、海外与辅助工具

| 渠道/工具 | 定位 | 使用边界 |
| --- | --- | --- |
| TqSdk | 期货历史/实时行情、回测、模拟及实盘开发 | 开源库依托快期服务；Tick/K线范围取决于合约历史与账户权限，开源许可不等于全部服务免费。[官方介绍](https://doc.shinnytech.com/tqsdk/latest/intro.html) |
| yfinance / Yahoo Finance | 海外股票、ETF及跨资产探索 | yfinance是非Yahoo官方的开源客户端；作者说明用途为研究教育并提醒数据个人使用条款。不能据此推定完整A股历史/PIT覆盖。[项目说明](https://github.com/ranaroussi/yfinance) |
| Alpha Vantage | 海外股票及相关数据 API | 官方普通免费额度为每日25请求；部分功能付费，实时/延迟美股另受权限限制，不能直接承接全市场批量建库。[官方额度说明](https://www.alphavantage.co/support/) |
| Databento | 海外交易所行情、盘口和高频数据候选 | 历史按量与订阅产品分开；历史按未压缩二进制字节计量，交易所许可另核。免费抵扣金是促销权益。[计费说明](https://databento.com/docs/faqs/usage-pricing-and-data-credits) |
| Bloomberg / LSEG（Refinitiv） | 专业多资产数据、终端与企业服务 | 终端、桌面 API、服务器 API 和数据授权须分别报价，不能把终端订阅费当成全部接口与数据使用费。[Bloomberg API](https://professional.bloomberg.com/support/api-library/)、[LSEG产品资料](https://www.lseg.com.cn/content/dam/lseg/en_us/documents/investor-relations/introduction-to-lseg.pdf) |
| FinanceDatabase | 证券符号、资产类别和分类元数据 | 可辅助代码发现与映射，不替代行情、公司行动或历史PIT证券主表。[项目仓库](https://github.com/JerBouma/FinanceDatabase) |
| QuantDash、free-stockdb | 社区笔记提到的小规模快照/本地历史试验线索 | 摘要未提供可唯一定位的项目地址、版本和授权；暂不认定具体项目及其数据能力，也不纳入稳定主源 |

## 3. 历史时点与数据口径

### 3.1 市值、股本与财务 PIT

| 口径 | 含义 | 判断方法 |
| --- | --- | --- |
| 当时已公开版本 | 在信号时点使用已公开的股本/报表 | 分开记录业务生效、公告公开与获取时间，核查修订链 |
| 事后重建的实际值 | 以最终确认的股本回填历史 | 可用于事后统计，不能未经说明代替当时可得信息 |
| 后来修订覆盖值 | 当前查询返回修订后的历史 | 有历史日期不表示保留了历史版本，须问原版是否可查 |

RQData `market_cap_3`明确采用公告后调整股本的处理方式；普通`market_cap`不能与其混用。该说明只证明这一字段的处理语义，不自动证明全部历史修订可审计、全部端点具备同样PIT能力。[字段说明](https://www.ricequant.com/doc/rqdata/python/stock-mod)

Tushare `daily_basic`、JQData `get_fundamentals(date=...)`等提供历史日期截面；仅凭日期参数不能判定全部公开版本已保留。财务数据应核对公告时点，报告期末不等于投资者已知日期。跨源建库时尤其不能把事后修订值与原始公开版本无标记地拼接。

### 3.2 公司行动、复权与总收益

分红方案、正式实施、登记、除权除息、现金到账和送转股上市是不同事实。预案不能按实施处理；行情跳空只能用于对拍，不能独立证明事件或金额。特别分红、配股、换股、吸收合并和退市补偿应分别核对，常规`dividend`接口不代表全部支持。

例如，RQData `get_dividend`的现金到账日与`get_split`中表示送转股上市日的`payable_date`不能合并为同一种事件时间；字段英文名称相似不代表业务含义一致。[公司行动字段](https://www.ricequant.com/doc/rqdata/python/stock-mod)

复权价收益与持股现金/再投资收益也有区别。按BaoStock官方涨跌幅复权公式，若前收100、派息10、除权参考价90、当日收99，则调整后单日涨幅为10%，持股加现金收益为9%。因此不能概括为“复权未计现金分红”或“总是低估收益”，也不能在已经包含分红影响的收益上重复加现金。[官方算法](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)

### 3.3 历史证券与板块成员

历史股票池应包含当时存在而今已退市的对象，并区分上市/退出业务日期、公开时点、代码变更与证券实体。Tushare默认`stock_basic`仅列在市证券，退市须另取；`bak_basic`始于2016年，不能证明更早全池。RQData历史筛选`all_instruments(date=...)`与返回最新详情的`instruments`应分开使用。[Tushare证券资料](https://tushare.pro/document/2?doc_id=25)、[历史列表](https://tushare.pro/document/2?doc_id=262)、[RQData通用接口](https://www.ricequant.com/doc/rqdata/python/generic-api)

当前行业/主题成员不能倒填历史。指数行情、分类依据、成员关系、成员生效日和成员公开日应分别核对；跨供应商同名板块也不一定具有相同定义。

## 4. 价格与权限的比较方法

价格至少拆为基础订阅、独立数据模块、历史扩容、调用/流量超额、交易所行情许可及终端/账户条件。具体公开数值与调用限制集中维护于§8；无可核正式报价的产品保留“待报价”，不使用社区数字补空。

三类“免费”需要分清：长期免费产品、期限/额度有限的试用、券商按账户条件提供的权益。平台会员、交易软件和本地数据API也可能独立计费。组合选择方法见[系统数据与数据源方案](A股量化系统数据与数据源方案.md)。

## 5. 质量与使用权核验

| 维度 | 需要的直接证据 |
| --- | --- |
| 历史覆盖 | 分市场、证券类型、粒度的可查询起止及缺失清单；覆盖早期退市、不同上市时期对象 |
| PIT与修订 | 原版/修订版及公开时间的样例；字段是否回填、接口如何查询旧版本 |
| 行动完整性 | 常规与特别分红、送转、配股和退出事件样例；进度与不同日期含义 |
| 量价一致性 | 原价与复权参数、成交量/成交额单位、交易时段、停牌处理和因子方向 |
| 调用限制 | 账号实际权限、单次返回上限、分页、并发、失败/重试扣额与可恢复方式 |
| 使用权 | 本地缓存、离线计算、批量建库、备份、订阅到期保留和允许的使用范围 |
| 跨源接续 | 重叠窗口的身份、价格、量额、复权、事件和修订差异；不能只对齐列名 |

付费服务可以降低维护成本；字段存在、调用成功与平台回测能运行，都不能替代上述用途验证。少量样例只能验证样例涉及的语义，完整历史覆盖仍需清单核对。

## 6. 供应商询价与样例问题模板

1. 列明所需市场、证券类型、粒度和历史长度后，哪些具体产品/字段覆盖？退市后历史是否继续可查，有无逐市场覆盖清单？
2. 股本、市值与财务采用生效日还是公开日？修订是否回填？旧版本、公告时间和首次可得时间能否导出？
3. 特别分红、送转、配股、换股与退市补偿分别在哪个接口？登记、除息、现金到账和股份可卖日期如何区分？
4. 个人/机构、基础/独立模块的金额与计费周期是什么？试用、批量历史、日常增量、超额及交易所费用如何计价？
5. 每请求行数/字节、频率/并发、分页和重试规则是什么？SDK与HTTP是否共用额度，怎样查实际扣额？
6. 能否导出到本地长期研究？试用结束或订阅到期后能否保留、回放、备份？操作系统、终端与证券账户有哪些依赖？
7. 能否提供覆盖常规对象、特别分红、回购注销/转股、退市换股、较早历史和不同市场的样例？分钟样例应包含时段边界、量额和复权定义。

## 7. 社区材料的吸收与取舍

### 7.1 两篇笔记提供的选型线索

用户提供的提取摘要来自以下两篇笔记；本节记录作者观点，不将未核验价格、免费额度或产品能力升级为官方事实：

- Blade runner：[《A股量化数据源怎么选？》](https://www.xiaohongshu.com/explore/6a55f0c6000000000803368b)。强调按用途组合研究库、行情/交易和补充渠道；提出BaoStock＋AKShare＋yfinance、Tushare＋QMT，以及试用iFinD/JQData建底库后由Tushare更新的组合。
- OpenAlphas：[《个人量化交易数据源：渠道、优劣对比与选型》](https://www.xiaohongshu.com/explore/69b79165000000002102fd8a)。按免费入门、垂直渠道、商用和专业数据分层，强调先按策略周期确定数据，再比较覆盖、质量、成本与接口。

采用“按用途分层、先验证质量再选渠道”的方法。具体组合增加必要边界：纯A股研究不必引入yfinance；没有实时或交易需求不必先配QMT/PTrade；试用建库必须有允许保留和后续使用的条款；跨源更新需先验证口径一致。财务回测的公告时间、复权/分红及缺失检查已纳入§3和§5。

### 7.2 笔记报价与可核事实分开

下表金额仅用于追踪作者给出的线索，不是预算依据、现价或已确认套餐。

| 笔记中的说法 | 本文处理 |
| --- | --- |
| Tushare Pro基础500元起、全量约2000元/年 | 官方存在200元年积分档，历史分钟2000元另开；“全量2000元”不成立于官方权限结构，按§8.1拆分 |
| Choice个人3000–6000元/年 | 适用终端/API及模块未证，待正式报价 |
| RQData个人基础约2000、全量约5000元/年 | 所称套餐及权限未证，待正式报价，不以“全量”推定PIT和全历史 |
| iFinD个人3500–6500元/年 | 适用数据接口套餐未证，待正式报价 |
| 掘金付费1000–3000元/年 | 当前套餐与扩容价未获可回查的官方详情证明，免费与付费权益均待核，不能用该区间概括所有权益 |
| Wind个人2万元/年起；聚宽/米筐机构1万–3万元/年 | 适用产品、接口与合同未证，待正式报价 |
| Bloomberg/Refinitiv 20万元/年以上 | 不作为当前报价，终端与企业数据/API分别核实 |
| Databento按量计费 | 官方存在历史按量机制，但也有订阅产品及数据集授权，不能概括全部服务只按量收费 |
| PTrade部分券商免费Level-2 | 保留为券商核验线索，需明确券商、数据类型、使用与导出条件 |

已有[Tushare分红更新反馈](https://github.com/waditu/tushare/issues/1910)和[证券代码映射争议](https://github.com/waditu/tushare/issues/1683)可用于设计质量样例；未经复核不能当作供应商当前普遍缺陷，也不应自行改写证券身份。

## 8. 调用方式、返回单位及限制

以下为**文档级核对**，示例未执行。只示意数据调用，不代表字段已取得使用权限或满足指定用途。SDK一次调用、上游数据请求、网络尝试、返回记录和计费/扣额单位分别登记。

### 8.1 Tushare：按调用次数与端点行数分别限制

Python使用Token认证，返回DataFrame；同一账号的SDK和HTTP不能当两份配额。官方HTTP教程使用`http://api.tushare.pro`，POST JSON含`api_name/token/params/fields`，返回`code/msg/data.fields/data.items`。HTTPS地址与账号实际支持情况仍应核实。[Python教程](https://tushare.pro/document/1?doc_id=131)、[HTTP教程](https://tushare.pro/document/1?doc_id=130)

```python
# 方法示例；query_date 使用 YYYYMMDD 格式。
import os
import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
caps = pro.daily_basic(trade_date=query_date,
                       fields="ts_code,trade_date,total_share,total_mv,circ_mv")
```

**主要个人价格档与能力差异：**下表按官方列示的年价比较，不是逐档费用相加；升级补差、赠分及有效期按账号确认。积分达到某端点门槛不等于该端点历史覆盖和 PIT 已验证。

| 个人年价／积分档 | 较低档新增的代表能力 | 通用分钟频次 | 通用每日调用上限 | 适合用途与主要边界 |
| --- | --- | --- | --- | --- |
| 免费／120 | `daily` 非复权日线 OHLC、量额 | 50次/分 | 8000次 | 原始量价试用；通用表不含证券资料、日历、复权因子和财务接口 |
| 200元／2000以上 | 日历、证券资料、每日市值／股本／估值、复权因子、分红、停牌、涨跌停价；普通财务指标 `fina_indicator` | 200次/分 | 每API 100000次 | 基础日线研究库；不含3000门槛的 `stock_st`、5000门槛的历史列表和申万日线 |
| **500元／5000以上** | `stock_st` 历史风险警示、`bak_basic` 每日历史列表、`sw_daily` 申万日线；`fina_indicator_vip` 按季度取全市场财务指标；筹码接口达到正式门槛 | 500次/分 | 常规数据无每日总量上限；筹码另限 | **综合日线／行业研究优先核验档**；仍不含6000门槛的东财板块接口；历史列表仅2016年起，财务批量不等于完整PIT |
| 1000元／10000以上 | 达到6000门槛的东财分类／成员／行情及券商月度金股；盈利预测正式权限与更高筹码日额度 | 常规500次/分；特色通用表300次/分，端点另核 | 常规无每日总量上限；特色按端点核对 | 明确需要更广板块、盈利预测或筹码用量时考虑；不代表6000／8000门槛的最低开通价格 |
| 1500元／15000以上 | 通用表列更高特色权限；已核实的具体增量是筹码日总量进一步放宽 | 500次/分，端点更严者优先 | 通用表列特色无总量限制 | 适合确有较大特色数据用量的情况；未核得本表代表接口中仅此档独占的新数据类别，普通日线不必直接选此档 |

“次调用”不是“数据行”。单次行数另看下表；独立权限另计。通用流控的跨API共享方式、账号实际获批条件待核；端点有独立更严限制时，不能直接套通用值。[官方权限/价目](https://tushare.pro/document/2?doc_id=290)

能力差异按端点核对：[`stock_st`为3000积分](https://tushare.pro/document/2?doc_id=397)，[`bak_basic`正式权限](https://tushare.pro/document/2?doc_id=262)和[`sw_daily`](https://tushare.pro/document/2?doc_id=327)为5000；普通[`fina_indicator`为2000，季度全市场版本 `fina_indicator_vip` 为5000](https://tushare.pro/document/2?doc_id=79)。东财三个板块接口的6000门槛、历史长度与行限见§8.5。因此500元与200元档的主要差别还包括数据范围和批量取得能力。

**特色数据不能只看通用档位名称。**官方端点给出的差异如下；不能把“10000档举例包含”误写成“10000才开放”。

| 代表接口 | 已核积分门槛及日调用量 | 单次行限／独立流控 | 对价格档选择的含义 |
| --- | --- | --- | --- |
| [`report_rc` 券商盈利预测](https://tushare.pro/document/2?doc_id=292) | 2000可试用每日10次；8000正式每日10万次；10000以上无日总量限制 | 3000行 | 200元档仅有小额试用，1000元档满足所列正式门槛；不是完整财务PIT的替代品 |
| [`cyq_perf` 筹码及胜率](https://tushare.pro/document/2?doc_id=293)、[`cyq_chips` 筹码分布](https://tushare.pro/document/2?doc_id=294) | 5000每日2万次；10000每日20万次；15000无日总量限制 | 各6000行；`cyq_chips`文档另列5000积分200次/分，不能套通用500次/分 | 500／1000／1500元档均可访问，差别主要在额度；筹码由算法估算，不是逐户持仓事实 |
| [`broker_recommend` 券商月度金股](https://tushare.pro/document/2?doc_id=267) | 6000 | 1000行 | 1000元档达到门槛，但不能据此认定必须花1000元；6000积分实际取得成本另核 |

`daily`端点另写基础积分500次／分，与通用表免费档50次／分存在表述差异；实际账号适用频次仍待确认，不能据此将免费档提高到500次／分。[日线说明](https://tushare.pro/document/2?doc_id=27)

| 接口 | 最低积分 | 单次最大行数（公开页） | 接口独立说明/历史范围 |
| --- | --- | --- | --- |
| [`daily`](https://tushare.pro/document/2?doc_id=27) | 按账号权限，免费档仅此未复权日线 | 6000 | 可按交易日取全市场或按代码／区间查询；历史及各市场实际覆盖须核验，量额单位见§8.6 |
| [`trade_cal`](https://tushare.pro/document/2?doc_id=26) | 2000 | 未核得固定数值 | 按交易所／期间查询，保留交易与非交易日记录 |
| [`daily_basic`](https://tushare.pro/document/2?doc_id=32) | 2000 | 6000 | 每日15–17时更新；宣称全历史，未列精确最早日；其他独立频次未另列 |
| [`stock_basic`](https://tushare.pro/document/2?doc_id=25) | 2000 | 6000 | **50次/分**；文档列SSE／SZSE／BSE；默认仅L，须按范围另取D/P等并核对身份，不据目录推断其他产品覆盖 |
| [`bak_basic`](https://tushare.pro/document/2?doc_id=262) | 5000正式权限 | 7000 | 2016年起；股本亿股，daily_basic为万股 |
| [`dividend`](https://tushare.pro/document/2?doc_id=103) | 2000 | 2000 | 2000-01-01起，20–21时更新；按代码/公告/登记/除息/实施公告等条件查询 |
| [`adj_factor`](https://tushare.pro/document/2?doc_id=28) | 2000 | **未披露** | 支持单股全历史或单日全市场；5000可高频，独立数值未量化 |
| [`stock_st`](https://tushare.pro/document/2?doc_id=397) | 3000 | 1000 | 2000-01-01起，每日9:20更新；不是2000积分档已含 |
| [`suspend_d`](https://tushare.pro/document/2?doc_id=214) | 2000 | 5000 | 2000-01-03起；5000频次更高，独立数值未量化 |
| [`stk_limit`](https://tushare.pro/document/2?doc_id=183) | 2000 | 5800 | 文档称总量不限制，分钟流控未量化；含A/B股和基金，需筛实际研究对象 |
| [`fina_indicator`／`fina_indicator_vip`](https://tushare.pro/document/2?doc_id=79) | 普通2000；VIP 5000 | 普通100；VIP单次行限未在本次核得 | 普通按单股查询；VIP支持按季度取全市场，仍须检查返回覆盖和公告／修订语义 |

**独立权限按需另购，不能靠升积分档获得：**下表只列与常见 A 股研究相关的项目，不是完整价目。

| 独立产品 | 公开个人价格 | 能力及关键限制 |
| --- | --- | --- |
| A股历史分钟 | 2000元/年 | 1／5／15／30／60分钟，历史自2009年起；不保证完整二十年，也不包含其他市场／指数的分钟权限 |
| 公告信息 | 1000元/年 | 历史10年以上，含标题及PDF下载链接；不等于已经抽取和验证全部历史股本／行动版本 |
| 盘前股本 | 500元/年 | 近2年、更新次日股本；已有日频股本与此不同，不为长期历史需求默认购买 |
| A股实时日线 | 200元/月 | 盘中当日行情；收盘后日线研究通常不需要该实时权限 |
| A股实时分钟 | 1000元/月 | 实时1／5／15／30／60分钟；与历史分钟分别计费 |

以上价格、范围与独立授权关系来自[官方价目](https://tushare.pro/document/2?doc_id=290)。历史分钟 `stk_mins` 按单代码、频度及起止时间查询，返回OHLC、`vol`和`amount`；单次8000行、500次／分钟、**滚动24小时30万次**，不能套常规数据“无日总量上限”。完整二十年及量额语义见§8.6。[分钟参数／返回／限额](https://tushare.pro/document/2?doc_id=370)

**选档建议（分析判断）：**仅建基础量价和常用指标库，可先核200元档；需要历史风险警示、日期化股票列表、行业及批量财务指标时，500元档更值得优先试验。只有明确需要更高门槛端点或已接近额度时，再比较1000／1500元档。免费源保留为补缺与校验，不默认同时采购多家综合产品。

3000／6000／8000积分实际开通价格、升级补差应向权限中心确认，不自行按比例推算；缓存、离线回放、到期保留及发票条件仍待实际条款核实。官方价目注明个人与机构价格不同，且购买后不支持退款，采购前应确认所需端点及适用条件。更高价格不代表更长历史、完整PIT、全公司行动或全量权限。[个人价格与购买说明](https://tushare.pro/document/2?doc_id=290)

### 8.2 RQData：按流量核额度，SDK与HTTP格式不同

Python在已配置的许可/账号下初始化，返回DataFrame等结构；可查询当日`bytes_limit/bytes_used/remaining_days/license_type`。[使用手册](https://www.ricequant.com/doc/rqdata/python/manual)

```python
# 方法示例；证券范围与日期由调用者显式提供。
import rqdatac as rq
rq.init()
quota = rq.user.get_quota()
caps = rq.get_factor(ids, "market_cap_3", start_date, end_date)
pool = rq.all_instruments(type="CS", date=query_date, market="cn")
actions = rq.get_dividend(ids, start_date, end_date)
splits = rq.get_split(ids, start_date, end_date)
```

HTTP文档：账号密码JSON POST至`https://rqdata.ricequant.com/auth`取得token，再向`https://rqdata.ricequant.com/api`提交包含`method`与方法参数的JSON，请求头放`token`。token有效2小时，新token使旧token失效。数据响应是**CSV文本**，不是通用JSON；HTTP忽略`expect_df`。SDK认证与此token不能混同。[HTTP认证/数据格式/限制](https://www.ricequant.com/doc/rqdata/http/data-process)

| 项目 | 已核信息 | 仍待确认 |
| --- | --- | --- |
| SDK额度单位 | 手册按实际压缩传输流量计量，配额字段单位为字节 | HTTP是否相同扣额；错误/重试/重复取数如何扣量 |
| 日额度 | 手册介绍试用每日1G；商城FAQ列示1G/日条件 | 实际个人套餐和试用资格；G的确切字节数以账号值为准，不能把示例当全部套餐 |
| 设备/并发 | 商城FAQ列单账号同时单电脑、最多3线程 | 所选套餐对操作系统、设备和部署方式的具体限制 |
| HTTP单请求 | 过大get_factor会拒绝；文档近似为1因子×全市场8年、CSV约250M | 非固定行数/250M保证；所选字段和代码的有效分块，其他端点上限 |
| 频次 | 未核得统一每秒/分钟数值 | 不写“不限次”；确认实际流控、并发和退避要求 |
| 价格 | 未核得适用字段组合的公开金额 | 个人年/月费、PIT/行动附加权限、批量导出/增量费用 |

`get_factor`没有月频或离散日期列表参数：要么按信号日分别查询，要么取连续日频数据后本地筛月末。CSV字节、压缩计量字节和本地Parquet大小不能互换估算。商城条款和价格应以对应报价确认，不由本表推定适用。[商城/询价入口](https://www.ricequant.com/welcome/store/)

### 8.3 JQData：额度按条数，客户端还可能展开分页和重试

本地`jqdatasdk.auth`认证，`get_query_count()`查询当日`total/spare`。它们是条数，不是调用数；实际每类产品、字段、空响应/失败的扣额规则仍须确认。[SDK](https://github.com/JoinQuant/jqdatasdk)

```python
# 方法示例；日期化查询与历史版本完整性分别验证。
import os
import jqdatasdk as jq
jq.auth(os.environ["JQDATA_USER"], os.environ["JQDATA_PASSWORD"])
quota = jq.get_query_count()
pool = jq.get_all_securities(["stock"], date=query_date)
q = jq.query(jq.valuation.code, jq.valuation.market_cap)
caps = jq.get_fundamentals(q, date=query_date)
```

另有`get_valuation(codes, start_date, end_date, fields=...)`历史序列接口；`market_cap`为亿元、`capitalization`为万股，入库时市值统一为元、股本统一为股。行情`get_price(..., fq=None)`、状态`get_extras('is_st', ...)`与行动`finance.STK_XR_XD`分域核验，不能以平台回测能运行替代导出字段证明。[当前API源码](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/api.py)

| 项目 | 文档或源码说明 | 适用边界/待核 |
| --- | --- | --- |
| `get_fundamentals` | 客户端单次10000行 | 应按明确的代码组/date条件切分，不假设返回无限行 |
| `finance.run_query` | 当前master客户端上限5000行 | 是检索时源码值，不是服务端或所有已安装版本保证；未核实前不沿用旧4000值 |
| `run_offset_query` | 源码每页10000，最多20页，达到边界有截断警告 | 一次Python调用可展开多次db_query；不据函数成功判全部取齐 |
| 首次元数据 | finance首次查询可能取得表名/schema | 另计，不能只统计业务函数次数 |
| 请求重试 | 当前源码默认3次尝试、超时300秒，可配置；429为流控错误 | 下游统计实际网络尝试；不把默认重试带入未设定预算的批量取数 |
| 账号条数 | `get_query_count()`可查 | 实际账号额度/每类数据扣额公式须按账号核实 |
| 并发/频次 | 未核得可靠现行统一数值 | thread-local不表示无限并发；登录数/IP数/秒和分钟上限待确认 |
| 价格/试用 | 公开试用/购买说明未完整取得 | 当前金额、期限、历史范围和条数仍需确认；不套旧一年/100万条或平台VIP价格 |

源码依据：[SQL生成](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/finance_service.py)、[table.py](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/table.py)、[client.py](https://github.com/JoinQuant/jqdatasdk/blob/master/jqdatasdk/client.py)。源码值须在选定SDK版本及服务端上核实。试用/报价入口：[试用规则](https://www.joinquant.com/help/api/doc?name=logon&id=9830)、[购买方式](https://www.joinquant.com/help/api/doc?name=logon&id=9831)。[通用用户协议](https://cdn.joinquant.com/std/user/protocol/user.pdf)不足以单独确认JQData本地缓存和到期回放权。

### 8.4 免费源及其他商业源：不补造尚未公开的数值

| 来源 | 调用/返回方式 | 已知计数要点 | 尚未核齐 |
| --- | --- | --- | --- |
| BaoStock | Python `login`→query函数→遍历ResultData→DataFrame→`logout` | 查询可有上游分页；公司行动按代码和`operate`年度展开，登录/分页/重试另计 | 现行固定秒/分钟/日限、批量条数和风控规则；免费不等于不限频 |
| AKShare | 对应Python函数通常返回DataFrame；依各端点访问上游 | 一个函数可能发多页/多个HTTP；需分别记录函数调用和上游请求 | 没有可套所有源的统一配额/账号权益；实际端点历史、退市、单位、PIT和限频待核 |
| 掘金 | 官方旧版Python页说明`set_token`后可直接调数据函数，无需运行策略 | 该说明不能证明当前版本、全历史、导出和费用全部适用；非交易数据下载与账户成交导出分开 | 支持的SDK与系统、必需字段、每日/单次/并发限制、扩容价及缓存权 |
| Wind | Client API与Server API分别核对，支持Python等调用 | 终端费用、接口权限和落地数据套餐不能合并计价 | 具体接口/字段、认证、历史批量上限、个人价格及数据使用权 |
| iFinD | QuantAPI官网有SDK/数据服务入口 | 个人/机构及不同产品权限分别确认 | 账号方式、字段、配额、支持的系统、价格、缓存/到期条款 |
| Choice | 数据服务/量化接口产品，按对应SDK/产品文档核对 | 客户端接口、数据库交付及终端不是同一产品 | 必需字段、历史版本、个人API费用、请求/流量限制和数据使用权 |

入口：[BaoStock](https://www.baostock.com/)、[AKShare股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)、[掘金Token调用说明](https://www.myquant.cn/docs/python/python_other_api)、[Wind Client API](https://www.wind.com.cn/mobile/ClientApi/en.html)、[iFinD QuantAPI](https://quantapi.51ifind.com/)、[Choice数据服务](https://choice.eastmoney.com/product/datacenter)。部分官方页面无法完整访问，未知项保留，不用第三方承诺补齐。

### 8.5 行业／主题与成员接口

指数行情、成员关系和分类依据是不同产品，不能由一个接口存在推定其余内容合格。

| 产品／接口 | 调用方法与限额 | 历史边界 |
| --- | --- | --- |
| 申万行情 `sw_daily` | 按日期批量或代码＋区间；4000行／次，5000积分 | 默认申万2021版；指数历史不自动证明当时成员 |
| 行业成员 `index_member_all` | 行业层级／代码查询；2000行／次，2000积分 | 默认最新，历史必须取旧关系；纳入／剔除日不自动等于公开日 |
| 东财行情 `dc_daily` | 日期批量或代码＋区间；2000行／次，6000积分 | 2020年起，不能算完整十年主题行情 |
| 东财概览 `dc_index` | 日期化概览；5000行／次，6000积分 | 聚合涨跌家数不等于按成员计算的历史趋势宽度；概览不替代成员关系 |
| 东财成员 `dc_member` | 板块＋交易日／区间；5000行／次，6000积分 | 2024-12-20起，更早成员历史须另找来源 |
| 免费AKShare接口 | `index_hist_sw`、`stock_board_concept_hist_em`、当前目录／成员接口 | 一次函数可展开多次HTTP；申万历史接口无起止日期参数，可能重复返回全段；当前成员不可倒填历史 |

来源：[申万行情](https://tushare.pro/document/2?doc_id=327)、[行业成员](https://tushare.pro/document/2?doc_id=335)、[东财行情](https://tushare.pro/document/2?doc_id=382)、[概览](https://tushare.pro/document/2?doc_id=362)、[成员](https://tushare.pro/document/2?doc_id=363)、[AKShare指数](https://akshare.akfamily.xyz/data/index/index.html)、[AKShare股票](https://akshare.akfamily.xyz/data/stock/stock.html)。

### 8.6 日线／分钟的量额字段及历史覆盖

**日线和所列分钟K线均含对应区间成交量、成交额字段**，无需为这两个字段购买逐笔成交；不代表全部历史对象无缺值。

| 来源／接口 | 粒度 | 成交量／成交额字段与来源单位 | 入库处理 |
| --- | --- | --- | --- |
| Tushare `daily` | 日线 | `vol`为手，`amount`为千元 | A股量×100转股、额×1000转元；保留原值和单位 |
| Tushare `stk_mins` | 1／5／15／30／60分钟 | `vol`为股，`amount`为元 | 已是标准量额单位，**不能复用日线的×100／×1000转换** |
| RQData `get_price` | 日线／分钟 | `volume`与`total_turnover`为K线量额；当前通用字段表未在该处列单位 | 按适用A股字段合同和样例确认单位；原始量价显式用`adjust_type='none'`，复权选项可能同时调整成交量 |

来源：[日线字段](https://tushare.pro/document/2?doc_id=27)、[分钟字段及限额](https://tushare.pro/document/2?doc_id=370)、[RQData行情与复权参数](https://www.ricequant.com/doc/rqdata/python/generic-api)。

Tushare `stk_mins`官方1分钟示例为09:30至15:00共241条，不能将240条容量假设作为删除竞价bar的依据。RQData `get_price`默认复权方式为前复权；原始行情须显式指定`adjust_type='none'`，复权选项可能同时调整成交量。RQData另有盘后行情接口，日线与分钟量额能否直接对拍须核同一交易时段和产品口径。[Tushare分钟示例](https://tushare.pro/document/2?doc_id=370)、[RQData行情接口](https://www.ricequant.com/doc/rqdata/python/generic-api)

**历史覆盖证据**：Tushare公开价目表标A股分钟自2009年起，查询起点早于2009年的窗口不能仅由该产品覆盖。RQData官方方案简介（历史材料）列A股分钟自2005年起，可作为二十年询价／试用候选；该简介不证明当前账号权限、北交所或全部退市对象完整，仍需核验并取得适用报价。不能用分钟接口“支持某频度”推定该频度的所有年份均可获得。[Tushare历史起点](https://tushare.pro/document/2?doc_id=290)、[RQData方案简介第1页](https://assets.ricequant.com/welcome/%E7%B1%B3%E7%AD%90RQData%E9%87%91%E8%9E%8D%E6%95%B0%E6%8D%AE%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E7%AE%80%E4%BB%8B.a528f1bd.pdf)

分钟单位、时段及历史覆盖须按具体账号验证。不同年限的条数、磁盘和请求量计算见[系统数据与数据源方案](A股量化系统数据与数据源方案.md)。

### 8.7 日期、代码与调用的补充核查（2026-10-23）

| 接口／事项 | 已核通用事实 | 不能由此推定 |
| --- | --- | --- |
| [`bse_mapping`](https://tushare.pro/document/2?doc_id=375) | 2000积分，1000行；按`o_code/n_code`查`name/o_code/n_code/list_date` | `list_date`为上市日，不是代码切换日；不能随意试后缀或把映射空当无历史 |
| [`namechange`](https://tushare.pro/document/2?doc_id=100) | 输入起止为公告日期；返回名称有效区间、`ann_date/change_reason`；公开页未列积分和固定行限 | 名称变更不能替代永久证券身份、改码或换股处置；未披露限额不能写成不限 |
| [`trade_cal`](https://tushare.pro/document/2?doc_id=26) | 官方说明三所日历相同，北交所参考沪深；参数表未列BSE | 不应为此猜测BSE参数；仍按真实上市期解释北交所行情 |
| [`dividend`](https://tushare.pro/document/2?doc_id=103) | 已列参数只有代码或公告/登记/除息/实施公告日，至少一个；没有已列起止区间参数；`end_date`返回值为分红年度 | 不发送臆造区间参数、不用报告年覆盖实施窗；`base_share`万股不是全期实际总股本证明 |
| [`sw_daily`](https://tushare.pro/document/2?doc_id=327) | 指数点位；成交量万股、成交额及市值万元 | 不能套个股`daily`的手／千元转换；指数不是历史成员关系 |
| [`stk_seasoned`](https://tushare.pro/document/2?doc_id=494) | 增发明细，2000积分、3000行，声明1991起；包含进程、首次/变动公告、实际发行量和新增股上市字段 | 增发不是配股／换股／吸收合并／退市补偿的统一完整接口，`update_flag`不自动证明旧版本均保留 |

[`stock_basic`官方建议取后本地保存](https://tushare.pro/document/2?doc_id=25)。[服务协议](https://tushare.pro/document/1?doc_id=405)同时列个人非商业、有限期许可及到期停止服务；没有据此确认所有原件、备份及派生数据在到期后的长期保存／回放权。此差异须按实际用途和适用授权确认，不能把接口的保存建议推广为全部数据永久授权。

当前[官方DataApi源码](https://github.com/waditu/tushare/blob/master/tushare/pro/client.py)的`query`可见一次`requests.post`，没有显式分页／重试循环，但库层重定向和底层传输不能靠函数次数推定。[HTTP教程](https://tushare.pro/document/1?doc_id=130)及该源码默认地址为HTTP；安全REST传输的实际支持需账号适用说明确认，不能把另一个HTTPS MCP端点当同一REST合同。调用使用安全凭据配置，公开材料不足时保留未知，不在核查中试发Token。

[官方变更日志](https://tushare.pro/document/1?doc_id=9)提示多代码参数机制调整，与个别端点表仍写支持多值的描述存在差异；未核实前采用单码或日期截面，不假设逗号列表/offset一定有效。旧日期化估值表没有显式公开／修订字段，不等于已经证明供应商完全不做PIT；可靠处理规则及原响应仍可作为证据，当前不能凭交易日期或非空响应自行放行。

## 9. 用量计量原则

逻辑查询、SDK函数调用、展开后的产品请求、网络尝试、返回记录数和计费字节是不同量。认证、分页、失败与重试分别核算；不能用“每分钟调用次数×每次最大行数”直接承诺批量下载工期。

估算历史与增量时，先明确接口可按日期、代码还是区间拆分，再计算满足返回上限的批次数；按额度实际采用的条数或字节估费用，并用小样本观察修正。压缩传输量、未压缩响应和本地Parquet大小不能互换。容量与通用增量/补齐方法统一见[系统数据与数据源方案](A股量化系统数据与数据源方案.md)。

## 10. 尚需确认的信息

| 信息 | 公开资料已能支持 | 仍需账号、样例或正式报价 |
| --- | --- | --- |
| A股研究接口 | Tushare、RQData、JQData主要字段与调用路径；免费源和商业源入口 | 选定SDK/服务端实际行为、权限、完整历史及修订链 |
| 价格 | Tushare公开档位与部分独立权限 | RQData/JQData/iFinD/Choice/Wind具体数据组合价；掘金当前免费/付费权益及其他渠道适用套餐与增值费 |
| 调用限制 | Tushare端点数值、RQData流量计量、JQData源码分页/重试 | 实际账号额度、免费源流控、并发/IP及失败重试扣费 |
| 行业/主题 | 部分行情、成员端点及明确历史起点 | 不同分类体系、旧成员与公开时点、缺失范围 |
| 长期日线/分钟 | 频度、量额字段、部分历史起点 | 各市场与早期退市对象覆盖；不将平台最早年份当成所有对象完整覆盖 |
| 券商渠道 | QMT/PTrade行情及交易能力、开通路径 | 券商版本、终端依赖、资金门槛、Level-2种类、历史下载和离线使用权 |
| 本地与到期权利 | 存在本地SDK/HTTP不等于已取得无限缓存权 | 试用建库、订阅到期保存/回放、备份和再分发条件 |

本报告以来源可回查、事实与未知分开、产品与用途对应为维护标准。具体采购、接入和研究结论应另行记录，避免将执行过程混入可复用来源资料。

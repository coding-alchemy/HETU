# 原始来源导航

选择来源前读取相关条目，出现缺口或替代来源时回查。按核心问题选择经济体、原发布方和具体表／序列／政策原文；S01–S18 是导航，不是每次全取清单。全球包含中国，中国来源只取与本次外溢问题相关的资料，不扩展为中国本土全景。研究行为见[研究规则](research-rules.md)，证据字段及交付见[报告约定](report-contract.md)。

## 1. 既有来源状态与使用边界

下表来源状态记录日期为 **2026-10-18**，保留当时的核验范围与限制，供本包独立选源使用；它们不是本包本次取证结果，也未因其他中国宏观 Skill 的新实读记录而升级。**已读**仅指相应原始页面或方法说明已打开；**已定位／候选**不表示已取得正文或具体数据。列示 API 均未实测，不代表已接入。此前读取受限或失败只说明当时结果，不表示永久不可用。

| 来源 | 宏观用途 | 已有核验与限制 |
| --- | --- | --- |
| S01 [国家统计局发布](https://www.stats.gov.cn/sj/zxfb/)、[国家数据](https://data.stats.gov.cn/) | 增长、就业、物价、需求及综合景气 | 发布目录已读；数据库依赖 JS，统一公共 API 未确认；区分月度／累计、季调及修订 |
| S02 [中国人民银行统计](https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html)及政策公告 | 利率、货币、社融、信贷与流动性 | 统计目录已读，政策需定位具体决定；社融存量／增量和主体结构分开 |
| S03 [财政部国库统计](https://gks.mof.gov.cn/tongjishuju/) | 财政收支、政府性基金、债务及执行 | 目录与财政正文已读；预算、执行、决算以及中央／地方口径分开 |
| S04 [外汇局统计](https://www.safe.gov.cn/safe/tjsj1/index.html) | 国际收支、储备、外债、结售汇与国际投资头寸 | 目录已读；月／季、初步／正式、流量／存量和修订分开 |
| S05 [海关查询入口](https://online.customs.gov.cn/mySearch/)、[WTO 数据](https://data.wto.org/en) | 总量贸易、伙伴结构和重大贸易政策 | 两个服务／数据目录已读；海关统计子站与 WTO API 门户此前读取受限，未验证自动获取 |
| S06 [Fed 数据](https://www.federalreserve.gov/data.htm)、[纽约联储市场栏目](https://www.newyorkfed.org/markets) | 政策、利率、央行资产负债、银行信用、贷款标准、回购与美元融资 | Fed 目录已读，纽约联储栏目已定位；各系列日／周／月／季频率不同，操作与实际使用分开 |
| S07 [BEA 数据与 API](https://apps.bea.gov/api/signup/)、[BLS API](https://www.bls.gov/developers/api_signature_v2.htm) | 美国 GDP、消费、PCE、CPI、就业工资和生产率 | 文档已读；BEA 需注册，BLS 注册／非注册额度不同；未实测，须保留发布脚注及修订 |
| S08 [美国财政部 TIC](https://home.treasury.gov/data/treasury-international-capital-tic-system)、[Fiscal Data](https://fiscaldata.treasury.gov/api-documentation/)、[利率目录](https://home.treasury.gov/resource-center-data-chart-center/interest-rates) | 跨境证券流、财政、债务供给及曲线 | TIC 已读；Fiscal Data 由官方引导确认但此前页面读取失败；利率具体表待核。持仓变化不等于净买卖 |
| S09 [ECB 统计](https://www.ecb.europa.eu/stats/html/index.en.html)、[Eurostat 数据库](https://ec.europa.eu/eurostat/web/main/data/database)、[Eurostat API](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction) | 欧元区／欧盟的政策、增长、HICP、就业与外部部门 | 页面已读，Eurostat 支持 API；不提供完整历史版本库，不能由最新修订值重建当时判断；ECB Portal 此前受限 |
| S10 [BOE 数据库](https://www.bankofengland.co.uk/boeapps/database/)、[BOE 曲线](https://www.bankofengland.co.uk/statistics/yield-curves)、[ONS](https://www.ons.gov.uk/) | 英国政策、货币信用、曲线和宏观统计 | 页面已读；BOE 曲线为可修订拟合值，提供文件但无曲线 API；ONS API 为 Beta，未实测覆盖 |
| S11 [BOJ 时序库](https://www.stat-search.boj.or.jp/index_en.html)、[官方 API 说明公告](https://www.boj.or.jp/en/statistics/outline/notice_2026/not260218a.htm) | 日本货币信用、利率汇率、价格和总量景气 | 数据库与公告已读，确认 JSON／CSV 能力，未调用；政策决定及干预须回具体发布机构原文 |
| S12 [BIS 数据](https://data.bis.org/)、[取得方式](https://data.bis.org/help/tools) | 全球流动性、跨境信用、部门杠杆、偿债负担、有效汇率和金融稳定 | 门户与说明已读，有 SDMX 和批量下载；所在地／合并口径不同，借款总量不等于套利交易 |
| S13 [IMF 数据](https://www.imf.org/en/data)、[OECD API](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html)、[World Bank API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) | 各经济体增长、财政外部脆弱性、结构和预测对照 | 官方入口／说明已读，具体数据集未全面取得；估计、实绩、预测及版本分开，新兴市场覆盖逐项确认 |
| S14 [EIA Open Data](https://www.eia.gov/opendata/) | 仅取能源基准价格和解释宏观供给冲击所需的总量信息 | 目录和 API 说明已读，需注册、未实测；电厂、炼厂及细分成本分析不进入宏观任务 |
| S15 [中国政府政策库](https://www.gov.cn/zhengce/zhengceku/)、[WTO](https://data.wto.org/en)、[OFAC](https://ofac.treasury.gov/sanctions-list-service)及各国正式公告 | 重大财政、贸易、跨境金融和政策事件 | WTO／OFAC 入口已读，政策库为候选；实际判断须定位具体原文、生效范围与日期，不做实体合规筛查 |
| S16 [纽约联储 GSCPI](https://www.newyorkfed.org/research/policy/gscpi)、[编制说明](https://libertystreeteconomics.newyorkfed.org/2022/01/a-new-barometer-of-global-supply-chain-pressures/) | 总量供应链压力及其通胀／增长影响 | 数据页框架已读，编制说明已定位，具体序列未取得；综合指数不能替代航运行业研究 |
| S17 [中国货币网](https://www.chinamoney.com.cn/)、[中债](https://www.chinabond.com.cn/)、[CFTC 持仓报告](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm)及目标市场原发布方 | 跨资产价格、曲线、整体利差与必要的资金／持仓参照 | LPR 入口已定位、COT 正文已读；其他具体序列与许可待核，股票宽基及全球信用利差的数据产品尚待确定；不能声称已有完整全球行情 |
| S18 [金融监管总局](https://www.nfra.gov.cn/)、研究地区财政部门、地方债及城投债披露原发布平台 | 中国银行体系总量、地方财政与债务、城投融资及区域信用压力 | 补充候选，具体表格／披露范围、汇总口径与使用权限待核；尚未取得可比的城投融资序列，不声称覆盖全部平台债务或隐性债务 |

### 1.1 2026-10-24美日专题的增量核验

上表保留2026-10-18记录；以下仅补记本包一次美日专题实际读取的范围，不认证其他经济体、接口或宿主，也不替代每次研究的时点与版本核对。

| 对应来源 | 本次实际取得与限制 |
| --- | --- |
| S06 Fed／纽约联储 | [9月FOMC声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm)、[7月SLOOS](https://www.federalreserve.gov/data/sloos/sloos-202607.htm)正文及会议日程已读；[SOFR方法页](https://www.newyorkfed.org/markets/reference-rates/sofr)已读，但动态数值未得，日期查询API经本次工具不可访问，不称已取得美元融资序列。 |
| S07 BEA／BLS | 二季度GDP第三次估计、8月收入支出/PCE、[9月CPI归档](https://www.bls.gov/news.release/archives/cpi_10142026.htm)及就业归档正文／表已读。保留各自发布版本、季调和修订；BEA／BLS数据API未实测。 |
| S08 美国财政部 | [名义曲线表](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?field_tdr_date_value=2026&field_tdr_date_value_month=202610&type=daily_treasury_yield_curve)及同日实际曲线的表头、日期行和方法已读；为指示性报价拟合值，精确上网时分与完整修订日志未知。没有由此核验TIC、Fiscal Data或净发行。 |
| S11 BOJ及日本原发布方 | BOJ政策PDF、购债计划、[9月短观](https://www.boj.or.jp/en/statistics/tk/yoshi/tk2609.htm)和会议日程已读；另实际读取内阁府二季度GDP二次速報、统计局9月CPI与[e-Stat固定文件](https://www.e-stat.go.jp/stat-search/file-download?fileKind=2&statInfId=000040513537)、厚生劳动省8月工资确报、财务省8月国际收支。CPI滚动PDF解析与图像冲突时改核固定文件；工资与最新CPI的历史版本衔接仍受限。日本财务省国债CSV本次返回不支持的内容类型，曲线数值未得；BOJ时序库API未实测。 |
| S17 目标市场原发布方 | [BOJ汇市日报](https://www.boj.or.jp/en/statistics/market/forex/fxdaily/fxlist/fx261023.pdf)已读，保留东京观察时点和日元／美元报价；与S11属同一底层来源。美债曲线同S08。未取得完整全球行情、黄金现价、宽基估值、跨币种基差或仓位，不宣称这些产品已接入。 |

上述通过公开搜索／浏览读取；网页可读不等于获得整库抓取或再分发许可。具体表、观察期、公开／修订时间、采用与失败应以当次证据附录为准；未列的新核验保持原状态。


### 1.2 2026-10-25资产与融资路径的增量核验

以下仅限本次当前宿主实读字段，保留§1及§1.1历史记录；不证明全部行情、历史、API或许可已接入。真实来源与离线合成分析分别判断，公开/修订时间未知仍须当次核对。

| 路径 | 实际取得与限制 |
| --- | --- |
| BTC价格 | [CF BRR](https://www.cfbenchmarks.com/data/indices/BRR)原取得单点及日频多场所定义；CME历史xlsx浏览不支持、下载超时，原Kraken浏览仅元信息。2026-10-25定向补取[Kraken公开Trades](https://api.kraken.com/0/public/Trades?pair=XBTUSD)实际1000笔，选同场所BTC/USD两笔80,758.70／80,768.90美元，UTC 03:55:19.197687／04:29:52.269397；[官方定义](https://docs.kraken.com/api-reference/market-data/get-recent-trades)及公开端点说明已读。所选成交两点可比，不与CF拼接；日收盘、完整历史及实时延迟仍未证。 |
| BTC基金 | [IBIT原披露](https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf)取得2026-10-23份额、资产/NAV及持仓口径；[同基金SEC年报](https://www.sec.gov/Archives/edgar/data/1980994/000143774926006058/bit20251231_10k.htm)取得2024/2025年末份额/资产及篮子申赎描述。未复原即时净流量或全市场，不将存量变化当申购。 |
| 美元稳定币 | [Circle透明度](https://www.circle.com/transparency)动态金额空，所链[八月储备鉴证](https://6778953.fs1.hubspotusercontent-na1.net/hubfs/6778953/USDCAttestationReports/2026/2026%20USDC_Examination%20Report%20August%2026.pdf)取得8月11/31日流通定义、储备分类/数值、回购及结算调整说明；该examination为合理保证鉴证，非完整财报审计或实时储备。另读[正式USDC条款](https://www.circle.com/legal/usdc-terms)的账户资格/直接赎回及拒绝、暂停等限制。未核全部地区/费用，未取得全部发行资金原来源。 |
| 黄金 | [SAFE官方原表](https://www.safe.gov.cn/safe/2026/0206/27116.html)已有2026年8/9月数量（万盎司）、美元/SDR金额与SDR换算，SAFE黄金估值方法仍未证。2026-10-25采用[IMF官方年报附录](https://www.imf.org/external/pubs/ft/ar/2025/pdfs/imf-annual-report-2025-appendices.pdf)第3页Appendix I.1的All countries黄金汇总作代表路径替代：2023/2024年末1157.7/1165.5百万金衡盎司，伦敦市场价估值1767.8/2297.1十亿SDR；表内行名及脚注1–2支持估值、单位和年末定义。样本改为IMF/IFS全球官方持有量，不与SAFE拼接，不推SAFE实际方法。完整交易/数量调整拆分与首次公开/修订时点仍未证；黄金市场价/基金路径仍候选。 |
| 美债抵押融资 | [NYFed SOFR方法](https://www.newyorkfed.org/markets/reference-rates/sofr/)已读，动态数值未返；[Fed FRED替代](https://fred.stlouisfed.org/series/SOFR?_hsmi=317232954)取得2026-10-19等日期值、日频非季调及原来源。只证明转载观察可读，NYFed动态/API、工具实际使用或被迫出售未证。 |
| 广义汇率 | [BIS方法](https://data.bis.org/topics/EER?lang=en)取得名义/实际、篮子和滞后，未取序列；[Fed替代原表](https://www.federalreserve.gov/datadownload/Preview.aspx?pi=400&preview=H10%2FH10%2FJRXWTFB_N.B&rel=H10)取得10月15/16日同系列名义广义两点，另读[指数说明](https://www.federalreserve.gov/releases/H10/summary/default.htm)及[篮子](https://www.federalreserve.gov/releases/h10///weights/)。同表10月15日121.5693至16日121.4048，相对下降约0.1353%的有限比较成立；其他页面基期标签差异仅登记为跨版本衔接未证；只在同表同定义比较，不混BIS/双边或其他基期序列。BIS下载/API仍未证。 |

没有取得的路径保持候选／未证；部分正文或定义可读不能升级为全字段可用。页面公开可读不等于获得整库再分发许可。

### 1.3 2026-10-25取证采用与历史路径的增量核验

本次截止2026-10-25 19:18:57北京时间，保留§1–§1.2记录；以下仅证明当前宿主实际取得的字段与期间，不认证整库、所有接口或完整历史。

| 路径 | 实际取得与限制 |
| --- | --- |
| S07就业 | BLS就业发布及[CES FAQ](https://www.bls.gov/web/empsit/cesfaq.htm)、[官方目录](https://download.bls.gov/pub/time.series/CE)已读；三份原文件浏览不支持内容类型、直接下载403。沿[FRED PAYEMS](https://fred.stlouisfed.org/series/PAYEMS)、[私人时薪CES0500000003](https://fred.stlouisfed.org/series/CES0500000003)、[私人周工时AWHAETP](https://fred.stlouisfed.org/series/AWHAETP)取得公开CSV及定义，含2025-10—2026-09最新12月及所需基数，均SA，原来源BLS、当前更新2026-10-02。分别完成同比/相邻比较；总非农岗位与私人工资/工时覆盖不同，不证全体购买力。完整初值vintage及其他接口未证。 |
| S07物价 | [BLS 9月发布/季调说明](https://www.bls.gov/news.release/archives/cpi_10142026.htm)及FRED [总体CPIAUCSL](https://fred.stlouisfed.org/series/CPIAUCSL)、[核心CPILFESL](https://fred.stlouisfed.org/series/CPILFESL)定义与CSV已取，1982–84=100、SA，2026-10-14更新。2025-10—2026-09窗口两条10月2025值均空，故11/12实值；其后11月相邻比较受限，其他可算同比/月比仍完成。SA同比不冒充官方NSA同比；空值原因本次未核，完整窗口未通过，BLS原API未测。 |
| S08 TIC | [官方npr_history原表](https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/npr_history.txt)实际下载，含2025-09—2026-08最新12月与基数、百万美元非季调、多行表头及SLT/BL/BC定义；同表第21/22/29/30列长期净取得、短期/托管变化、银行净美元负债变化及总净流入分别复算并用于短分析。表内生成2026-10-13、HTTP更新10月16；不与旧S口径拼接，不以流量当持仓或当前美元压力；完整修订日志/其他接口未证。 |
| BTC市场日线 | [Kraken OHLC定义](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)与[OHLCVT说明](https://support.kraken.com/in/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data)已读；公开OHLC端点XBTUSD、1440分钟实际返回31日桶2026-09-25—10-25，最后未结束桶排除。选9/25—10/16同场所已结束日桶收盘84090.5→81500.8美元/枚，约−3.0797%；同日期Fed广义美元约+0.8932%、财政部10年实际曲线−9BP用于限制单因果。日桶最后成交与宏观报价时刻不完全同步，只作区间对照，不拼CF或旧分钟成交，不证全市场趋势/资金归因。 |
| 黄金市场价格 | [LBMA价格页](https://www.lbma.org.uk/prices-and-data/lbma-precious-metal-prices)实际说明10:30/15:00 London基准及历史许可；[WGC页](https://www.gold.org/goldhub/data/gold-prices?gad=1)方法/单位可读、所链均价xlsx403。另核GoldPrice.com/Metals.dev日收盘说明，CSV内部错误、直接页403及目标日期页不可达；未取得所选窗口两点市场价及适用完整方法，仍未通过。没有登录/注册/采购；SAFE或IMF储备估值不替代市场价。 |

本次S1/S3/S4代表路径成立，CPI与黄金保留上述最小缺口；未测产品、许可、全历史及其他宿主不升级。各次研究仍须核所用时点与版本，不直接继承本次值为未来事实。

### 1.4 2026-10-25两项定向补证

保留§1.3当时未齐的记录；本次仅补实际读取结果，不认证完整行情、全部接口或宿主。

| 路径 | 新取得范围、定位与限制 |
| --- | --- |
| S07物价缺值原因 | [BLS 2025年11月归档](https://www.bls.gov/news.release/archives/cpi_12182025.htm)正文、Table A及Table 1与[停摆FAQ](https://www.bls.gov/cpi/additional-resources/2025-federal-government-shutdown-impact-cpi-faq.htm)§2–3实际已读。归档发布2025-12-18，FAQ末改2026-09-16：2025-10调查未采集且无法补采；总体/剔除食品能源对应值及10/11月相关月比未发布，少数非调查分项另有值。两条FRED SA序列保留2025-10—2026-09窗口各11/12实值；10月及11月月比不造值，其他比较仍用同版本。BLS内部估计不当发布值；不由当前空格认定所有分项均缺。 |
| 黄金市场报价 | 从[9月25日Reuters转载](https://www.marketscreener.com/news/mounting-rate-hike-bets-keep-weekly-loss-in-sight-for-gold-ce785adfde8aff23)及[10月16日Reuters转载](https://in.marketscreener.com/news/gold-set-for-a-weekly-drop-as-traders-assess-fed-signals-ce785cdadd8ff424)正文取得同类美元现货黄金观察，分别18:05/18:07 GMT、4282.98/4165.12美元/金衡盎司；不是同文期货结算或LBMA定盘。三周约−2.7518%，可与同日期美元/实际曲线作区间对照；两分钟和宏观时点差异不支持精确事件归因。转载署Reuters，公开可读不等于整库抓取/再分发许可；底层合成算法、可成交Bid/Ask和完整修订日志未核。 |

本次缺值原因核查完成不等于两条CPI的12个月全值已齐；黄金只核上述现货区间，不混其他产品或储备估值。后续研究仍按自身时点核所用版本，不直接继承本次行情为未来事实。

### 1.5 2026-10-26主权融资代表路径核验

保留以上历史状态。本次通过公开浏览取得以下官方原表，不证明当前全历史、全部期限或API已接入。

| 路径 | 实际读取、可完成比较及限制 |
| --- | --- |
| S08付息与收入 | 从[MTS目录](https://fiscal.treasury.gov/accounting/monthly-treasury-statement)进入[Previous Issues](https://fiscal.treasury.gov/accounting/monthly-treasury-statement/previous)的FY2025 August，读取[官方PDF](https://fiscal.treasury.gov/system/files/files/reports-statements/mts/mts0825.pdf)第38页Table 9的Total receipts、Net Interest和同期列，第3、39页核modified cash basis及公共债务利息应计确认。FY2024/FY2025前11个月收入4,391,116/4,690,953、净利息843,384/933,459百万美元，负担约19.21%→19.90%，+0.69个百分点。该代表路径证明同版同范围同期核算，不作为2026年当前负担；2026年9月PDF本次403、两项API请求经工具不可访问，未取得该版净利息。 |
| S08拍卖承接 | TreasuryDirect [2026-09-09](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20260909_2.pdf)与[2026-10-07](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20261007_2.pdf)官方结果均实际读取。两次同CUSIP 91282CRF0、原始10年名义固定利率续发、2036-08-15到期，剩余期限9年11月/9年10月；非SOMA获配约390亿美元，原表Bid-to-Cover 2.71→2.77（投标/获配小计，含非竞争性），High Yield 4.834%→5.300%。倍数略升与收益率上升同时保留，不把收益率差当尾差，不把单次结果当全市场或长期结论；无对应发行前报价，不计算尾差，类别不代表国籍。 |

公开可读与原文摘取不代表整库下载或再分发许可；首次发布时分及完整修订日志未知。原件返回、定位、计算及失败见本轮增量验收，不将旧受限路径升级为全部可用。

### 1.6 2026-10-27逐期历史路径增量核验

本次仅核以下目标窗口及比较，保留§1.1–1.5历史状态，不认证全部系列、历史版本或宿主。原发布目录/交互框架与实际原数据分别登记，今后仍按研究截止核版本。

| 路径 | 实读定位、字段与历史取得方法 | 覆盖与限制 |
| --- | --- | --- |
| S07私人实际需求 | BEA[2026Q2第三次估计](https://www.bea.gov/news/2026/gdp-third-estimate-industries-corporate-profits-state-gdp-and-state-personal-income-2nd)及[定义/统计惯例](https://www.bea.gov/news/gdp-release-additional-information)已读。目标Real final sales to private domestic purchasers为消费加私人固定投资，不是GDI；交互表仅返框架后，实读FRED的BEA转载[水平LB0000031Q020SBEA](https://fred.stlouisfed.org/series/LB0000031Q020SBEA)（Table1.4.6，十亿2017链式美元、季度SAAR）及[折年率PB0000031Q225SBEA](https://fred.stlouisfed.org/series/PB0000031Q225SBEA)（Table1.4.1/1.17.1）。使用[公开CSV](https://fred.stlouisfed.org/graph/fredgraph.csv?id=LB0000031Q020SBEA%2CPB0000031Q225SBEA&cosd=2023-07-01&coed=2026-06-30)直接下载后核实际返回的observation_date及两列，不能由请求日期推定覆盖。 | 取得2023Q3—2026Q2的水平及所需基数，正文2024Q3—2026Q2八季同比/普通季环比及官方折年率均采用，同版2026-09-30更新。普通季环比按水平比值；原发布折年率另按(1+SAAR/100)^(1/4)−1换算，舍入差异分列，不能由折年率倒推精确水平。参考年是实际金额标尺，链式权重随时期变化、分项不机械相加。完整vintage、修订日志、BEA API及其他窗口未证，错误GDI候选不替代。 |
| S04银行结售汇 | 从[官方历史目录](https://www.safe.gov.cn/safe/2023/0215/22329.html)的“银行结售汇数据时间序列”取得[本次原xlsx](https://www.safe.gov.cn/safe/file/file/20261020/4eeaa0fb8f90475fbd5de27e1cd0ea24.xlsx)。“以美元计价（月度）”第3行亿美元、第4行月标，第5/15/25行结汇/售汇/直接差额；按月标定位列，不把序列页面原创建日期当最新观察期。 | 同版2026-10-20历史文件有2024-10—2026-09原值；2025-10—2026-09十二月逐项适用同比/相邻比较及2025/2026年1—9月同版月量加总累计已采用。银行自身与代客均含，兑换行为时点，含远期履约/期权行权、不含银行间。未季调，零负差额基数仅局部限制，不取消其他比较；完整修订日志/接口未证。 |
| S04代客涉外收付 | 从[另一官方历史目录](https://www.safe.gov.cn/safe/2018/0419/8806.html)取得[本次原xls](https://www.safe.gov.cn/safe/file/file/20261020/c8db913913c0403ab9df424464f672ac.xls)。本次用已有本地格式转换读取“以美元计值（月度）”：第3行亿美元、第4行月标，第5/23/41行收入/支出/直接收支差额；保留原xls与转换出处，转换工具不成为本包依赖。 | 同版同窗口十二月、窗口外基数及两年1—9月累计比较均采用。包括人民币和外币，不含银行自身和现钞；美元计值不等于仅美元收付，与结售汇统计对象/时点不同，两净额不相加当全部资本流。表注说明顶层总额不等于经常、资本金融账户分项之和；不强拼分项或未读旧版公告。 |

本轮浏览CSV及两工作簿分别因工具不可访问/不支持格式受限，公开直接下载取得原件并回读后才更新为已取得；失败不泛化为来源不可得。未来路径仍须沿当期官方链接定位，不能假定本次静态文件永不修订。公开引用使用与批量再分发许可分开；不新增商采或第五份规则。

### 1.7 2026-11-04—05市场定价与融资的增量核验

本轮信息截止2026-11-04 23:39北京时间，获取跨至11月5日；以下仅代表路径，未完成项不被离线充分材料替代，旧核验继续按原时点有效。

- **政策与CPI：** CME联邦基金期货结算页仅框架，Barchart两目标历史页要求机器人验证，未取得两观察日同目标价格；不绕过、不用SOFR替代。CME [FactCard公开原文转载](https://www.kisfutures.com/FactCard30Dayfedfundfutures.pdf)核月平均EFFR与100减报价方法，不能据方法称已取数据。2026-10-14 CPI的[Reuters事前公开转载](https://uk.marketscreener.com/news/high-gasoline-prices-expected-to-boost-us-consumer-inflation-in-september-ce785dd2db89fe26)与[BLS对应首发归档](https://www.bls.gov/news.release/archives/cpi_10142026.htm)已读，可分别比较总体/核心月比及同比；未披露调查样本与中位数不补造。财政部同表10月13/14日日曲线可比，不混入新闻日内点位作精确事件归因；本轮详细新版算法未取得。
- **标普500：** FactSet [10月23日](https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_102326.pdf)及[10月30日](https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_103026.pdf)原PDF已下载、文本及实际图片回读，固定CY2027 bottom-up EPS及对应前日收盘价格可比，可自算固定年度PE；原文滚动NTM PE另列。未核逐日成分vintage或实时接口；公开报告不等于整库许可。
- **融资价格：** [IG OAS](https://fred.stlouisfed.org/data/BAMLC0A0CM)、[HY OAS](https://fred.stlouisfed.org/data/BAMLH0A0HYM2)公开表实际取得2026-09-09—11-03日值；配系列页核美元、评级、期权调整及相对Treasury曲线的市值加权方法。原CSV浏览失败后的同源表替代成立；ICE数据许可限制保留。BOJ [10月23日FSR原PDF](https://www.boj.or.jp/en/research/brp/fsr/data/fsr261023a.pdf)Chart II-2-8仅止9月底，三个月项是FX swap premium、一年项才是basis swap，不能替代本轮三个月JPY/USD跨币种基差八周序列；该路径仍未完成。
- **美国供给：** [Fiscal Data拍卖](https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query)的CUSIP、原始期限、发行/到期日及total_accepted，配[回购操作](https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/buybacks_operations)结算和[回购明细](https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/buybacks_security_details)面值，可复算2024Q4—2026Q3国库券及原始2/3/5年名义固息Note子集。total_accepted包含SOMA，不是私人吸收；MSPD Table3余额组成字段不能替代流量。周末/假日现金时间参照[NYFed rollover FAQ](https://www.newyorkfed.org/markets/treasury-rollover-faq-05042022)。[11月4日季度再融资声明](https://home.treasury.gov/news/press-releases/sb0678/)及[未来正式拍卖日历](https://home.treasury.gov/system/files/221/TentativeAuctionScheduleQ42026.xml)已读；保存HTML的官方发布字段为`2026-11-04T13:30:00Z`（北京时间21:30），早于本轮23:39:44截止，纠正此前漏提取登记，仅适用于声明及其正文安排，其他XML版本时点未由此确认。两笔已发bill获配及已宣布回购细表尚未取得；未公布未来发行/SOMA保持未知，不宣称完整未来净供给；其他原始期限、TIPS/FRN、本金调整未在本子样本核。
- **欧洲：** Eurostat公开Statistics API的`gov_10q_ggnfa`（Q、MIO_EUR、NSA、S13、FR/IT、TR/TE/B9）及`gov_10q_ggdebt`（GD）2026-10-21版已取2023Q3—2026Q2，支持八季及同比/上季、半年完全同期；[执行口径](https://ec.europa.eu/eurostat/cache/metadata/en/gov_10q_ggnfa_esms.htm)和[债务口径](https://ec.europa.eu/eurostat/cache/metadata/en/gov_10q_ggdebt_esms.htm)已读，保留临时值与修订。AFT同ISIN FR0014018YR0的[9月3日](https://www.aft.gouv.fr/en/publications/communiques-presse/03-september-2026-issuance-oats)及[10月1日](https://www.aft.gouv.fr/en/publications/communiques-presse/01-october-2026-issuance-oats?trk=article-ssr-frontend-pulse_little-text-block)正文与[发行制度](https://www.aft.gouv.fr/en/issuance-techniques)可读，XLS原下载403；竞争获配不混后续非竞争发行。意大利央行[结果目录与制度](https://www.bancaditalia.it/compiti/operazioni-mef/risultati-aste/index.html)所链当年ZIP/XLS成功下载，IT0005729931的9月29/10月29普通续发可比。三国ECB日频候选失败，唯一Investing同类替代仅10月5日起一个月可读、扩八周失败；其底层切券与精确定价方法未核，仍缺承诺的共同八周，不能用月均或一月替代。两国拍卖不作跨国覆盖倍数排名。

### 1.8 2026-11-05剩余来源定向补证

保留§1.7当时状态；以下是后续实际取得范围，原截止仍为11月4日23:39:44北京时间。获取新原件不等于其全部字段或版本在旧截止前可知。

- **美国已公布安排：** 从实际公开链接回到TreasuryDirect正式[TM9结果](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20261019_2.pdf)和[VA2结果](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20261020_1.pdf)，核CUSIP、拍卖日、发行日和到期日。官方[VA2结果JSON](https://www.treasurydirect.gov/TA_WS/securities/912797VA2/10/22/2026?format=json)的`grandTotalParAwarded`、`totalParAwardedInsideOfOfferingAmount`、`somaNoncompetitiveParAwarded`与PDF一致；`somaInsideOfOfferingAmount=No`，总获配已含SOMA，不再加一次。JSON列`treasuryAuctionResultsPressReleaseFileName`；结果文件名沿实际链接／字段取得，不猜编号。Fiscal Data缺获配不代表正式结果缺失；到期核算按CUSIP补全各次发行和适用已发生调整，不能只给旧跳过整笔的汇总追加最新续发。两PDF发布日和Last-Modified均在10月19／20日，支持原截止前版本。
- **回购细表：** 官方[Quarterly Refunding目录](https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents)第四季发布段所链[PDF日程](https://home.treasury.gov/system/files/221/Tentative-Buyback-ScheduleQ42026.pdf)成功下载回读，第一页标注11月4日发布，响应Last-Modified为13:30:03Z。读取公告／操作／结算日期、操作类型、剩余期限桶、到期范围、最低／上限及前季星号；按结算窗口筛选。初步／最终CUSIP清单分别在公告／操作日11:00 ET公布，剩余期限桶不等于原始2／3／5年；计划上限不能分摊成该子样本已执行回购。此前XML超时不适用于PDF，其他XML版本时点未连带确认。
- **政策路径：** [The Market Wrap日期目录](https://market-wrap.com/daily-wrap/)实际链接的10月28日、11月3日正文及HTML含`Futures-implied Fed funds path`表，可按同目标月读12月／1月显示值，方法称ZQ期货100减价格。但具体合约、结算／末笔性质、精确采样及原截止可得版本未核；两位百分数仅支持舍入后的转载比较，未升级为本轮所需原始路径。[CME Daily Bulletin说明](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457420037)实读称日文件被次日覆盖，初步／最终版约次工作日午夜／10:00 CT更新；当前文件和方法不能补作旧日结算。
- **欧洲与基差受限归属：** 本次Webstat FM目录及旧镜像实际只返框架，原始HTML回读也未取得成员历史；搜索定位的成员月均系列仅为候选，不能换频率代码冒充日频。Investing正常历史页面浏览返回403，未绕过。三个月日元基差的新公开文章／编辑面板只有旧期单点或参考区间；法国央行[Eco Notepad 444](https://www.banque-france.fr/system/files/2026-04/Billet_444_VE.pdf)第4页图确为Bloomberg旧期基差，但不覆盖本次八周，不能替代当前序列。共同八周成员收益率及三个月基差仍未证，不把编辑核验标签、旧图、信用利差或方法当目标报价。

上述原件、字段和有限计算路径可复用；不是全历史、全部接口、私人承接或未来执行认证。新任务仍沿当期官方链接核版本与许可，公开读取不等于整库再分发授权。

### 1.9 2026-11-06资料渠道核查（目标历史仍未齐）

保留§1.7–1.8原结果；以下为获取时实读的产品／方法和公开线索，不确认旧截止可得版本、具体历史覆盖或商业权限。

- **政策历史：** [CME DataMine](https://www.cmegroup.com/datamine.html)的Settlements栏目及[Support](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457319709/CME%2BDataMine%2BSupport)已读：可按历史期间申请one-time order，经账号／许可后以API、SFTP、S3或File Browser交付；每产品含全部合约月，未承诺按四点售卖，价格依种类／期间且具体费用未知。Settlements目录本工具只返空框架。本次未得ZQZ26／ZQF27两日结算及历史版本；已有转载四值与−1／−2BP有限比较不撤销，也不能倒造结算价。
- **三个月基差：** [LSEG IDB](https://www.lseg.com/en/data-analytics/libor-transition-solutions/interdealer-broker-data)已确认JPY TONAR vs USD SOFR产品族及Workspace／feeds渠道。[Totan官方说明](https://www.tokyotanshi.co.jp/en/market_report/market_data/tona/basis.html)列USD/JPY 1W—40Y，TFPR <GO>／<TOTANICAPINDEX>入口及须签约；两侧复利SOFR／TONA、季度后付和日计数等已读。具体3个月历史、加点侧／符号、报价时点和版本未得；DBXSJP3M仍只为文章定位线索，不能与这些入口等同。
- **欧洲基准：** [LSEG Fixed Income](https://www.lseg.com/en/data-catalogue/fixed-income)已读历史与参考数据渠道，可沿Workspace的BENCHMARKS Q／历史日期、DataScope Select、Tick History或Data Library核具体系列；未确认法意德八周同类benchmark与切券。公开Borsa Italiana的Radiocor历史新闻同题为“Euro zone benchmark 10-year sovereign bond yields”，本次仅取10月8／15日三国两点及GMT观察时刻；定向采样日期搜索无结果，尚缺共同八周、完整报价／切券及版本定义。[MTS](https://www.mtsmarkets.com/)当前表列基准券及16:00 CEST Day Close，未取得历史入口，不能把其方法套给新闻或回填旧截止。
- **跨项商业候选：** [Bloomberg Data License](https://professional.bloomberg.com/products/data/data-license/)已读历史／参考字段与Per Security选择及交付方式；以上具体系列、历史版本、用户本任务权限、导出／引用权与费用仍未知。先核具体覆盖及已有授权导出条件，再提交最小字段与日期范围供决定；本次没有注册、试用、采购、付费或联系供应商，不因产品说明可读判来源通过。

### 1.10 2026-11-07政策与欧洲公开快照补证

原截止仍为2026-11-04 23:39北京时间，获取为11月7日；保留§1.7–1.9历史状态。以下新增可用事实不代表S1／S3／S5全部来源通过。

- **政策收盘转载：** [GlosEdge 10月31日美国国债简报](https://glosedge.co.jp/market_hub/2026-10-31/treasury_briefing.html)第02节“終値の日付”表实际列ZQZ26／ZQF27的10月23–30日六个日线收盘，脚注明Yahoo Finance／CBOT及10月31日取数，并披露旧11月合约修订；本次HTTP Last-Modified为11月4日13:17:05Z，早于原截止。规定首日10月28日两收盘95.965／95.890，100减报价得目标月月均EFFR表达4.035%／4.110%；收盘不自动等于CME正式结算，精确定价时钟未核。11月3日兼容两值仍缺，不能拼旧舍入隐含率或改称会议概率。沿当期表头、原来源脚注及响应版本定位，不假定未来持续覆盖。[CME Daily Bulletin](https://www.cmegroup.com/market-data/daily-bulletin.html)实际所链PG10有30D FED FD FUT，但本次current文件为11月5日FINAL，只能按首页日期采用；Yahoo普通公开历史页本次返回429，未绕过，不判整个机构不可用。
- **三个月基差路径：** [MUFG 9月9日Q&A](https://www.mufgresearch.com/rates/japan-economic-financial-weekly-special-issue-09-september-2026/)正文及HTML真实`data-download-url`已取得，所链PDF本次未读；仅定位不能称八周数据已得。[DBIQ](https://dbindex.db.com/dbiq-web/)官方HTML／脚本指明匿名检索，本次代码及名称检索均200返回空数组；只归于该公共路径，不尝试protected。BlueGamma实际历史入口需登录，未取得目标序列；不以OAS或不等价旧图替代。
- **欧洲新闻快照：** Bitget公开转载Dow Jones的“10-Yr Benchmark Govt Yields - Germany vs Other Nations”，正文`Current Spread in Yield Basis Points`同表列三国%、BP，并注明Tullett Prebon via FactSet和ET／GMT发稿时刻；从实际历史新闻链接读取、按国家名提取，发布时间与底层报价时间分开。本次九个GMT发布日9/9、16、23、30、10/7、14、21、26、11/3的27个显示收益率已取；[首篇](https://www.bitget.com/news/detail/12560605811812)、[末篇](https://www.bitgetapp.com/news/detail/12560606081539)可回溯。10/26为最后一周的实际取样，不搬到10/28；11/3 04:10GMT正文为11/2 23:10ET，不冒称11/3欧洲收盘。成员减德国乘100复算BP，原表个别利差与按三位收益率复算差0.1BP，保留显示舍入差异。动态sitemap序号不固定日期，只沿实际href核新闻正文；浏览长度受限后读取同一公开HTML取得链接有效，未猜新闻ID。11/2周日ET发稿另列、不接入图；不与Borsa日内或推算前收拼成统一序列。
- **欧洲仍缺定义：** [Parameta固定收益页](https://www.parametasolutions.com/solutions/capital-markets/fixed-income/)实读说明TP／ICAP交易台和price reporters、政府benchmark以及intraday／EOD／历史产品，但没有为上述新闻指定报价侧、实际共同定价时刻、EUR底层券或切券规则。已读TP Benchmark Methodology v5.0适用于EUR IRS，不能套给国债新闻。当前仅通过这些新闻显示值的有限计算／分析，仍未认证合格共同八周收益率序列；后续须核适用定义，无法关联时另取兼容原始报价，不能据供应商名称补齐方法。

## 2. 如何找到具体原文

从对应入口按下列动作定位，直到取得与问题匹配的当期正文、表／序列和方法／脚注，再作为证据记录；搜索摘要、机构首页、目录和 API 说明只能作为线索。以下动作不宣称所述产品或数据已取得，也不虚构统一接口。历史研究找截止时点可知的当期发布或对应版本，不能用当前最新修订值替代。

### 2.1 S01–S05：中国外溢

| 来源 | 具体定位动作 |
| --- | --- |
| S01 国家统计局 | 从“最新发布”按观察期及指标找当期 GDP、就业、物价、需求或景气发布，打开正文、核算表及附注；国家数据可辅助查序列，但遇 JS 或接口不明时记录实际结果。对 GDP 核对实际／名义、需求贡献及修订，对月度数据核当月／累计与季调；只保留解释内需、进口、出口或价格外溢的相关观察。 |
| S02 中国人民银行 | 从统计目录进入当期金融统计、社会融资规模存量／增量等具体发布及附表；政策判断另从央行官网政策公告按日期与决定名称找原文，核工具、利率、适用对象和生效时间。分开社融存量／增量、融资工具、借款人部门、所有制与贷款期限，按原表覆盖取得相关证据；仅有社融人民币贷款合计列时不视为已有居民或所有制拆分，主体判断另定位适用部门表，按研究规则§4说明代理及限制。 |
| S03 财政部 | 从国库统计按月份或年度找财政收支执行原文、表格及说明；债务或预算安排回财政部相应正式发布。核预算／执行／决算、一般公共预算／政府性基金及中央／地方，按全球需求外溢需要区分发债、拨付与实际支出。已得执行正文先读与全球需求／商品外溢有关的民生、建设等用途及同期值，再沿原表／附件补所选历史；未提取历史不能登记为当期正文未取得。 |
| S04 国家外汇管理局 | 从统计目录选国际收支平衡表、国际投资头寸、储备、外债或结售汇的对应期次，打开原表、发布及方法说明；查交易、估值及其他调整的解释，不能把头寸或储备市值变化直接当净流入。核月／季、初步／正式及修订，只采用与跨境传导有关的资料。结售汇与银行代客涉外收付款分别进入对应历史原表／下载文件及方法，定位结汇／售汇／差额、收入／付款／差额，以及本年与上年相同累计期间；核银行自身／代客、涉外收付款的人民币／外币覆盖、计价单位与修订，两组分别记录覆盖及缺基数，不相加为总资本净流入。按研究规则§4.1取得完整所选月窗及窗口外比较基数；多文件衔接先核版本与口径，累计绝对量仅在同口径可加、版本兼容时差分为当月，原表已列差额保留并与可核分项校验。具体文件与字段须实读后采用，发布期不当观察期。 |
| S05 海关／WTO | 从海关官网按期次找进出口贸易发布、总量及伙伴结构表；查询入口仅作定位，实际读不到表时保留缺口。需跨国对照时在 WTO 按经济体、伙伴、时期和指标选具体数据及定义；量／价、币种和覆盖须可比。重大贸易政策回 S15 的正式原文，不由数据目录推定政策内容。 |

### 2.2 S06–S08：美国

| 来源 | 具体定位动作 |
| --- | --- |
| S06 Fed／纽约联储 | 从 Fed 官网按会议日期找政策决定及实施说明，再按问题从数据目录找 H.4.1 当期资产负债表或 SLOOS 当期贷款标准／需求调查，读表、脚注与调查说明。回购、美元融资或市场操作从纽约联储市场栏目找具体操作公告、结果或序列；涉及缓冲时，定位央行美元互换、FIMA 回购等适用安排、资格与操作结果，分开机制、宣布和实际使用，核日／周／月／季频率及观察期。未实读的具体路径与结果保持候选，不由工具存在推定效果。SLOOS先读本期企业与居民分部门的标准、条款、需求和回顾期，再从调查历史表取相应净比例或水平；不能用标准变化替代需求，也不能因历史未齐弃用本期正文。政策隐含路径另按观察日与目标月份／会议定位CME联邦基金期货结算原表及结算／FedWatch方法，或有出处与方法的美元OIS历史；先核合约参考期和换算条件，再对齐同目标比较。纽约联储调查核首次公开时间，事件后公开只作后获知材料。 |
| S07 BEA／BLS | 从 BEA 官网当期 GDP 或 Personal Income and Outlays（收入与支出）发布进入正文、明细表及表注，核版本与修订。需求判断须定位实际消费、实际可支配收入及必要储蓄信息，按附件／工作簿目录找到相关表与序列，不止读取价格栏目；核季调、年率水平与普通月增速的区别。从 BLS 官网当期 Employment Situation（就业）或 CPI 发布读正文、表和技术说明，按需求判断取就业、工时及工资，名义工资不代实际收入。API 文档只说明方法，未调用不能称数据已取；注册与额度按权限处理。就业从Employment Situation发布及所链CES目录分别定位总非农、私人部门工资／工时的历史表、系列与方法，核覆盖、单位、季调和版本；需要时使用可回溯BLS原来源的FRED对应转载，不凭记忆拼造地址。CPI分别定位总体与剔除食品能源、季调／非季调所需系列；总体CPI API失败不能代表核心CPI或就业失败。先核附件与历史文件，最新新闻稿只有几个月不等于历史不存在。BEA年化水平、链式美元与折年增长率分别核方法。私人国内最终销售从GDP发布所链账户明细、下载表及方法定位，先实读定义及消费／私人固定投资覆盖，再采用系列名称与代码；不以GDP、GDI或相近名称代替。交互表只有框架时沿该发布官方文件或下载表继续，必要时核可回溯BEA的FRED对应转载；按研究规则§4.1取得八季及适用同比／环比基数，核实际／名义、季调、参考年、年化性质与修订。官方折年率转换普通季环比时，先核发布方定义，再用`((1 + SAAR / 100) ** (1 / 4) - 1) × 100%`并记录输入；不以年化水平之差当增长率。只有官方增速时保留增速序列及适用百分点比较，明确未得水平／基数，不倒推精确原值或称目标已全齐。发布预期按事件和发布日期定位Reuters等调查正文或可回溯转载，核首次公开、调查时点、样本／中位数与预测定义，再对照BLS对应CPI归档初值；先核时点再选事件，单机构预测不称共识。 |
| S08 美国财政部 | 利率目录按日期和所需名义／实际类型进入具体日利率表，核期限、价格性质与方法；TIC先区分新闻稿、历史证券交易／银行流量、持仓表，再沿对应历史文件或归档补缺月；核净流入、长期、短期、银行分项的定义、正负和修订，不把滚动12月总额拆成单月，分别使用交易／头寸及估值说明，保留滞后。财政和拍卖仍按实际采用命题选来源，不把全部融资项目变为默认必取。Fiscal Data按命题从Monthly Treasury Statement（月度财政报告）目录定位净利息支出与总收入的对应表／行，核净额、联邦预算范围、财政年或累计期间及上年同期，再计算负担；净发行另核相应期限发行与到期，不能用总发行替代。拍卖承接沿TreasuryDirect正式拍卖公告／结果及历史查询定位同原始期限、名义／通胀保值、续发／新发等可比结果，读发行额、投标／获配口径、倍数与适用分类；无对应发行前报价不计算尾差。目录可读且有工具时须读取与主要命题相关的具体表，不能停在目录或先建议采购。未实读的具体项目／报价保持候选；平均债务成本不以当前市场收益率代替，间接投标人不直接等于外国投资者。目录可达不等于具体表已取，旧失败不预判本次结果。期限净供给从Quarterly Refunding、Fiscal Data／Monthly Statement of the Public Debt所链发行与偿还原表及TreasuryDirect定位工具、原始期限、面值、实际结算、到期和调整；余额只作核对。沿正式公告另找未来一季已公布发行／到期与计划，未定部分保留未知。 |

### 2.3 S09–S11：欧元区、英国、日本

| 来源 | 具体定位动作 |
| --- | --- |
| S09 ECB／Eurostat | 从 ECB 官网按会议日期找政策决定与实施说明，信贷问题另找当期 Bank Lending Survey（银行贷款调查）正文、表与方法。由 Eurostat 官网按期次找 GDP 或 HICP 发布，再按数据集、地区、单位、季调和时期定位具体数据及元数据；区分欧元区／欧盟／成员国。ECB Portal 受限须记录本次结果；Eurostat 最新序列不提供完整历史版本，历史判断另找当期发布。成员融资按同版Eurostat一般政府季度收支／赤字及适用债务定位最新完整八季与窗口外比较基数，核地理、单位、季调和修订，另有累计则找上年完全同期。收益率沿ECB或Bundesbank定位法／意／德同币种、约10年、同日和同报价性质的表及方法。法国AFT、意大利MEF沿正式拍卖结果与发行制度找各两次自身可比名义固息拍卖／续发，读取原始／剩余期限、投标／获配和收益率；目录不代数值。 |
| S10 BOE／ONS | 从 BOE 官网按日期找货币政策决定与报告，需曲线时进入曲线页，先读拟合方法及修订说明，再选币种、名义／实际类型、期限和日期对应文件。宏观统计从 ONS 按 GDP、就业工资或物价等主题找当期发布、表及方法；ONS API Beta 的覆盖按实际取得核实，不能由数据库或文件入口推定曲线 API 可用。 |
| S11 BOJ及日本当地原发布方 | 政策从 BOJ 官网按会议日期找决定原文；货币信用、利率汇率等由时序库按统计名称、序列、单位、频率和时期选表并读说明。JSON／CSV 公告不等于已调用。就业、工资或其他指标若需转日本当地统计机构，先记录实际原发布方、具体发布／表及候选状态，实际取得后再更新；不能把 BOJ 目录当作全部日本数据。干预等事件回实际发布机构的原文，原发布方未定时保持候选。家庭消费与工薪家庭收入沿统计局／e-Stat家庭调查发布及所链历史表定位，工资沿厚生劳动省对应工资调查；先采用相关当期正文，再查历史与样本定义，二者不能互代。具体路径未读保持候选，不从BOJ目录推定已取。跨币种融资沿BOJ金融系统／市场报告的表、附件、下载及方法寻找三个月日元／美元基差原值与出处，再回溯原发布方；核两侧参考利率、加点侧、符号和覆盖。只有旧图形时记录提取精度及旧覆盖，不按图标题称当前连续数值已取。 |

### 2.4 S12–S13：跨国信用与比较

| 来源 | 具体定位动作 |
| --- | --- |
| S12 BIS | 在门户先选全球流动性、国际银行、部门信用、偿债负担或有效汇率等与问题匹配的数据集，读定义后按币种、借款／报告部门、报告地／对手方所在地及期限等可用维度选序列；按具体数据集核所在地或合并口径。保存序列与元数据定位，不能由 SDMX／批量下载说明称已取数据，也不能由借款总量推定套利交易。三个月日元／美元基差可沿BIS相关报告的表、下载、方法与原来源定位；滞后负债存量只说明原期暴露，不替代基差价格。具体字段、时效与可读范围均待实读。 |
| S13 IMF／OECD／World Bank | 从相应官方入口选与增长、财政、外部脆弱性、结构或预测问题匹配的数据集及发布版次，再选经济体、指标、时期、单位和定义；逐项标记实绩、估计或预测，核修订及可得历史版本。新兴市场覆盖逐项确认，比较时保留本地与跨国统一定义的差异；世界总量不能代替目标地区数据，接口说明不能代替具体观察。 |

### 2.5 S14–S16：宏观供给冲击与正式政策

| 来源 | 具体定位动作 |
| --- | --- |
| S14 EIA | 从 Open Data 按所需能源基准价格或总量供应资料选具体序列／报告，读单位、频率、地区与定义，用于宏观通胀、进口成本、贸易条件或供给冲击。API 注册要求按授权处理，未实测仍记未实测；不延伸为电厂、炼厂、细分成本或品种供需研究。 |
| S15 政策原发布机构 | 先按国家、政策名称和发布日期找政府、财政、贸易或金融主管机关的正式公告，再读全文、生效附件和后续修订，核适用地区、对象、税率／措施、生效及终止日期。中国政策库、WTO 或 OFAC 仅作相关定位入口；重大贸易政策以实际制定机关原文及生效附件为依据，不以新闻标题或目录代替，不扩展为实体合规筛查。 |
| S16 纽约联储 GSCPI | 在 GSCPI 数据页找具体序列的下载或展示，记录观察期和版本；同时进入编制说明，核构成、标准化及解释边界。框架可读与编制说明仅定位、序列未取的旧状态分别保留，只有取得实际值后才作观察；综合供应链压力不能扩展成航运或其他行业研究。 |

### 2.6 S17–S18：跨资产参照与中国补充

| 来源 | 具体定位动作 |
| --- | --- |
| S17 目标市场原发布方 | 先按资产与经济体选择宽基股票指数、主权名义／实际曲线、整体信用利差、代表性商品价格或货币对的原发布产品；逐项核价格定义、币种、报价方向、观察／收盘时点、覆盖时期、方法及许可。中国货币网或中债按实际产品查原始发布，LPR 定位不代表已取全部利率；CFTC 按本次必要的总量持仓参照找对应报告及覆盖，正文已读不代表全部序列已取。宽基股票、全球信用利差等产品未定或具体序列未取时记候选，不虚构接口、产品覆盖或完整全球行情；不研究具体合约和持仓收益。宽基定价按发布日期与完整盈利目标年度找S&P DJI／FactSet公开标普500指数盈利报告的当前及前版原表与方法，读取相应价格、PE、总量EPS／盈利、指数覆盖及版本；滚动NTM不冒充固定年度修订。信用按ICE BofA指数方法及FRED可回溯转载定位美元IG与HY OAS原表／下载，逐项核评级、覆盖、基准、期权调整、单位和共同窗口。日元／美元基差另找有出处的原发布方数值与方法，不能用普通远期点替代。 |
| S18 中国监管／地区财政及披露平台 | 银行体系总量按问题从金融监管总局找具体统计表及说明；地方财政、债务和城投披露仅在有可定位证据表明显著影响全球需求、跨境融资或风险偏好时，按研究地区和发布主体找财政执行、地方债资料或公开披露，记录原发布方、覆盖及汇总方法。来源未定保持候选；区分城投债、平台全部融资、地方政府债、隐性债务及流量／存量，不能相加或推定政府全偿，不制造可比城投融资或隐性债务精确全量，不进入单券尽调。 |

### 2.7 比特币与美元稳定币：候选定位

以下为定位动作；具体已读取范围见§1核验，未列实测的路径仍为候选／未实测。旧S01–S18状态保持，不能连带升级。

| 对象 | 原发布方与具体动作 | 边界 |
| --- | --- | --- |
| 比特币价格／交易 | 先按问题选CME CF等基准编制方或可核交易场所的价格产品、方法及历史报价／日线／基准文件，核币种、场所、时区、收盘或成交定义、实际首末观察及覆盖。近期成交只覆盖短窗时，沿该发布方历史产品或适用公开替代找所需窗口；读取真实返回，不能以请求起日或笔数推定覆盖。 | 不同场所、基准、日收盘与成交不拼接；替代保留自身定义与许可。未取得历史、动态／接口能力仍记未证，旧两点核验不升级为完整历史。 |
| BTC基金与衍生品 | 所选ETF发行方或同基金SEC披露查报告日、份额、每份净值、资产／持币与申赎机制，核拆分／类别；按需找CME、CFTC或交易场所衍生品原统计及方法。 | 不假设公开提供全部即时净流量；存量不当交易，单场所仓位／清算不代表全市场，不做基金选择或具体合约分析。 |
| 美元稳定币 | Tether、Circle等所选发行方按报告期找储备披露、鉴证原件、流通定义及正式赎回条款；政策回实际监管原文，链上资料回可追溯账本或有方法的数据方。 | 核现金、直接短债、基金与逆回购的期限／重叠，区分发行方披露、鉴证与财务报表审计，按原文核对保证程度、对象和覆盖期间；核赎回资格、处理限制、多链与桥接口径。地址不推用户地区，链上总额不代支付用途，报告日储备不代实时。 |

黄金沿S17定位基准编制／管理方或可核市场原发布的价格与方法，例如LBMA黄金基准所链发布渠道；先核实际可读产品、计价币种、金衡盎司单位、上午／下午基准或其他报价定义、观察时点、历史覆盖及许可，未实读保持候选。价格用于市场表现，并与同期间美元及实际利率对照；价格序列公开受限时查适用且能回溯原来源的公开转载，不能用储备单位估值代替。储备配置另沿S04／S13查央行／IMF数量、估值与调整说明，基金或需求命题再核原披露／世界黄金协会方法；官方、行业汇总与估计分别标注，市值不冒充数量或交易。

美债沿S06／S08找对应期限净供给、拍卖／持有人交易、回购参考利率及实际操作结果；分国库券与中长期债，尾差需发行前报价，间接投标非境外投资者，抵押持仓非已出售。汇率沿当地央行、S12 BIS有效汇率与篮子说明、S13国际收支／外债，按命题查对冲／基差及干预原文；保留名义／实际、报价、覆盖和滞后。未读取的具体数值／接口仍候选，旧方法已读不升级为数值已取。

以上新增路径均为候选定位动作，未实读不更新§1历史核验状态。具体公开路径失败且无新线索时停止重复探测；若影响核心命题，说明缺少的字段、市场／期限、窗口／时效、已试路径及替代差异，并提出LSEG／Bloomberg／FactSet等对应商业候选的预期改善、仍未知事项和未知费用；请求不等于采购授权，也不关闭来源缺口。必要商业资料按下节审批规则处理。

## 3. 本次取得、许可与后续更新

本次每条证据分别记录“已取得正文／数据、仅定位、候选、读取失败、许可待核”，并说明取得的是目录、方法、政策正文、具体表／序列还是 API 实际调用结果。取得与采用分开；已取得也须核用途、口径、时点和许可。公开、可下载、允许使用、已接入分别记录，页面可读不等于可无限抓取、再分发或已完成接入。商业或其他非公开资料仅在明确授权范围内使用；超出公开或已有授权的接入，先报告需要什么及限制，不自行采购、注册试用、付费调用或联系供应商，也不绕过访问控制。

只有后续真实任务实际取得相应材料后，才可据直接依据更新本导航对应条目的核验记录，并保留 2026-10-18 既有记录及其时点；未核验条目保持原状态，不要求先访问每个网站或认证全部 API。本次研究的原发布方、具体链接与表／页／段／序列、取得内容与状态、获取时间、直接依据及未知项，按报告约定记入 `evidence.md`，与完整 `report.md` 一起交付；不增加其他必交付文件或运行依赖。

宏观来源按具体经济体选择，不用美国、中国或世界总量代填其他地区缺项。替代来源核对原发布方、定义、时期和许可，同源转载仍算一个底层来源。历史版本、事前市场预期或全球资金仓位不足时，记录尝试与最小缺口，限制直接受影响判断；目录更新或日期变化不取消已对原时点成立的旧结果。

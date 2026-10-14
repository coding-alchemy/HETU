<!-- fixture:QS01-TASK -->
这是离线局部报告呈现任务，请求深度 quick。沿用材料中主体、时点和资料使用条件，根据当前运行规则及允许读取的事实 owner 和产物，在指定输出目录编写九章报告候选及本次呈现核对记录。仅评估本次呈现，不重新取数、解析或执行完整研究，不修改输入。允许输入以 material.md 列出的文件为准；输出位置由协调者单独给出。

<!-- fixture:QS01-MATERIAL -->
真实主体：恒瑞医药600276.SH／江苏恒瑞医药股份有限公司。原时点：2026-10-11T18:00:00+08:00，public。允许只读复用；来源根为 `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800`，来源任务 `恒瑞医药-600276.SH-quick-20261011T195429+0800`。旧路径仅用于回源，不赋予目录遍历权限。

允许文件逐项如下。Markdown owner/evidence只读指定行；原状态、自检、研究判断、评分、回归评分和修复答案不开放、不复制为本次状态。manifest只用于采用与血缘定位，不能整套照搬。所有未列文件均不在允许集合；明确排除旧report.md、W10、lock、delivery、复评与回归产物。

BS/CF/分部/股东主输入使用schema-v2；利润表保留attempt2的全部14期；行情使用r2。metrics-result为完整计算结果，metrics为工具信封，chart-envelope为图表信封，SVG为可打开图表。PB的schema-v1仅保留原adopted派生结果必要血缘，不作本次完整三表主采用。

| 允许文件（绝对路径） | 原状态／用途与读取边界 |
| --- | --- |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W7-valuation-expectations/dividend-yield--calc-v1.json` | adopted；W7 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W7-valuation-expectations/market-cap--close4685-20261009--calc-v1.json` | adopted；W7 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W7-valuation-expectations/pb--calc-v1.json` | adopted；W7 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W7-valuation-expectations/pe-ttm--calc-v1.json` | adopted；W7 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W8-market-signals/market-chart--600276-r2.svg` | adopted；W8 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W8-market-signals/market-chart-envelope--600276-r2.json` | adopted；W8 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W8-market-signals/ohlcv-input--600276-qfq-r2.json` | adopted；W8 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W8-market-signals/ohlcv-metrics--600276-r2.json` | adopted；W8 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/derived/W8-market-signals/ohlcv-metrics-result--600276-r2.json` | adopted；W8 derived |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W2-incremental-events/adapter-verdict--cninfo-announcement-index--20251011-20261011--schema-v1.json` | adopted；W2 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W2-incremental-events/announcement-index--cninfo-announcement-index--20251011-20261011--schema-v1.json` | adopted；W2 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W4-business-governance/segments--annuals--schema-v2.json` | adopted；W4 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W4-business-governance/top-shareholders--history--schema-v2.json` | adopted；W4 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W5-financial-validation/adapter-verdict--sina-profit--schema-v1.json` | adopted；W5 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W5-financial-validation/balance-sheet--annuals--schema-v1.json` | superseded；历史PB计算血缘，只读核对，不作完整三表主采用 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W5-financial-validation/balance-sheet--annuals--schema-v2.json` | adopted；W5 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/normalized/W5-financial-validation/cash-flow--annuals--schema-v2.json` | adopted；W5 normalized |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/announcement-index--cninfo-announcement-index--20240301-20240531--attempt1.json` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/announcement-index--cninfo-announcement-index--20250301-20250531--attempt1.json` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/announcement-index--cninfo-announcement-index--20251011-20261011--attempt1.json` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/annual-2023-report--cninfo-announcement-index--20261011T213000+0800--4fe60113.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/annual-2024-report--cninfo-announcement-index--20261011T213000+0800--7fad40fe.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/annual-2025-report--cninfo-announcement-index--20261011T213000+0800--fae80e2d.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/buyback-result-2026--cninfo-announcement-index--20261011T213000+0800--6469c408.pdf` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/dividend-implementation-2025--cninfo-announcement-index--20261011T213000+0800--00c2c29a.pdf` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/h1-2026-report--cninfo-announcement-index--20261011T213000+0800--ff691066.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/h1-2026-report-summary--cninfo-announcement-index--20261011T213000+0800--1c2e4085.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/cninfo-announcement-index/q3-2025-report--cninfo-announcement-index--20261011T213000+0800--15136c6b.pdf` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/financials-600276-series-20231231-20260630.json` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/info-600276-20261011.json` | adopted；W1 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-qfq-20250701-20250930.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-qfq-20251001-20251231.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-qfq-20260101-20260331.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-qfq-20260401-20260630.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-qfq-20260701-20261011.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-raw-20260520-20260605.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/perf-600276-raw-20260928-20261011.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/shares-600276-20261009.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/hexin/summary-600276-20261009.json` | adopted；W8 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/annual-2023-report-text--pdf-text-extract--20261011T213000+0800--b7e20725.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/annual-2024-report-text--pdf-text-extract--20261011T213000+0800--bc57c324.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/annual-2025-report-text--pdf-text-extract--20261011T213000+0800--e63ceb50.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/buyback-result-2026-text--pdf-text-extract--20261011T213000+0800--28d28eda.txt` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/dividend-implementation-2025-text--pdf-text-extract--20261011T213000+0800--15a5c4c3.txt` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/h1-2026-report-summary-text--pdf-text-extract--20261011T213000+0800--c8258d05.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/h1-2026-report-text--pdf-text-extract--20261011T213000+0800--c611ce4d.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/pdf-text-extract/q3-2025-report-text--pdf-text-extract--20261011T213000+0800--10b8a2fb.txt` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/sina-financial-statements/statements-profit--sina-financial-statements--20231231-20260630--attempt2.json` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/wind-docs/announcements-shareholders-events-600276-20251001-20261011.json` | adopted；W2 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/wind-docs/segments-600276-annual-reports-2022-2025.json` | adopted；W4 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/wind/events-dividends-600276-20251001-20261011.json` | adopted；W7 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/raw/wind/fundamentals-600276-5periods.json` | adopted；W5 raw |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/scripts/W2-incremental-events/pdf-download--W2-incremental-events--20261011T213000+0800--e23389b1.py` | adopted；W2 script |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/scripts/W4-business-governance/extract-shareholders-segments--W4-business-governance--20261011T223500+0800--0d9b6ba0.py` | adopted；W4 script |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/artifacts/scripts/W5-financial-validation/extract-statements--W5-financial-validation--20261011T223500+0800--1768184c.py` | adopted；W5 script |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/manifest.json` | metadata_only；仅读取run及本清单所列artifact条目／inputs；其他条目只为排除定位，不开放其产物。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/evidence.md` | metadata_only；仅行3–11、16–21、23–24、26–28、34–35、48–50（来源、E1–E6、E8–E9、E11–E12、冲突及E16–E18事实/计算定位）；不读其余行，尤其E19、修订摘要及旧报告/复评说明。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W0-task-framing.md` | metadata_only；仅行3–8、10–12（原任务身份、时点、范围）；不读旧完成状态、目标验收、回归、自检或修订段。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W1-subject-verification.md` | metadata_only；仅行3–8、25–43、45–47（事实、股本计算与原缺口定位）；不继承旧完成状态。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W2-incremental-events.md` | metadata_only；仅完整行3–8、27–33、36–40、46–47；行48仅允许“其余索引条目附件可按 URL 定位未逐份取全文”这一事实片段。行34–35不开放，分红与回购全部事实直接读取本清单已允许的权益分派／回购结果PDF及全文文本、wind事件与normalized公告索引（窗口2025-10-11..2026-10-11）；保留完整事件日期、金额、股数、用途、实施条件和窗口前减持日期，按原文和窗口边界定位。不读其余片段、自检与修订记录。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W4-business-governance.md` | metadata_only；仅完整行3–7、29、45、47–54；行24–25只允许第4列“证据或查询范围”（治理公告事实／审计范围、2024起完整产品分类变化）。不允许这两行其他列，尤其旧状态与覆盖判定；完整分类／股东明细仍直接读取已允许的schema-v2及年报原件。不读模型研究结论、回归、修复、自检或修订记录。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W5-financial-validation.md` | metadata_only；仅完整行3–7、16、28–40；行24–25只允许第4列“证据或查询范围”（TTM三期公式及数值、hexin指标序列），不允许其他列，尤其旧状态与覆盖判定。完整财务科目、全部期间与输入仍由本清单原件／schema-v2开放。不读回归、修复、自检或修订记录。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W7-valuation-expectations.md` | metadata_only；仅完整行3–7、28–36、44–46、49–50；行51仅允许“快照 PB 口径冲突保留”这一事实片段，双方原值直接读取已允许的pb计算产物与hexin快照。其余行51内容不开放；不读完成状态、自检、旧报告。 |
| `/Users/nanzhang/trading/HETU/.hetu/research/恒瑞医药-600276.SH-quick-20261011T195429+0800/work-packages/W8-market-signals.md` | metadata_only；仅完整行3–7、16、29–35、39–41、55–59；行22–23、26只允许第4列“证据或查询范围”（7原件／309根及窗口、复权与公司行动互证、节假日／停牌／gaps），不允许其他列，尤其旧状态与覆盖判定。完整行情／指标／SVG仍直接读取已允许的r2产物与原件。不读旧完成状态、回归/复评、修订。 |

正式身份回源：2025年报文本行148附近的股票上市交易所／简称／代码，行6643–6653的统一社会信用代码、A股及H股沿革；正式PDF与该文本均在清单。Q06/Q07原文回源为top-shareholders v2的source标记、三年年报／2025Q3／2026H1文本，已采用回购结果及权益分派正文／PDF；其余公告只有索引，不冒称全文已取得。

本次复制按canonical artifact-contract.md：新run.reuse_previous_task_data=true，每条副本provenance保留source_task_id、source_artifact、original_source、original_acquired_at、copied_at；新manifest.inputs只引用本次运行内的本地副本，不直接指向旧根。原脚本仅为方法定位，不重新执行旧取得／抽取／计算。

新增取证按正常规则核原时点的公开、版本、生效与适用期间；取得时间可以晚于截止，但不能采用截止后才公开内容。以下是待取证用途而非已取得声明：经营／财务解释及必要附注、关键行业／独立反方和竞对正式资料、治理及产品监管批准原文、适用资本动作影响、驱动依据与基础估值参照。参考源入口为canonical source-contracts.md、material-business/company/risk/market.md；不预制情景、估值或应得结论。

<!-- fixture:QS02-TASK -->
这是离线合成局部工作包任务，请求深度 quick。按当前运行规则处理材料所支持的财务指标、业务构成和市场比率，提交 W4/W5/W7 的相关片段、计算及资料状态，并写供用户阅读的对应报告片段。评估范围仅为本次指定用途，不执行完整股票研究；只读取本次派发文件和正常规则，输出位置由协调者给出。

<!-- fixture:QS02-COMMON -->
合成券商青禾证券，非真实上市公司；资料仅用于局部方法判断。截至 2026-06-30T12:00:00+08:00，单位百万元，股本百万股，价格元。

合成《青禾证券2025年报》，发布日2026-03-28，页标题：经营与分部附注。
经纪收入800，直接业务成本500；自营收入200，直接业务成本150；总部共用费用370。未分配共用费用，不披露分部净利润。附注目录为“业务收入、直接成本、共用费用、分部披露范围”，相邻段分别载上述三项及范围：合成披露范围内没有其他分部利润表。此处保留该用途相关附注和上下文，非完整三表。
页标题：2025年度财务输入。2025年收入1,000、归母净利润-20、经营现金流-200。
合成《青禾证券2026Q1报告》，发布日2026-04-28，页标题：财务输入。2026Q1收入240、归母净利润-5、经营现金流-50；2025Q1收入230。
页标题：经营模型。经纪与自营证券业务，适用净资本／风险覆盖等监管指标；未经营工业生产或商品存货。新财富管理子业务收入由2024年0增至2025年10，包含在经纪收入中。
合成行情与股本片段，发布日2026-06-30 11:30+08:00，页标题：市场时点。2026-06-30收盘价10、总股本100；本期尚未实施现金分红。行情窗口不在本局部范围，不由这两个数证明Q02完整。

<!-- fixture:QS02-BASE -->
2025Q1 归母净利润：-15
合成《青禾证券2026Q1财务简表》，发布日2026-04-28，页标题：合并资产负债表。单位百万元：总资产10,000、负债9,900、归母权益100。
合成《青禾证券2026Q1监管报告》，发布日2026-04-28，页标题：资本指标。单位百万元：净资本80、风险资本准备40。

<!-- fixture:QS02-MISSING -->
2025Q1 归母净利润：本次未取得
本次只有青禾证券最新资产负债附件 latest-balance.txt，owner尚未抽取。净资本／风险资本准备查询的模拟记录见 missing-response.json，本次未提供其值。附件及模拟记录仅为本选择用途，模拟失败不是实际联网。


<!-- fixture:QS02-MARKET-PIT -->
合成《青禾证券收盘行情及股本参考》，发布2026-06-29T15:01:00+08:00；行情日2026-06-29，收盘价10元，总股本100百万股。此来源仅作为截至2026-06-30T12:00:00+08:00前最近已知收盘参考，不证明2026-06-30当日盘中／未来收盘或完整Q02。资料仅用于本次局部方法判断，非真实行情；原2026-06-30行情片段及其发布时间原样保留供核对适用时点。

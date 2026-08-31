# 确定性工具目录

## 当前状态

六个具名工具均为 `adopted`，随 canonical Skill 的 `scripts/` 目录分发，可按本目录的接口与边界调用。前五个 JSON 工具沿用阶段 03 的 RED→GREEN、取证哈希锁定、离线字节重放、拒绝边界和全量门禁；`pdf_text_extract.py` 以 v1.4 的稳定文本、页面失败和拒绝边界测试放行。共享信封模块 `_artifact_io.py` 不是独立工具，只为五个 JSON 工具提供确定性的读取、SHA-256 和规范 JSON 写入。

除六个具名分析工具、封闭采集脚本 `source_fetch.py`、阶段 04 机械助手 `source_adapter.py`、阶段 05 机械检查器 `check-run-artifacts.py` 与共享信封模块 `_artifact_io.py` 外，`scripts/` 内不得存在任何其他文件。只有 `source_fetch.py` 可按下述封闭接口联网；历史聚合脚本、一次性补采或最终报告拼接脚本仍禁止接入和联网。封闭文件集由 `tests/product/skill/test_deterministic_tool_io.py` 的严格相等断言保证。

## 共享信封与退出码

五个 JSON 工具与阶段 04 机械助手 `source_adapter.py` 统一通过 `_artifact_io.run_transform` 写确定性 JSON 信封（排序键、紧凑分隔符、UTF-8、结尾换行）；PDF 文本工具使用下文单独定义的稳定文本格式：

- 成功信封：`schema_version`（`"1.0"`）、`tool`、`input_sha256`（输入文件字节 SHA-256）、`status`（`"success"`）、`source`（输入中 `source` 块的原样透传，无则 `null`）、`source_provided`（布尔）、`result`。
- 失败信封：`schema_version`、`tool`、`input_sha256`、`status`（`"failed"`）、`error_type`、`error_message`、`source`、`source_provided`；不含 `result`，不含执行时间等非确定字段。
- 退出码：`0` 成功；`1` 解析或转换失败（失败信封落盘后退出）；`2` 参数非法、输入文件缺失或不可读、输出路径与输入相同、输出文件已存在或被并发创建（已存在的输出文件保持原样，拒绝覆盖，不产生部分产物）。信封写入采用独占创建（`O_EXCL`），即使输出文件在友好预检之后、写入之前被并发创建，也绝不覆盖任何既有文件。
- 相同保存输入重复运行产生字节相同的信封；输入变化必然改变 `input_sha256`。信封只由显式文件输入决定，可完全离线重放。

## 通用禁止边界

除 `source_fetch.py` 外，全部工具统一：**不选择来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号**；不联网、不注册新的 `hetu-stock` CLI 命令、不固定证券/日期/绝对路径、不读写研究目录外的位置、不改写任何来源原值。来源选择、证据采用、冲突裁决、检索窗口与业务解释始终由 Agent 负责；脚本不可用只形成对应局部缺口，不得让整个 public 研究无条件失败。

## 封闭来源采集

### source_fetch.py

- 文件路径：`skills/hetu-stock-analysis/scripts/source_fetch.py`
- 用途：按 Agent 显式选择，采集一只证券的一个封闭公开来源并保存 adapter-ready 响应。
- CLI：`python source_fetch.py --input REQUEST.json --output SAVED_RESPONSE.json`。
- 来源闭集：`cninfo-announcement-index`、`sina-financial-statements`、`tencent-quote-snapshot`。
- 成功：退出 0；保存 `source_metadata`、`response.status_code` 与顶层 `body`。
- 来源或请求失败：退出 1并写稳定失败信封；输入不可读、JSON 非对象、CLI 或输出冲突：退出 2。
- 边界：只访问固定 HTTPS allowlist；同 allowlist 重定向的每个候选 URL 在发出下一请求前重新校验，
  越界重定向失败关闭。巨潮/新浪只接受规范化 `application/json`，腾讯只接受真实响应头诊断确认的
  规范化 `text/html`，均允许参数但不接受其他 media type。不自动重试、换源、补采、采用证据或生成
  结论；不自动换源。
- 共同输入 Schema：JSON 对象的顶层键严格为 `schema_version`（`"1"`）、`source_id`、`subject`（大写 `NNNNNN.SZ|SH|BJ`）、带时区且不晚于当前时点的 `as_of`、与来源匹配的 `purpose` 和 `request`；不接受额外键、任意 URL、请求头、Cookie、token、代理或可执行输入。
- 三种 `request`：巨潮为 `{"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD"}`（北京时间日期、结束日不晚于 `as_of`）；新浪为 `{"statement":"profit|balance|cash","period_count":1..20}`；腾讯为 `{}`。
- 成功 `body`：巨潮保存已连续取得并闭合的 `pages`；新浪保存 `format="sina_report_list"` 与原始 `report_list` 容器；腾讯保存单证券 GBK 原始文本、机械提取的证券/时点/价格/市值字段及原位字段标签。每份成功输出同时回显请求、证券和来源元数据，供 `source_adapter.py` 重新判断闭合和适用性。
- 失败状态：来源侧 403、429、其他非 2xx、传输或结构错误分别以稳定的 `permission_denied`、`rate_limited`、`transport_error` 或 `parse_error` 失败信封保存；脚本不把空结果或来源成功解释为证据采用。
- 文件发布：同目录临时文件完整写入、flush、`fsync` 后原子且不覆盖地发布；写入失败、发布竞态或
  既有目标均不留下截断目标或临时残留。
- 研究目录：Agent 在调用前创建或选择当前研究根，且只能在 W1 唯一核验后使用
  `.hetu/research/<证券简称>-<证券代码>-<请求深度>-<任务时间>/` 作为当前研究根；
  `--input` 和 `--output` 必须位于同一当前研究根，输出只允许位于其 `artifacts/raw/` 下。
  `source_fetch.py` 不接受研究根参数或任意写入授权；目录选择和边界确认由 Agent 负责，脚本只执行
  固定 CLI 的同路径、覆盖和竞态拒绝。
- 实际测试文件：`tests/product/skill/test_source_fetch.py`、`tests/product/skill/test_source_adapter.py`、`tests/product/skill/test_deterministic_tool_io.py`。

## 已批准工具

### financial_statements.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/financial_statements.py`
- 用途：把已保存的新浪财务报表响应标准化为按报告期新到旧排序的行，保留来源原值字符串与报告期。
- 输入 Schema：JSON 对象 `{"result": {"data": {"report_list": {"<YYYYMMDD>": {"data": [{"item_title": 字符串, "item_value": 字符串, "item_tongbi"?: 字符串}]}}}}}`；可选 `source` 块原样透传。CLI 参数：`--input`、`--output`、`--limit`（默认 9，正整数）。
- 输出 Schema：`result = {"limit": N, "rows": [{"报告期": "YYYY-MM-DD", "<标题>": 原值字符串, "<标题>_同比": 原值字符串?}]}`，取新到旧前 N 期。
- 失败异常：结构缺失、`report_list` 非对象、报告期非严格 8 位 `YYYYMMDD` 数字串（`202681` 之类的非补齐形式拒绝）、条目缺标题/值非字符串、重复标题、`limit` 非正整数时抛 `ValueError`（退出 1，失败信封）；参数/输入缺失/输出冲突退出 2。东财等 list 形 `report_list` payload 正式拒绝，不产出任何指标。
- 是否可离线重放：是（只读显式保存文件，无网络、无时钟依赖）。
- 禁止边界：不猜字段语义、同比或调整前后口径；不选择来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号。
- 实际测试文件：`tests/product/skill/test_financial_statements_tool.py`、`tests/product/skill/test_tool_cli_contract.py`、`tests/product/skill/test_deterministic_tool_io.py`。

### announcement_index.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/announcement_index.py`
- 用途：把已保存的巨潮公告分页响应合并、跨页去重并按发布时间降序建索引，只保留不晚于时区化 `as_of` 的公告。
- 输入 Schema：JSON 对象 `{"pages": [{"pageNum": 整数, "totalAnnouncement": 非负整数, "announcements": [{"announcementTitle": 字符串, "announcementTime": 毫秒整数, "adjunctUrl": 相对路径字符串, "announcementTypeName"?: 字符串列表或 null}]}], "as_of": "带时区 ISO-8601"}`。CLI 参数：`--input`、`--output`。
- 输出 Schema：`result = {"total_count", "returned_count", "usable_count", "page_complete", "announcements": [{"title", "published_at", "url", "category"}]}`；附件 URL 只拼接到 `https://static.cninfo.com.cn/`；总数不一致、页码缺失/重复/不连续、返回数与总数不一致（多于或少于）时 `page_complete=false`，不得据此写"无公告"。
- 失败异常：`as_of` 无时区或日期型、payload 非对象、附件 URL 非相对路径（任意 scheme 前缀如 `http`/`https`/`ftp`/`javascript`/`data`、以 `/` 或 `\\` 开头、含反斜杠、原始或百分号解码后含 `..` 父目录段（含 `%2e%2e`、`..%2F` 与双重编码）、空值）或非法字段类型时抛 `ValueError`（退出 1）；参数/输入缺失或不可读（含二次读取失败）/输出冲突（含并发创建）退出 2，且均不产生部分产物。
- 是否可离线重放：是。
- 禁止边界：不决定检索窗口和证据采用；不选择来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号。
- 实际测试文件：`tests/product/skill/test_announcement_index_tool.py`、`tests/product/skill/test_tool_cli_contract.py`、`tests/product/skill/test_deterministic_tool_io.py`。

### numeric_consistency.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/numeric_consistency.py`
- 用途：对显式给出的数值执行确定性 Decimal 计算，暴露冲突主张的双方值、差异和容差结果。
- 输入 Schema：CLI `--operation OP --input IN --output OUT`，输入为该操作的显式参数 JSON：`gross_margin_percent`(revenue, revenue_unit, cost, cost_unit)、`debt_ratio_percent`(liabilities, liabilities_unit, assets, assets_unit)、`market_cap_values`(price, price_unit, total_shares, total_shares_unit, float_shares, float_shares_unit)、`price_earnings_ratio`(market_cap, market_cap_unit, attributable_profit, attributable_profit_unit)、`price_to_book_ratio`(market_cap, market_cap_unit, attributable_equity, attributable_equity_unit)、`dividend_yield_percent`(dividend_per_share, dividend_per_share_unit, price, price_unit)、`compare_metric`(metric, unit, claimed, recomputed, tolerance)——每个数值参数都必须携带显式单位（非空字符串）；参与同一次相减或相除比较的输入单位必须一致，单位不一致即拒绝且不做任何换算；可选 `source` 块。缺参数（含缺任一单位）或出现未声明参数即拒绝。
- 输出 Schema：算术操作输出 `value`（或 `total_market_cap` 与 `float_market_cap`）加上 `input_units`（逐输入单位回显）与 `result_unit`（`%`、复合单位或 `ratio`）；`compare_metric` 输出 `metric`、`unit`、`claimed`、`recomputed`、`tolerance`、`consistent`、`absolute_difference`。全部数值为 Decimal 字符串，不是 float。
- 失败异常：非有限数、零分母、负容差、非法参数名、单位缺失或同次比较单位不一致抛 `ValueError`（退出 1）；未知操作名或参数错误退出 2。
- 是否可离线重放：是。
- 禁止边界：保留双方原值和差异，不自动交换标签、不自动改写来源值；不选择来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号。
- 实际测试文件：`tests/product/skill/test_numeric_consistency_tool.py`、`tests/product/skill/test_tool_cli_contract.py`、`tests/product/skill/test_deterministic_tool_io.py`。

### pdf_text_extract.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/pdf_text_extract.py`
- 用途：把一份显式保存的本地 PDF 确定性提取为一份带输入哈希和页边界的 UTF-8 文本。
- 输入 Schema：CLI `--input INPUT.pdf --output OUTPUT.txt`；只读取该本地 PDF，不接收证券、日期、
  来源、owner 或研究目录路由参数。运行依赖为 `pypdf>=6.16,<7`。
- 输出 Schema：文本头依次为 `HETU_PDF_TEXT_V1`、`input_sha256=<64 hex>`、
  `page_count=<int>`、`empty_text_pages=<none|逗号分隔页码>`；正文以 `<<<PAGE N>>>` 标记每页，
  无可提取文本的页面写 `[NO_EXTRACTABLE_TEXT]`，文件末尾固定一个换行。
- 失败异常：PDF 解析或任一页提取异常退出 1 且不产生输出；输入缺失或不可读、输入输出同路径、
  输出已存在或发布竞态退出 2，既有输出保持原样，临时文件清理。
- 是否可离线重放：是（从同一输入字节计算哈希并提取，不联网、不读取时钟）。
- 禁止边界：不联网、不 OCR、不判断 owner、不硬编码证券、公司、日期、目录或 W4/W5 路由；不选择
  来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号。
- 实际测试文件：`tests/product/skill/test_pdf_text_extract_tool.py`、
  `tests/product/skill/test_deterministic_tool_io.py`。

### financial-ratio-series.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/financial-ratio-series.py`
- 用途：从已标准化、显式给出主体/范围/单位的财务序列计算固定指标集：毛利率、归母净利率、经营现金转化、净营运资本及其占收入比、应收与存货占总资产比，以及收入与归母利润的端点 CAGR。
- 输入 Schema：JSON 对象 `{"subject": 非空字符串, "scope": 非空字符串, "unit": 非空字符串, "input_hash": 小写十六进制 8–64 位（上游 normalized 产物哈希）, "periods": [{"period": YYYY-MM-DD 或 YYYYMMDD 日历日期, "revenue", "cost", "attributable_profit", "operating_cash_flow", "current_assets", "current_liabilities", "accounts_receivable", "inventory", "total_assets": 十进制数}]}`；`periods` 非空、标签不重复且按解析后日期严格升序；可选 `source` 块。CLI 参数：`--input`、`--output`。
- 输出 Schema：`result = {"subject", "scope", "unit", "input_hash"（原样回显）, "periods": [每期 7 项指标], "cagr": {"revenue_percent" 或 "revenue_status"+"revenue_reason", "attributable_profit_percent" 或对应状态与原因}}`。比率类结果为精确 Decimal 字符串（非精确除法保留完整上下文有效位，不四舍五入）；CAGR 百分比按 `CAGR_QUANTUM = Decimal("0.1")` 量化为一位小数（见"已知输入契约限制"）。
- 失败异常：缺字段（含缺 `input_hash`）、多余字段、期间标签非严格 `YYYY-MM-DD`/`YYYYMMDD` 日历日期、重复/非升序期间、零分母、非有限数、`input_hash` 非法时抛 `ValueError`（退出 1）；CAGR 首尾值非正或跨度为零时输出正式 `not_computable` 状态与原因，不猜测方向；参数/输入缺失/输出冲突退出 2。
- 是否可离线重放：是。
- 禁止边界：不选择财务口径、不解释结果好坏；不选择来源、不决定换源、不裁决冲突、不生成报告结论、不生成交易信号。CAGR 年化跨度按端点日期真实年距计算（ACT/365.25，见"已知输入契约限制"）。
- 实际测试文件：`tests/product/skill/test_financial_ratio_series_tool.py`、`tests/product/skill/test_tool_cli_contract.py`、`tests/product/skill/test_deterministic_tool_io.py`。

### market-series-metrics.py

- 状态：`adopted`
- 文件路径：`skills/hetu-stock-analysis/scripts/market-series-metrics.py`
- 用途：从已保存、显式给出复权口径/时区/`as_of`/窗口的收盘价序列计算窗口收益率、简单均线、窗口高低和最大回撤（按窗口内逐点历史峰值）。
- 输入 Schema：JSON 对象 `{"subject": 非空字符串, "adjustment": 非空字符串, "timezone": 可解析 IANA 时区, "as_of": 带时区 ISO-8601, "input_hash": 小写十六进制 8–64 位（上游 normalized 产物哈希）, "window": 正整数, "bars": [{"timestamp": 带时区 ISO-8601, "close": 正有限十进制数}]}`；bars 严格升序、不晚于 `as_of`（恰为 `as_of` 的 bar 保留）、`window` 不得超过 bar 数；声明时区必须可解析且与 `as_of` 及每个 bar 时间戳的实际偏移一致；可选 `source` 块。CLI 参数：`--input`、`--output`。
- 输出 Schema：固定十一个字段 `{"return_percent", "simple_moving_average", "window_high", "window_low", "max_drawdown_percent", "effective_start", "effective_end", "adjustment", "timezone", "window", "input_hash"}`；数值为 Decimal 字符串；复权口径与时区原样透传；`input_hash` 原样回显；无任何信号字段。
- 失败异常：缺/多字段（含缺 `input_hash`）、naive 时间、不可解析时区、时间偏移与声明时区矛盾、重复或逆序时间、晚于 `as_of` 的 bar、窗口越界、非正或非有限价格、`input_hash` 非法抛 `ValueError`（退出 1）；参数/输入缺失/输出冲突退出 2。
- 是否可离线重放：是。
- 禁止边界：不生成交易信号，任何指标不得被解释为买卖建议；不选择来源、不决定换源、不裁决冲突、不生成报告结论。
- 实际测试文件：`tests/product/skill/test_market_series_metrics_tool.py`、`tests/product/skill/test_tool_cli_contract.py`、`tests/product/skill/test_deterministic_tool_io.py`。

## 阶段 04 机械助手

### source_adapter.py

- 状态：`mechanical_helper`（机械助手，不是第六个分析候选工具）
- 文件路径：`skills/hetu-stock-analysis/scripts/source_adapter.py`
- 用途：对显式保存的原始响应做解析、失败分类、字段映射核验、等价边界计算和元数据信封；Agent 决定是否调用、如何保存原始响应（含来源元数据与字段依据）以及是否采用结果。
- 输入 Schema：直呼 `adapt_saved_response(domain, source_id, saved_payload, *, subject, as_of, period, scope, unit, purpose, disabled_sources=())`；CLI `--input`/`--output`，输入 JSON 为 `{domain, source_id, saved_payload, subject, as_of, period?, scope?, unit?, purpose, disabled_sources}`，其中 `disabled_sources` 是正式必填字段，不依赖调用方约定。`domain` 限四个结构化域（公告及附件、财务报表、同业市场估值、交易状态与市场快照）；行业需求与竞争为合同-only，适配器正式拒绝（`ValueError`）。`saved_payload` 须为对象并含 `source_metadata`（身份、入口、采集时间、字段依据、许可标注五类要素原样透传，适配器不生成、不补写；五类要素任一缺失、入口非 http(s) URL、采集时间非带时区 ISO-8601 即拒绝该次调用——键名别名为 身份 provider/producer/channel/identity/source、入口 entry/endpoint/url、采集时间 fetch_request_time/fetch_request_times/captured_at/collected_at、字段依据 field_basis/raw_file/raw_files/annual_report_reference/share_capital_basis/field_dictionary、许可 license/licensing，覆盖合成 Schema 与已保存批次形态）、`response`（整数 `status_code`）与 2xx 时的 `body`，以及主体/期间/范围/单位/用途记录字段。`body` 形态：公告及附件＝巨潮分页 `pages`（条目解析经 `announcement_index.py` 承接；分页闭合由适配器按各页 `totalAnnouncement` 一致、返回数相等、末页 `hasMore=false`＋请求页序＝保存页序判定——真实响应无 `pageNum` 且 `totalpages` 不可靠，携带显式连续 `pageNum` 的合成 payload 按页码连续性闭合）；财务报表＝`format` 二选一——`sina_report_list`（适配器先施加已证形态容差：仅跳过 `item_value` 为显式 `null` 的行（缺失该键的行不在容差内，按结构偏差判 `parse_error`）、数值型 `item_tongbi` 转字符串，再经 `financial_statements.py` 承接，阶段 03 工具本身保持仅字符串 Schema）或 `eastmoney_push2_indicators`（按适配器自带字段字典 `f183` 营业总收入、`f184` 营业总收入同比增长、`f185` 归母净利润同比增长、`f186` 销售毛利率、`f188` 资产负债率解析，一手依据见"独立字段字典一手依据"节，未知 f 码判 `parse_error`）；同业市场估值与交易状态与市场快照＝快照 body（必含 `trade_date`、`price`，股本与市值标签字段可选）。
- 输出 Schema：信封固定为 `{schema_version, adapter, domain, source_id, called, disabled, status, source_metadata, normalized, equivalence, raw_input_sha256}`。来源可用时 `called=true`、`disabled=false`、`status` 为九值闭集之一（`success`、`not_found`、`rate_limited`、`transport_error`、`permission_denied`、`parse_error`、`incomplete_pagination`、`out_of_asof`、`scope_mismatch`），分类顺序固定：记录的 HTTP 状态（429→`rate_limited`；401/403→`permission_denied`；404→`not_found`；其他非 2xx→`transport_error`）→ body 解析（结构变化→`parse_error`）→ 分页与检索面（公告分页不闭合→`incomplete_pagination`；检索面闭合且为空→`not_found`，财务域含每期全部科目值为空）→ 主体/期间/范围/单位/用途轴（不符→`scope_mismatch`，双方原值在 `equivalence.axes` 保留）→ 内容晚于 `as_of`（全时刻精度：公告按毫秒时刻、快照按 `trade_date`＋可选 `trade_time`（挂接来源时区：body 或 source_metadata 的 `timezone`——接受 IANA 名、±HH:MM、含内嵌 IANA 记号或封闭中文标准时名（北京时间/中国标准时间→Asia/Shanghai）的装饰性声明，无法识别判 `parse_error`；`HH:MM` 与 `HH:MM:SS` 均接受）、财务按报告期日；全部内容晚于→`out_of_asof`，同日 `as_of` 时刻之后的内容不进入成功状态；同日无 `trade_time` 的快照时点不可证，`content_after_asof=null` 且不能单独支持替代取得）→ `success`。来源被禁用时 `called=false`、`disabled=true`、`status=null`、`normalized=null`：不进入九种取数状态、不判为取数失败、不自动调用替代，是否改用替代由 Agent 决定。`equivalence` 只做机械检查：各轴保留全部原值（主体/单位轴为 requested/payload/body 三值——快照域交叉核验 body 实际 `security` 与 `price_unit`，主体按规范化代码（sz000002/000002.SZ 同码）比较，不可规范化的标识判 `match=null`；期间轴交叉核验 body 实际覆盖（快照 `trade_date`、财务报告期集合），请求期间不在覆盖内即 `match=false`→`scope_mismatch`；比较基于规范币种族（元/CNY/RMB 同族、USD/美元同族等）：可规范化双方真实矛盾→`match=false`→`scope_mismatch`，不可规范化的描述性标签（如「价格元、市值亿元」）→`match=null` 机械不可核验、由 Agent 核验且不能单独支持替代取得，三值原样保留）；`as_of` 节增列 `content_moment`（公告毫秒时刻或快照 `trade_date`＋`trade_time`）；快照域的价格×股本复算仅在市值标签单位与价格单位一致时给出精确 Decimal 比较，否则 `consistent=null` 并保留双方原值，绝不交换标签；任一轴 `match=null`（保存响应未记录该轴或请求未提供）不构成该轴核验通过，不能单独支持「替代取得」标记，按未核验如实披露。`raw_input_sha256` 为 `saved_payload` 规范序列化（排序键、紧凑分隔符、UTF-8）的 SHA-256；相同输入字节稳定，输入变化必然改变该值。
- 失败异常：`domain` 非四结构化域（含行业需求与竞争）、缺 `disabled_sources`、`saved_payload` 非 object、缺/坏 `source_metadata`、`response` 或 2xx 缺 `body`、`as_of` 无时区等输入 Schema 违规抛 `ValueError`（退出 1，失败信封）；来源侧 body 结构变化不抛异常，落 `status=parse_error`（退出 0）；参数错误/输入缺失或不可读/输出与输入相同/输出已存在（含并发创建）退出 2，均不产生部分产物。
- 是否可离线重放：是（只读显式保存文件，无网络、无时钟依赖）。
- 禁止边界：不联网、不选择来源、不决定换源、不自动调用替代、不裁决采用或冲突、不生成报告结论、不生成交易信号、不改写任何来源原值；来源元数据只透传不生成、不补写。
- 实际测试文件：`tests/product/skill/test_source_adapter.py`、`tests/product/skill/test_deterministic_tool_io.py`。


## 阶段 05 机械检查器

### check-run-artifacts.py

- 状态：`mechanical_helper`（机械检查器，不是第七个分析候选工具）
- 文件路径：`skills/hetu-stock-analysis/scripts/check-run-artifacts.py`
- 用途：对一次已完成研究的产物做无状态单次机械检查——必需文件（W0–W10、checkpoint、evidence、manifest、report）、manifest JSON 解析与条目/输入的路径安全和哈希、条目状态封闭集（`adopted/superseded/failed/not_adopted`，failed 条目须带失败原因、script 条目须带脚本元数据块）、manifest 闭合 Schema（顶层 `schema_version`/`run` 必填块、非空 `artifacts`、条目封闭键集与 type/work_package 合法值——稳定码 `manifest.schema`）、十二章固定顺序、首页固定字段（含“分析模型”）与第 2 章十行核心发现固定行均须为表格行（散文关键词不通过）、W10 报告映射须为含五字段表头＋至少一行数据的表格、`artifacts/scripts/` 实际文件对 manifest 的登记与属主目录归属（属主限 W0–W10，未知属主判 `script.unknown_owner`）、无脚本工作包的“未创建或修改中间脚本”声明、lock record 的最终消息哈希与研究树哈希重算及记录路径绑定（伪造路径判 `lock.message_path_mismatch`/`lock.research_path_mismatch`，父字段类型错误按不可读输入退出 2；缺失或类型错误的绑定哈希字段直接产生 issue，绝不静默放行；`PASS` 同时要求零 issue 且全部 check 为 ok）。不判断自然语言真假、来源适用性或采用裁决。
- 输入：`check_run(research_root, delivery_message, lock_record)`；CLI `--research-root`/`--delivery-message`/`--lock-record`/`--output`。
- 输出 Schema：`{schema_version, mechanical_status, message_input_status, checks, issues}`；issue 固定 `{code, path, message}`；最终消息缺失时 `message_input_status="not_checked"`，整体不得 `PASS`。
- 失败异常：有 issue 时仍写结果并退出 1；参数错误、输入不可读、输入为符号链接或输出已存在退出 2，无部分产物（输出经临时文件＋原子 link 落盘）。
- 是否可离线重放：是（只读显式路径，无网络、无时钟依赖）。
- 禁止边界：不执行工作包、不产生任何自然语言语义裁决（`mechanical_status` 的 `PASS/FAIL` 只表示机械结构闭合与否，`PASS` 不是发布许可）、不读取 G1/G2 答案、不作为 `hetu-stock` 子命令存在。
- 实际测试文件：`tests/product/skill/test_artifact_checker.py`。

## 需求 4.2 六类已知错误的现行保护

六类错误的当前保护直接运行 canonical 接口和一手字段字典，不读取退役目录或任何历史脚本。历史实现只可在 Git 历史中定位，不是运行依赖。

| # | 已防护行为 | 现行具名测试 |
| --- | --- | --- |
| 1 | 东财字段序号含义与旧标签矛盾 | `test_source_adapter.py::test_eastmoney_dictionary_keeps_corrected_meanings_and_old_contradictions` 与 EastMoney 适配测试 |
| 2 | 腾讯总/流通市值标签不得互换 | `test_numeric_consistency_tool.py::test_market_cap_values_expose_swapped_total_and_float_labels`、`::test_market_cap_check_does_not_swap_source_labels`、`::test_compare_metric_keeps_both_values_and_difference` |
| 3 | 不同 Schema 不得静默覆盖同名输出 | `test_deterministic_tool_io.py::test_existing_output_file_is_rejected_not_overwritten` 与各工具的 `test_cli_existing_output_is_refused` |
| 4 | 不得硬编码证券、日期或绝对路径，且必须过滤晚于 `as_of` 的内容 | `test_deterministic_tool_io.py::test_canonical_scripts_have_no_hardcoded_security_date_or_absolute_path`、`test_announcement_index_tool.py::test_pages_filter_after_as_of_strip_html_and_use_https`、`test_market_series_metrics_tool.py::test_bar_after_as_of_is_rejected` |
| 5 | 公告 URL 只允许 HTTPS 且分页必须闭合 | `test_announcement_index_tool.py::test_announcement_pages_reject_absolute_attachment_urls` 和分页完整性系列；`test_source_fetch.py` 的 allowlist 与中途失败测试 |
| 6 | 不得在主脚本外补采、重试或 fallback | `test_deterministic_tool_io.py::test_scripts_directory_contains_only_approved_files`、`::test_same_input_produces_byte_identical_envelopes`、`test_numeric_consistency_tool.py::test_cli_unreadable_input_returns_2_without_partial_output`、`test_source_fetch.py::test_source_fetch_does_not_retry_or_call_fallback` |

## 独立字段字典一手依据

东财字段编码的独立一手依据固定在 `tests/product/fixtures/forensic/eastmoney-field-dictionary.json`（获取时间 `2026-08-21T21:41:41+08:00`，快照采集时间 `2026-08-22T00:12:17+08:00`），出处不是取证文件：

- 方法：对取样证券 `600519.SH 贵州茅台`（取样对象，与任何研究主体无关）、报告期 `2026-06-30` 中报（公告日 `2026-08-15`），把 `push2.eastmoney.com/api/qt/stock/get`（`fltt=2`，按 10 位小数返回）的字段值与东方财富 F10 主要指标具名字段接口（`emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew`）逐一比对，含义以能精确对上的 F10 具名字段为准。一手字段字典保存已验证的字段意义、交叉引用和与旧标签的矛盾；现行测试核对稳定适配器核心名称及完整口径元数据，不在运行时从两份快照重新推导对应关系。
- 结论：`f183` 为营业总收入（对照 `TOTALOPERATEREVE`；旧脚本误标 `rev_yoy`，同比增长在 `f184`）；`f186` 为销售毛利率（`XSMLL`；旧标 `np_yoy`，归母净利润同比增长在 `f185`）；`f188` 为资产负债率（`ZCFZL`；旧标 `gross_margin`，销售毛利率在 `f186`）。
- 取样值只证明字段身份，不代表任何研究对象的数值；字典不被 canonical 脚本导入或执行，仅供取证对照测试引用。

## 已采用的确定性数值合同

用户于 2026-08-22 仅对本轮阶段 3 评审缺口给予一次性直接修复授权，并在本次修复中正式采用以下合同；该授权不延伸至后续评审或实现，后续改变精度、量化步长或年化跨度规则仍须重新获得用户批准：

1. **数值表示规则**：全部工具的比率与算术输出在有理精确时输出完整 Decimal 字符串；非精确除法保留固定上下文（prec=28、ROUND_HALF_EVEN，不随进程环境变化）的全部有效位、不四舍五入。唯一例外是 CAGR：幂运算结果按常量 `CAGR_QUANTUM = Decimal("0.1")` 量化——该常量是 CAGR 百分比的量化步长（0.1 个百分点，ROUND_HALF_EVEN），例如端点 `"10.0"`。
2. **CAGR 年化跨度规则（ACT/365.25）**：期间标签必须解析为日历日期（严格 `YYYY-MM-DD` 或 `YYYYMMDD`），年化跨度 = `(末期日期 − 首期日期).days / 365.25`（正的端点真实年距，与实现 ``span_days = (last_date - first_date).days`` 一致），而非期间间隔数；序列按解析后日期严格升序，缺期不影响年化的正确性（跨度按日期差计算）。

## 接入边界

工具以 canonical Skill `scripts/` 下的独立脚本提供，通过显式参数接收输入和输出路径；不注册新的 `hetu-stock` CLI 命令。相同保存输入重复运行必须产生字节相同的信封；输入变化产生不同 `input_sha256` 产物身份。脚本只处理显式保存的输入并写显式输出，不联网、不选择来源、不推导覆盖、不生成业务结论；来源选择、证据采用和冲突裁决始终由 Agent 负责。工具是可选局部助手：调用前确认输入、输出、授权和文件范围，脚本失败时用宿主等价能力、合法替代或记录局部缺口，不使整个研究无条件失败。输出按产物合同进入 `raw/`、`normalized/` 或 `derived/` 并登记 `manifest.json`，不在研究目录外留下最终结果。

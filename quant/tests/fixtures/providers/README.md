# 供应商测试原始响应

现有 Parquet 材料用途保持。2026-10-16 评审修复新增两份原始 JSON，均逐字复制已存响应，未从标准化实现反生成：

- `em-fhps-raw-301059.json`：来自 `.hetu/quant/qualification/capability-gap-20261016/em-fhps-raw-301059.json`；东财 RPT_SHAREBONUS_DET，301059 送转现金条款换算的独立原始形状；消费者 `test_bj_actions_conversion_matches_baostock_values`。这只是字段换算对照，不证明 301059 属于北交所。
- `bj-kline-920002-raw.json`：来自同目录同名原件；东财 push2his，920002、2026-09-01..2026-10-13；消费者 `test_bj_daily_fetch_uses_pure_numeric_secid`，仅替换网络，真实 fetch 解析。

测试从本目录显式取材，不读取运行工作区 `.hetu/`，不触网；预期值保留在测试侧。历史在线验证输出保留于原位置，修复后离线重放另存 `bj-provider-verify-corrected.json`，不冒称新在线验证。

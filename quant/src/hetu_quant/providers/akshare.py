"""AKShare 记录的公开免费端点适配（申万宏源研究所与东方财富）。

实测（2026-10-14）：akshare 1.19.1 默认 requests 会话在本环境访问
swsresearch.com 与 push2.eastmoney.com 会被挂起；同一公开端点经
curl_cffi（Chrome TLS 指纹）正常返回 JSON。本适配器访问与 akshare
文档相同的端点，参数与 akshare 对应函数一致，仍属免费公开来源。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from curl_cffi import requests as creq

from hetu_quant.providers.baostock import normalize_code

CN = ZoneInfo("Asia/Shanghai")

SW_BASE = "https://www.swsresearch.com/institute-sw/api/index_publish"
EM_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_THEMELIB = "https://quote.eastmoney.com/zhuti/api/hottheme"
EM_FHPS = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BSE_LIST = "https://www.bse.cn/nqxxController/nqxxCnzq.do"

JSON_HEADERS = {"Accept": "application/json"}


class AKShareProvider:
    name = "akshare"

    def __init__(self, timeout: float = 30.0, retries: int = 2) -> None:
        self._timeout = timeout
        self._retries = retries

    def _get_json(self, url: str, params: Mapping[str, object]) -> dict:
        last_error: Exception | None = None
        for _ in range(self._retries + 1):
            try:
                resp = creq.get(
                    url,
                    params=dict(params),
                    headers=JSON_HEADERS,
                    impersonate="chrome",
                    timeout=self._timeout,
                    verify=False,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as error:  # noqa: BLE001
                last_error = error
                time.sleep(1.0)
        raise ConnectionError(f"akshare endpoint failed: {url}: {last_error}")

    # ---- 原始获取 ----

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        start_s, end_s = start.isoformat(), end.isoformat()
        if product_id == "sw_l1_catalog":
            rows: list[dict] = []
            page = 1
            while True:
                payload = self._get_json(
                    f"{SW_BASE}/current/",
                    {"page": page, "page_size": 50, "indextype": "一级行业"},
                )
                data = payload.get("data") or {}
                rows.extend(data.get("results") or [])
                if not data.get("next"):
                    break
                page += 1
            return pd.DataFrame(rows)
        if product_id == "sw_l1_index":
            frames = []
            for code in codes:
                payload = self._get_json(
                    f"{SW_BASE}/trend/", {"swindexcode": code, "period": "DAY"}
                )
                frame = pd.DataFrame(payload.get("data") or [])
                if len(frame):
                    # 该端点无日期参数、恒返回全历史：按请求区间截断，
                    # 保证多分区调用合并时不重复（2026-10-14 全市场运行实测）。
                    frame = frame[
                        (frame["bargaindate"].astype(str) >= start_s)
                        & (frame["bargaindate"].astype(str) <= end_s)
                    ]
                    if len(frame):
                        frames.append(frame)
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        if product_id == "sw_l1_members":
            frames = []
            for code in codes:
                payload = self._get_json(
                    f"{SW_BASE}/details/component_stocks/",
                    {"swindexcode": code, "page": 1, "page_size": 10000},
                )
                data = payload.get("data") or {}
                results = data.get("results") if isinstance(data, dict) else data
                frame = pd.DataFrame(results or [])
                if len(frame):
                    frame["board"] = code
                    frames.append(frame)
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        if product_id == "em_theme_catalog":
            rows = []
            page = 1
            while True:
                payload = self._get_json(
                    EM_LIST,
                    {
                        "pn": page,
                        "pz": 100,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": "m:90 t:3 f:!50",
                        "fields": "f12,f14,f3",
                    },
                )
                data = payload.get("data") or {}
                rows.extend(data.get("diff") or [])
                total = int(data.get("total") or 0)
                if page * 100 >= total:
                    break
                page += 1
            return pd.DataFrame(rows)
        if product_id == "em_theme_members":
            rows = []
            for board in codes:
                page = 1
                while True:
                    payload = self._get_json(
                        EM_LIST,
                        {
                            "pn": page,
                            "pz": 100,
                            "po": 1,
                            "np": 1,
                            "fltt": 2,
                            "invt": 2,
                            "fid": "f3",
                            "fs": f"b:{board} f:!50",
                            "fields": "f12,f13,f14",
                        },
                    )
                    data = payload.get("data") or {}
                    diff = data.get("diff") or []
                    for item in diff:
                        item["board"] = board
                    rows.extend(diff)
                    total = int(data.get("total") or 0)
                    if page * 100 >= total:
                        break
                    page += 1
            return pd.DataFrame(rows)
        if product_id == "em_theme_index":
            frames = []
            for board in codes:
                payload = self._get_json(
                    EM_KLINE,
                    {
                        "secid": f"90.{board}",
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "klt": "101",
                        "fqt": "0",
                        "beg": start.strftime("%Y%m%d"),
                        "end": end.strftime("%Y%m%d"),
                        "smplmt": "10000",
                        "lmt": "1000000",
                    },
                )
                data = payload.get("data") or {}
                klines = data.get("klines") or []
                rows = []
                for line in klines:
                    parts = str(line).split(",")
                    rows.append(
                        {
                            "board": board,
                            "date": parts[0],
                            "open": parts[1],
                            "close": parts[2],
                            "high": parts[3],
                            "low": parts[4],
                            "volume": parts[5],
                            "amount": parts[6],
                        }
                    )
                frames.append(pd.DataFrame(rows))
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        if product_id == "em_theme_library":
            # 东财"主题投资·题材库"（AllThemes）：response result[0] 携带
            # FieldName（逗号分隔列名）与 Data（每行按 SplitSymbol=| 分隔的
            # 字符串）。raw 层保留原始行字符串原形，解析只发生在 standardize。
            payload = self._get_json(
                EM_THEMELIB, {"startIndex": 1, "pageSize": 2000}
            )
            result = (payload.get("result") or [{}])[0]
            return pd.DataFrame(
                {
                    "raw_row": [str(row) for row in (result.get("Data") or [])],
                    "field_name": str(result.get("FieldName") or ""),
                }
            )
        if product_id == "bj_basic":
            # 北交所官网股票列表（nqxxCnzq.do，POST 分页）。响应为 JSONP 风格
            # 数组文本，data[0] 携带 totalPages 与 content（位置数组）。
            rows: list = []
            page = 0
            while True:
                form = {
                    "page": str(page),
                    "typejb": "T",
                    "xxfcbj[]": "2",
                    "xxzqdm": "",
                    "sortfield": "xxzqdm",
                    "sorttype": "asc",
                }
                data: list | None = None
                last_error: Exception | None = None
                for _ in range(self._retries + 1):
                    try:
                        resp = creq.post(
                            BSE_LIST,
                            data=form,
                            headers={"User-Agent": "Mozilla/5.0", **JSON_HEADERS},
                            impersonate="chrome",
                            timeout=self._timeout,
                            verify=False,
                        )
                        text = resp.text
                        data = json.loads(text[text.find("[") : -1])
                        break
                    except Exception as error:  # noqa: BLE001
                        last_error = error
                        time.sleep(1.0)
                if data is None:
                    raise ConnectionError(f"bse endpoint failed: {BSE_LIST}: {last_error}")
                head = data[0]
                rows.extend(head.get("content") or [])
                total_pages = int(head.get("totalPages") or 1)
                page += 1
                if page >= total_pages:
                    break
            return pd.DataFrame(rows)
        if product_id == "bj_daily":
            # 东财 push2his 日线。BJ 的 secid 实测（2026-10-16）为市场前缀 0 +
            # 纯数字代码（920002.BJ → 0.920002）；akshare 1.19.1 的
            # stock_zh_a_hist 拼成 0.920002.BJ 返回空，故本适配器直连。
            frames = []
            for code in codes:
                num = code.split(".")[0]
                payload = self._get_json(
                    EM_KLINE,
                    {
                        "secid": f"0.{num}",
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "klt": "101",
                        "fqt": "0",
                        "beg": start.strftime("%Y%m%d"),
                        "end": end.strftime("%Y%m%d"),
                        "smplmt": "10000",
                        "lmt": "1000000",
                    },
                )
                klines = (payload.get("data") or {}).get("klines") or []
                rows = []
                for line in klines:
                    # f51 日期,f52 开盘,f53 收盘,f54 最高,f55 最低,f56 成交量(手),f57 成交额(元)
                    parts = str(line).split(",")
                    if len(parts) < 7:
                        continue
                    rows.append(
                        {
                            "code": f"{num}.BJ",
                            "date": parts[0],
                            "open": parts[1],
                            "close": parts[2],
                            "high": parts[3],
                            "low": parts[4],
                            "volume": parts[5],
                            "amount": parts[6],
                        }
                    )
                if rows:
                    frames.append(pd.DataFrame(rows))
            if not frames:
                return pd.DataFrame(
                    columns=["code", "date", "open", "close", "high", "low", "volume", "amount"]
                )
            return pd.concat(frames, ignore_index=True)
        if product_id == "bj_actions":
            # 东财 datacenter 分红送配详情（RPT_SHAREBONUS_DET，含 BJ；分页）。
            # 覆盖现金分红与送转；配股不在本报表字段内（600030 全史实测无
            # 2022 年配股行，见 capability-gap-20261016 交叉核对证据）。
            frames = []
            for code in codes:
                num = code.split(".")[0]
                page = 1
                while True:
                    payload = self._get_json(
                        EM_FHPS,
                        {
                            "sortColumns": "REPORT_DATE",
                            "sortTypes": "-1",
                            "pageSize": "500",
                            "pageNumber": str(page),
                            "reportName": "RPT_SHAREBONUS_DET",
                            "columns": "ALL",
                            "quoteColumns": "",
                            "js": '{"data":(x),"pages":(tp)}',
                            "source": "WEB",
                            "client": "WEB",
                            "filter": f'(SECURITY_CODE="{num}")',
                        },
                    )
                    result = payload.get("result") or {}
                    data = result.get("data") or []
                    if data:
                        frames.append(pd.DataFrame(data))
                    pages = int(result.get("pages") or 1)
                    if page >= pages:
                        break
                    page += 1
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        raise KeyError(f"akshare provider does not serve product: {product_id}")

    # ---- 标准化 ----

    def standardize(
        self, product_id: str, raw: pd.DataFrame, product: Mapping[str, object]
    ) -> dict[str, pd.DataFrame]:
        if not len(raw):
            return {product["tables"][0]: raw}
        now = datetime.now(CN).isoformat()
        if product_id == "sw_l1_catalog":
            return {
                "boards": pd.DataFrame(
                    {
                        "board_id": raw["swindexcode"].astype(str),
                        "board_kind": "industry",
                        "name": raw["swindexname"],
                        "provider": "sw",
                        "classification_version": "sw_l1_current_observed",
                    }
                )
            }
        if product_id == "sw_l1_index":
            # 实测（2026-10-14，evidence 见 .hetu/quant/qualification/）：
            # bargainsum 单位为亿元——274.44 亿 ≥ 当日成员样本成交额之和 25.5 亿元。
            amount = pd.to_numeric(raw["bargainsum"], errors="coerce") * 1e8
            return {
                "index_bars": pd.DataFrame(
                    {
                        "code": raw["swindexcode"].astype(str),
                        "date": raw["bargaindate"].astype(str),
                        "close": pd.to_numeric(raw["closeindex"], errors="coerce"),
                        "amount_cny": amount,
                        "return_basis": "price",
                        "raw_amount_field": raw["bargainsum"],
                    }
                )
            }
        if product_id == "sw_l1_members":
            code_col = "stockcode" if "stockcode" in raw.columns else raw.columns[0]
            return {
                "memberships": pd.DataFrame(
                    {
                        "board_id": raw["board"].astype(str),
                        "code": raw[code_col].astype(str).map(normalize_code),
                        "observed_at": now,
                        "available_at": now,
                        "effective_from": None,
                        "effective_to": None,
                        "membership_kind": "current_snapshot",
                    }
                )
            }
        if product_id == "em_theme_catalog":
            # 冻结协议（ranking-defaults category）：产业主题/风格/标签须按原始类别
            # 或逐项依据分类；实测原始端点无类别字段（探查 2026-10-14），分类依据
            # 缺失时如实标注为待核验候选；排名入口按本产品来源送入统一分类门，
            # 名称命中且逐条核验为产业主题才放行，不在标准化时猜测标为 theme。
            return {
                "boards": pd.DataFrame(
                    {
                        "board_id": raw["f12"].astype(str),
                        "board_kind": "unclassified",
                        "name": raw["f14"],
                        "provider": "eastmoney",
                        "classification_version": "em_concept_current_observed",
                    }
                )
            }
        if product_id == "em_theme_library":
            # 题材库每行是 题材+代表证券，同一题材多行：按题材代码去重保留
            # 首见（FieldName 前四列为 CategoryCode,CategoryName,ParentCode,
            # ParentName，与 SplitSymbol=| 按位置对应）。只解析实体对齐所需
            # 四列；raw 层原始行字符串已保留。
            rows = []
            for line in raw["raw_row"]:
                parts = str(line).split("|")
                if len(parts) < 4:
                    continue
                rows.append(
                    {
                        "theme_code": parts[0],
                        "theme_name": parts[1],
                        "parent_code": parts[2],
                        "parent_name": parts[3],
                    }
                )
            library = pd.DataFrame(
                rows,
                columns=["theme_code", "theme_name", "parent_code", "parent_name"],
            )
            if len(library):
                library = library.drop_duplicates(subset=["theme_code"], keep="first")
            return {"theme_library": library}
        if product_id == "em_theme_members":
            return {
                "memberships": pd.DataFrame(
                    {
                        "board_id": raw["board"].astype(str),
                        "code": raw["f12"].astype(str).map(normalize_code),
                        "observed_at": now,
                        "available_at": now,
                        "effective_from": None,
                        "effective_to": None,
                        "membership_kind": "current_snapshot",
                    }
                )
            }
        if product_id == "em_theme_index":
            return {
                "index_bars": pd.DataFrame(
                    {
                        "code": raw["board"].astype(str),
                        "date": raw["date"].astype(str),
                        "close": pd.to_numeric(raw["close"], errors="coerce"),
                        "amount_cny": pd.to_numeric(raw["amount"], errors="coerce"),
                        "return_basis": "price",
                    }
                )
            }
        if product_id == "bj_basic":
            # bse.cn 原始行是字典（2026-10-16 实测字段名；akshare 1.19.1 的
            # 位置映射基于旧版响应，与本响应不匹配）：xxzqdm 证券代码、
            # xxzqjc 证券简称、xxzgb 总股本、xxfxsgb 发行流通股本、
            # fxssrq 发行上市日期（YYYYMMDD）、xxhyzl 行业、xxssdq 地区、
            # xxjsrq 数据截止日。列表无退市标记，status 仅表示当前在册。
            def _col(field: str) -> pd.Series:
                if field in raw.columns:
                    return raw[field].reset_index(drop=True)
                return pd.Series([None] * len(raw), dtype=object)

            def _date(field: str) -> pd.Series:
                values = _col(field).astype(str).str.slice(0, 8)
                return values.where(values.str.len() == 8, None).map(
                    lambda value: f"{value[:4]}-{value[4:6]}-{value[6:8]}" if value else None
                )

            return {
                "securities": pd.DataFrame(
                    {
                        "code": _col("xxzqdm").astype(str) + ".BJ",
                        "name": _col("xxzqjc").astype(str),
                        "market": "BJ",
                        "ipo_date": _date("fxssrq"),
                        "out_date": None,
                        "security_type": "stock",
                        "status": "listed",
                        "raw_total_shares": _col("xxzgb"),
                        "raw_float_shares": _col("xxfxsgb"),
                        "raw_industry": _col("xxhyzl"),
                        "raw_report_date": _col("xxjsrq"),
                    }
                )
            }
        if product_id == "bj_daily":
            return {
                "daily_bars": pd.DataFrame(
                    {
                        "date": raw["date"].astype(str),
                        "code": raw["code"].astype(str),
                        "open_raw": pd.to_numeric(raw["open"], errors="coerce"),
                        "high_raw": pd.to_numeric(raw["high"], errors="coerce"),
                        "low_raw": pd.to_numeric(raw["low"], errors="coerce"),
                        "close_raw": pd.to_numeric(raw["close"], errors="coerce"),
                        # 东财成交量单位为手（2026-09-01 万达轴承实测 11641 手 ×
                        # 均价 53.12 ≈ 当日成交额 6183 万元），×100 转股
                        "volume_shares": pd.to_numeric(raw["volume"], errors="coerce") * 100,
                        "amount_cny": pd.to_numeric(raw["amount"], errors="coerce"),
                    }
                )
            }
        if product_id == "bj_actions":
            # 仅已实施（EX_DIVIDEND_DATE 非空）行构成事件；每 10 股口径换算为
            # 每股：现金 d=PRETAX_BONUS_RMB/10，送转 m=1+BONUS_IT_RATIO/10
            # （总比例缺失时退回送股+转增分量）。可得时点取预案公告日，
            # 缺失退回公告日；均为空时留空（未知不伪造）。
            rows = []
            for _, row in raw.iterrows():
                ex_date = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
                if not ex_date:
                    continue
                cash10 = pd.to_numeric(pd.Series([row.get("PRETAX_BONUS_RMB")]), errors="coerce").iloc[0]
                split10 = pd.to_numeric(pd.Series([row.get("BONUS_IT_RATIO")]), errors="coerce").iloc[0]
                if pd.isna(split10):
                    bonus = pd.to_numeric(pd.Series([row.get("BONUS_RATIO")]), errors="coerce").iloc[0]
                    transfer = pd.to_numeric(pd.Series([row.get("IT_RATIO")]), errors="coerce").iloc[0]
                    split10 = (0.0 if pd.isna(bonus) else float(bonus)) + (
                        0.0 if pd.isna(transfer) else float(transfer)
                    )
                d = 0.0 if pd.isna(cash10) else float(cash10) / 10.0
                m = 1.0 + (0.0 if pd.isna(split10) else float(split10) / 10.0)
                available = (
                    str(row.get("PLAN_NOTICE_DATE") or "")[:10]
                    or str(row.get("NOTICE_DATE") or "")[:10]
                    or None
                )
                code = f"{row.get('SECURITY_CODE')}.BJ"
                terms = f"report={str(row.get('REPORT_DATE') or '')[:10]};{row.get('IMPL_PLAN_PROFILE') or ''}"
                if d > 0:
                    rows.append(
                        {"code": code, "ex_date": ex_date, "action_type": "cash_dividend",
                         "m": 1.0, "d": d, "terms_raw": terms, "available_at": available}
                    )
                if m != 1.0:
                    rows.append(
                        {"code": code, "ex_date": ex_date, "action_type": "bonus_split",
                         "m": m, "d": 0.0, "terms_raw": terms, "available_at": available}
                    )
            frame = pd.DataFrame(
                rows,
                columns=["code", "ex_date", "action_type", "m", "d", "terms_raw", "available_at"],
            )
            if len(frame):
                frame = frame.drop_duplicates(subset=["code", "ex_date", "action_type"], keep="first")
            return {"corporate_actions": frame}
        raise KeyError(f"no standardize mapping for product: {product_id}")

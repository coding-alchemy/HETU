"""合成Tushare事实。只含原始供应商形状，不含资格或标准化答案。"""

import json
from datetime import date


def response(api="daily", *, code="SYNTH.SH", day="20260105", price=10, empty=False):
    facts = {
        "daily": (
            ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
            [code, day, price, price + 1, price - 1, price, 9, 12.34, 56.78],
        ),
        "daily_basic": (
            ["ts_code", "trade_date", "close", "total_share", "total_mv", "circ_mv"],
            [code, day, price, 100, 1000, 800],
        ),
        "stock_basic": (
            ["ts_code", "symbol", "name", "fullname", "exchange", "market", "list_status", "list_date", "delist_date"],
            [code, code.split(".")[0], "synthetic", "synthetic full name",
             {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[code.split(".")[-1]], "主板", "L", "20000101", None],
        ),
        "dividend": (
            [
                "ts_code",
                "end_date",
                "ann_date",
                "div_proc",
                "stk_div",
                "cash_div_tax",
                "record_date",
                "ex_date",
                "pay_date",
                "div_listdate",
                "imp_ann_date",
                "base_date",
                "base_share",
            ],
            [
                code,
                "20251231",
                "20260101",
                "实施",
                1,
                0.5,
                "20260104",
                day,
                "20260107",
                "20260109",
                "20260102",
                "20260104",
                100,
            ],
        ),
    }
    fields, row = facts[api]
    return json.dumps(
        {
            "code": 0,
            "msg": None,
            "data": {"fields": fields, "items": [] if empty else [row], "has_more": False, "count": 0},
        }
    ).encode()


class SourceClient:
    """只替换HTTP传输，调用参数及返回原形都能观察。"""

    def __init__(self, api="daily", *, empty=False, fail=False):
        self.api, self.empty, self.fail = api, empty, fail
        self.calls = []

    def query(self, api, params, fields):
        self.calls.append((api, params, fields))
        if self.fail:
            raise ConnectionError("synthetic upstream failure")
        day = params.get("start_date", "20260105")
        day = max(day, "20260105")
        if date.fromisoformat(day) > date.fromisoformat(params.get("end_date", day)):
            return response(api, empty=True)
        return response(api, code=params.get("ts_code", "SYNTH.SH"), day=day, empty=self.empty)

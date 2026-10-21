"""巨潮公开股本候选记录与有界实际事实；公告字段本身不提供PIT资格。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

_COLUMNS = ["code", "announcement_date", "effective_date", "total_shares", "reason",
            "latest_flag", "obtained_at", "source_row", "issues"]


def normalize_share_changes(payload: dict, code: str, obtained_at: str) -> pd.DataFrame:
    """保留所有响应版本和实际取得时间，万股按Decimal换算，不生成verified。

    单位与既有官方401000000/1256197800股原件对照一致；调用者仍须独立
    核公告是否记载完成事实及其适用期间。total与返回条数不等时拒绝截断。
    """
    records = payload["records"]
    if int(payload["total"]) != len(records):
        raise ValueError("share response count incomplete")
    rows = []
    for index, item in enumerate(records):
        if item["SECCODE"] != code.split(".")[0]:
            raise ValueError("share response security mismatch")
        shares = Decimal(str(item["F003N"])) * 10000
        if not shares.is_finite() or shares <= 0 or shares != shares.to_integral_value():
            raise ValueError("invalid exact share count")
        public = item["DECLAREDATE"]
        effective = item["VARYDATE"]
        issues = [label for value, label in [(public, "announcement_date_missing"),
                                            (effective, "effective_date_missing")] if not value]
        rows.append(dict(code=code,
                         announcement_date=date.fromisoformat(public).isoformat() if public else None,
                         effective_date=date.fromisoformat(effective).isoformat() if effective else None,
                         total_shares=int(shares), reason=item.get("F002V", ""),
                         latest_flag=item.get("F027C"), obtained_at=obtained_at, source_row=index,
                         issues=";".join(issues)))
    return pd.DataFrame(rows, columns=_COLUMNS)


def select_capital_facts(facts: pd.DataFrame, signal_day: str) -> pd.DataFrame:
    """只消费实际完成、有独立发布引用且期间有界的事实；不从候选行自动认定。

    facts是有原件摘录及发布记录的事实，不接受预填资格。实际字节及引用由
    导入入口核验；日期仅知公日时采用次日，未知/计划/过期事实不参与市值。
    """
    day = date.fromisoformat(signal_day).isoformat()
    mask = (facts["fact_kind"].eq("actual_completed")
            & facts["published_date"].fillna("").astype(str).ne("")
            & facts["published_date"].astype(str).lt(day)
            & facts["valid_from"].astype(str).le(day)
            & facts["valid_to"].fillna("").astype(str).ne("")
            & facts["valid_to"].astype(str).ge(day)
            & facts["evidence_ref"].fillna("").astype(str).ne("")
            & facts["publication_ref"].fillna("").astype(str).ne(""))
    chosen = facts[mask].copy()
    if len(chosen) and chosen.groupby("code")["total_shares"].nunique().gt(1).any():
        raise ValueError("ambiguous actual capital versions")
    return chosen.sort_values("published_date").drop_duplicates("code", keep="last")

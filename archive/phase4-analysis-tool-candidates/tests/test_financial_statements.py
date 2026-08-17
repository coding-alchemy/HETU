from __future__ import annotations

import pytest
from financial_statements import normalize_finance_report


def test_normalize_finance_report_orders_periods_and_preserves_values() -> None:
    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20251231": {
                        "data": [
                            {
                                "item_title": "营业收入",
                                "item_value": "39353112419.78",
                                "item_tongbi": "0.3085",
                            }
                        ]
                    },
                    "20260331": {
                        "data": [
                            {
                                "item_title": "营业收入",
                                "item_value": "10322863908.82",
                                "item_tongbi": "0.2580",
                            },
                            {
                                "item_title": "营业成本",
                                "item_value": "6113944336.44",
                            },
                        ]
                    },
                }
            }
        }
    }

    rows = normalize_finance_report(payload, limit=2)

    assert rows == [
        {
            "报告期": "2026-03-31",
            "营业收入": "10322863908.82",
            "营业收入_同比": "0.2580",
            "营业成本": "6113944336.44",
        },
        {
            "报告期": "2025-12-31",
            "营业收入": "39353112419.78",
            "营业收入_同比": "0.3085",
        },
    ]


def test_normalize_finance_report_rejects_missing_report_list() -> None:
    with pytest.raises(ValueError, match="report_list"):
        normalize_finance_report({"result": {"data": {}}})

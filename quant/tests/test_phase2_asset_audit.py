"""离线增量核算的最小事实，不读取本机运行资产。"""
import runpy
from pathlib import Path


def test_actions_increment_subtracts_only_operate_coverage():
    ns = runpy.run_path(str(Path(__file__).parents[1]/'probes/phase2_review_assets.py'))
    facts = [dict(basis='report_year', start='2023-01-01', end='2026-12-31'),
             dict(basis='ex_date', start='2023-01-01', end='2023-12-31'),
             dict(basis='ex_date', start='2024-01-01', end='2024-12-31', failed=True)]
    gaps = ns['action_gaps']('2023-01-01', '2026-10-13', facts)
    assert gaps == [['2024-01-01', '2026-10-13']]
    counts = ns['request_counts'](gaps)
    assert counts['provider_fetch'] == 1
    assert counts['upstream_product_queries'] == 3
    assert ns['request_counts']([['2023-01-01', '2026-10-13']])['upstream_product_queries'] == 4

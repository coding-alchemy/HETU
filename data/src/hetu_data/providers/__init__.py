"""来源适配器注册。"""

from __future__ import annotations


def default_providers() -> dict[str, object]:
    from hetu_data.providers.akshare import AKShareProvider
    from hetu_data.providers.baostock import BaoStockProvider
    from hetu_data.providers.local_file import LocalFileProvider
    from hetu_data.providers.tushare import TushareProvider

    return {
        "baostock": BaoStockProvider(),
        "akshare": AKShareProvider(),
        "local_file": LocalFileProvider(),
        "tushare": TushareProvider(),
    }

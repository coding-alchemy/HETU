"""来源适配器注册。"""

from __future__ import annotations


def default_providers() -> dict[str, object]:
    from hetu_quant.providers.akshare import AKShareProvider
    from hetu_quant.providers.baostock import BaoStockProvider
    from hetu_quant.providers.local_file import LocalFileProvider

    return {
        "baostock": BaoStockProvider(),
        "akshare": AKShareProvider(),
        "local_file": LocalFileProvider(),
    }

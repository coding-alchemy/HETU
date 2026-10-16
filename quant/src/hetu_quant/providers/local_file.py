"""合法免费本地文件来源：无网络入口，文件经 DataStore.import_files 准入。"""

from __future__ import annotations

from datetime import date

import pandas as pd


class LocalFileProvider:
    name = "local_file"

    def fetch(
        self, product_id: str, codes: tuple[str, ...], start: date, end: date
    ) -> pd.DataFrame:
        raise NotImplementedError(
            f"{product_id}: local_file 产品只支持 import_files 入口，无网络 fetch"
        )

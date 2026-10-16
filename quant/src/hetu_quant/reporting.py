"""03.3 只读导出：已发布结果转 ZIP（定义／来源／限制 JSON、各表 CSV、研究卡 Markdown）。

导出不重新计算、不改动已发布产物；改变导出路径只产生新的导出文件。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from hetu_quant.models import ResultRef


def _cards_markdown(cards: pd.DataFrame) -> str:
    lines = ["# 研究卡", ""]
    for _, row in cards.iterrows():
        group = str(row["group_id"]) if str(row["group_id"]) else "板块"
        lines.append(f"## {row['entity_id']}（{group}）")
        lines.append(f"- Gate 状态：{row['gate_state']}")
        lines.append(f"- 入选依据（原值／贡献）：{row['rationale']}")
        lines.append(f"- 反方解释：{row['counter_evidence']}")
        lines.append(f"- 缺口与限制：{row['limitations']}")
        lines.append(f"- 下一项核验：{row['next_check']}")
        lines.append(f"- 线索状态：{row['clue_types']}")
        lines.append("")
    return "\n".join(lines)


def export_result(result: ResultRef, destination: Path) -> Path:
    """把 ``results/<result_id>/`` 的已发布产物导出为 ZIP，返回 ZIP 路径。

    ``destination`` 为目录时写入 ``<result_id>.zip``；只读已发布资产。
    """
    result_dir = result.root / "results" / result.result_id
    manifest_path = result_dir / "result.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"result manifest missing: {result.result_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination = Path(destination)
    zip_path = (
        destination
        if destination.suffix == ".zip"
        else destination / f"{result.result_id}.zip"
    )
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "definition.json",
            json.dumps(manifest["definition"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "sources.json",
            json.dumps(manifest["sources"], ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "limitations.json",
            json.dumps(manifest["limitations"], ensure_ascii=False, indent=2),
        )
        for name in manifest["tables"]:
            table_path = result_dir / f"{name}.parquet"
            if not table_path.exists():
                raise FileNotFoundError(f"result table missing: {name}")
            frame = pd.read_parquet(table_path)
            zf.writestr(f"{name}.csv", frame.to_csv(index=False))
        cards_path = result_dir / "cards.parquet"
        cards = pd.read_parquet(cards_path) if cards_path.exists() else pd.DataFrame()
        zf.writestr("cards.md", _cards_markdown(cards))
    return zip_path

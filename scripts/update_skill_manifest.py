import json
from pathlib import Path

from hetu_stock.skill import build_skill_manifest

ROOT = Path("skills/hetu-stock-analysis")
ROOT.joinpath("MANIFEST.json").write_text(
    json.dumps(build_skill_manifest(ROOT), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

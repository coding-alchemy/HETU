from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures/material_cases"


def _render(root: Path, references: list[list[str]]) -> str:
    parts = []
    for relative, key in references:
        if relative != "shared.md" and not relative.startswith("inputs/"):
            raise KeyError(relative)
        text = (root / relative).read_text(encoding="utf-8")
        marker = f"<!-- fixture:{key} -->\n"
        if text.count(marker) != 1:
            raise KeyError((relative, key))
        parts.append(text.split(marker, 1)[1].split("\n<!-- fixture:", 1)[0].strip())
    return "\n\n".join(parts) + ("\n" if parts else "")


def materialize_input(
    case: str,
    variant: str,
    stage: str,
    output: Path,
    *,
    root: Path = ROOT,
) -> tuple[Path, ...]:
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    key = f"{case}/{variant}/{stage}"
    entry = catalog[key]
    task = _render(root, entry["task"])
    material = _render(root, entry["material"])
    selection = {
        "case": case, "variant": variant, "stage": stage,
        "context": entry["context"], "as_of": entry["as_of"],
    }
    output.mkdir(parents=True, exist_ok=False)
    paths = []
    for name, text in (("task.md", task), ("material.md", material)):
        path = output / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    selected = output / "selection.json"
    selected.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    paths.append(selected)
    for relative, name in entry["assets"]:
        target = output / name
        shutil.copyfile(root / relative, target)
        paths.append(target)
    return tuple(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description="仅生成指定合成场景的当前输入")
    for name in ("case", "variant", "stage", "output"):
        parser.add_argument(name)
    args = parser.parse_args()
    for path in materialize_input(args.case, args.variant, args.stage, Path(args.output)):
        print(path.resolve())


if __name__ == "__main__":
    main()

"""显式数据根上的离线规划和查询。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from hetu_data.models import SnapshotRef, data_request_from_dict
from hetu_data.store import DataStore
from hetu_data.updates import plan_update


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hetu-data")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    query = commands.add_parser("query")
    for command in (plan, query):
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    plan.add_argument("--request", type=Path, required=True)
    plan.add_argument("--contracts", type=Path, required=True)
    query.add_argument("--snapshot", required=True)
    query.add_argument("--codes", required=True)
    query.add_argument("--start", type=date.fromisoformat, required=True)
    query.add_argument("--end", type=date.fromisoformat, required=True)
    query.add_argument("--columns", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--root", type=Path, required=True)
    worker.add_argument("--once", action="store_true")
    web = commands.add_parser("web")
    web.add_argument("--root", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--root", type=Path, required=True)
    submit.add_argument("--plan", type=Path, required=True)
    submit.add_argument("--submission-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "web":
        try:
            from streamlit.web import cli as streamlit_cli
        except ImportError:
            print("请安装 hetu-data[web] 后启动网页", file=sys.stderr)
            return 1
        previous = sys.argv
        sys.argv = ["streamlit", "run", str(Path(__file__).parent / "ui" / "app.py"),
                    "--", "--root", str(args.root.resolve())]
        try:
            return streamlit_cli.main()
        finally:
            sys.argv = previous
    if args.command == "worker":
        from hetu_data.worker import main as worker_main
        return worker_main(["--root", str(args.root)] + (["--once"] if args.once else []))
    try:
        store = DataStore(args.root)
        if args.command == "submit":
            from hetu_data.catalog import DataTaskStore
            from hetu_data.jobs import submit_update_plan
            job = submit_update_plan(DataTaskStore(args.root), json.loads(args.plan.read_text()), args.submission_id)
            print(json.dumps(dict(definition_id=job.definition_id, job_id=job.job_id)))
        elif args.command == "plan":
            request = data_request_from_dict(json.loads(args.request.read_text()))
            result = plan_update(request, store, json.loads(args.contracts.read_text()))
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        else:
            frame = store.read_range(
                SnapshotRef(args.snapshot, args.root),
                "daily_bars",
                codes=tuple(args.codes.split(",")),
                start=args.start,
                end=args.end,
                columns=tuple(args.columns.split(",")),
            )
            frame.to_csv(args.output, index=False)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"hetu-data: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

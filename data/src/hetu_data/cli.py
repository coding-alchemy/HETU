"""显式数据根上的离线规划和查询。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from hetu_data.library import list_libraries, query_library
from hetu_data.models import LibraryRef, SnapshotRef, data_request_from_dict
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
    source = query.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot")
    source.add_argument("--library")
    query.add_argument("--codes", required=True)
    query.add_argument("--start", type=date.fromisoformat, required=True)
    query.add_argument("--end", type=date.fromisoformat, required=True)
    query.add_argument("--columns", required=True)
    query.add_argument("--markets", default="")
    query.add_argument("--predicates", type=json.loads, default=[])
    libraries = commands.add_parser("libraries")
    libraries.add_argument("--root", type=Path, required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--root", type=Path, required=True)
    worker.add_argument("--once", action="store_true")
    web = commands.add_parser("web")
    web.add_argument("--root", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--root", type=Path, required=True)
    submit.add_argument("--plan", type=Path, required=True)
    submit.add_argument("--submission-id", required=True)
    backup = commands.add_parser("backup").add_subparsers(dest="backup_command", required=True)
    for action in ("plan", "run", "verify", "restore"):
        step = backup.add_parser(action)
        if action in {"plan", "run"}:
            step.add_argument("--root", type=Path, required=True)
        if action != "run":
            step.add_argument("--repository", type=Path, required=True)
        if action in {"verify", "restore"}:
            step.add_argument("--point", required=True)
        if action == "restore":
            step.add_argument("--destination", type=Path, required=True)
        elif action == "run":
            step.add_argument("--plan", type=Path, required=True)
        elif action == "plan":
            step.add_argument("--output", type=Path, required=True)
            step.add_argument("--library")
            step.add_argument("--exclude-raw-originals", action="store_true")
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
        if args.command == "backup":
            from hetu_data import packed_backup

            if args.backup_command == "verify":
                result = packed_backup.verify_packed_backup(args.repository, args.point)
            elif args.backup_command == "restore":
                result = packed_backup.restore_packed_backup(args.repository, args.point, args.destination)
            elif args.backup_command == "plan":
                path = packed_backup.plan_packed_backup(
                    DataStore(args.root), args.repository, output=args.output, library_id=args.library,
                    exclude_raw_originals=args.exclude_raw_originals,
                )
                result = {"plan": str(path)}
            else:
                result = packed_backup.execute_packed_backup(DataStore(args.root), args.plan)
            print(json.dumps(result, ensure_ascii=False))
            return 0
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
        elif args.command == "libraries":
            print(json.dumps(list_libraries(store), ensure_ascii=False))
        elif args.library:
            result = query_library(
                store, LibraryRef(args.library, args.root),
                codes=tuple(args.codes.split(",")), markets=tuple(args.markets.split(",")) if args.markets else (),
                start=args.start, end=args.end, columns=tuple(args.columns.split(",")),
                predicates=tuple(args.predicates),
            )
            result["bars"].to_csv(args.output, index=False)
        else:
            if args.markets or args.predicates:
                raise ValueError("--markets and --predicates require --library")
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

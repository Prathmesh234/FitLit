#!/usr/bin/env python3
"""Entry point for every personal task — the thing cron and systemd call.

    uv run python -m personal.runner list
    uv run python -m personal.runner run coffee [--dry-run|--force|--no-send]
    uv run python -m personal.runner status [coffee]
    uv run python -m personal.runner history coffee --limit 20
    uv run python -m personal.runner feedback "Victrola Coffee" disliked --note "too loud"

A kernel-held lock keeps two overlapping runs from producing two emails; the
per-day reservation in `personal.store` keeps a retried timer from doing the
same across restarts.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any

from fitlit.journal import PACIFIC
from personal import config, store
from personal.tasks import coffee

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger("fitlit.personal.runner")

TASKS: dict[str, Any] = {coffee.TASK: coffee}


def _lock():
    config.PERSONAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(config.PERSONAL_LOCK, "a+")
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
    return handle


def _unlock(handle) -> None:
    if handle is None:
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=PACIFIC)
    return moment.astimezone(PACIFIC)


def command_run(args: argparse.Namespace) -> int:
    module = TASKS[args.task]
    handle = _lock()
    if handle is None:
        _emit({"status": "busy", "task": args.task})
        return 0
    try:
        result = module.run(
            now=_parse_now(args.now),
            dry_run=args.dry_run,
            force=args.force,
            send=not args.no_send,
        )
    finally:
        _unlock(handle)
    _emit({"task": args.task, **vars(result)})
    return 0 if result.status in ("sent", "dry-run", "recorded", "already-sent", "disabled") else 1


def command_status(args: argparse.Namespace) -> int:
    names = [args.task] if args.task else list(TASKS)
    _emit({name: TASKS[name].status() for name in names})
    return 0


def command_history(args: argparse.Namespace) -> int:
    module = TASKS[args.task]
    _emit({args.task: module.history(limit=args.limit)})
    return 0


def command_feedback(args: argparse.Namespace) -> int:
    connection = store.connect()
    try:
        key = store.record_feedback(
            connection, args.shop, args.sentiment, args.note
        )
        _emit({
            "recorded": {
                "shop": args.shop,
                "shop_key": key,
                "sentiment": args.sentiment,
                "note": args.note,
            },
            "blocked_now": store.blocked_shops(connection),
        })
    except store.PersonalStoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    return 0


def command_list(_: argparse.Namespace) -> int:
    _emit({
        "tasks": [
            {
                "name": name,
                "module": module.__name__,
                "summary": (module.__doc__ or "").strip().splitlines()[0],
            }
            for name, module in TASKS.items()
        ]
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal.runner", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every registered personal task")

    run_parser = sub.add_parser("run", help="run one personal task")
    run_parser.add_argument("task", choices=sorted(TASKS))
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="research and render without recording or sending",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="run even if today's slot was already used",
    )
    run_parser.add_argument(
        "--no-send",
        action="store_true",
        help="record the result but do not email it",
    )
    run_parser.add_argument("--now", help="override the Pacific timestamp (ISO)")

    status_parser = sub.add_parser("status", help="show task configuration and recent runs")
    status_parser.add_argument("task", nargs="?", choices=sorted(TASKS))

    history_parser = sub.add_parser("history", help="show a task's past results")
    history_parser.add_argument("task", choices=sorted(TASKS))
    history_parser.add_argument("--limit", type=int, default=20)

    feedback_parser = sub.add_parser(
        "feedback", help="record the owner's verdict on one coffee shop"
    )
    feedback_parser.add_argument("shop")
    feedback_parser.add_argument("sentiment", choices=store.SENTIMENTS)
    feedback_parser.add_argument("--note", default=None)
    return parser


_HANDLERS = {
    "list": command_list,
    "run": command_run,
    "status": command_status,
    "history": command_history,
    "feedback": command_feedback,
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

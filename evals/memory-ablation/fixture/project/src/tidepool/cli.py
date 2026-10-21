"""Command-line entry points."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(".tidepool") / "last-run.json"


def sync(source: str, out: Path) -> int:
    """Pull the bookmark list, render the page, record the run."""
    items = _pull(source)
    out.write_text(_render(items), encoding="utf-8")
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(
        json.dumps({"when": datetime.now(timezone.utc).isoformat(), "count": len(items)}),
        encoding="utf-8",
    )
    return 0


def status() -> int:
    """Print the last run, or say there was none."""
    if not STATE.exists():
        print("no run recorded")
        return 1
    print(STATE.read_text(encoding="utf-8"))
    return 0


def _pull(source: str) -> list[dict[str, str]]:
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _render(items: list[dict[str, str]]) -> str:
    rows = "\n".join(f"- [{i['title']}]({i['url']})" for i in items)
    return f"# Bookmarks\n\n{rows}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidepool")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--source", default="bookmarks.json")
    s.add_argument("--out", default="status/index.md")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.cmd == "sync":
        return sync(args.source, Path(args.out))
    return status()

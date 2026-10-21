"""Upload the rendered status page to a storage bucket."""

from __future__ import annotations

import argparse
import os
import sys

DEFAULT_BUCKETS = {
    "staging": "tidepool-staging",
    "production": "tidepool-prod",
}


def bucket_for(target: str) -> str:
    """The bucket name for a target; the environment may override it."""
    return os.environ.get("TIDEPOOL_BUCKET") or DEFAULT_BUCKETS[target]


def upload(page: str, bucket: str) -> None:
    """Placeholder for the storage client call."""
    raise NotImplementedError(f"upload {page} to {bucket}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidepool-deploy")
    parser.add_argument("--target", choices=sorted(DEFAULT_BUCKETS), required=True)
    parser.add_argument("--page", default="status/index.md")
    args = parser.parse_args(argv)
    try:
        upload(args.page, bucket_for(args.target))
    except Exception as exc:  # noqa: BLE001
        print(f"deploy failed: {exc}", file=sys.stderr)
        return 1
    return 0

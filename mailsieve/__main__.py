"""Local CLI: preview or run mailsieve against your inbox.

uv run python -m mailsieve classify --dry-run --query "in:inbox newer_than:14d"
uv run python -m mailsieve classify --all --workers 12   # backfill every unseen email (resumable)
uv run python -m mailsieve sweep --dry-run
uv run python -m mailsieve login   # mint .secrets/token.json
"""

import argparse
import collections
import os
from pathlib import Path

from . import config, engine, gmail


def load_env() -> None:
    env = Path(__file__).parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    p = argparse.ArgumentParser(prog="mailsieve")
    p.add_argument("command", choices=["login", "classify", "sweep", "watch"])
    p.add_argument("--dry-run", action="store_true", help="decide and log, change nothing")
    p.add_argument("--query", help="Gmail search to classify instead of new unseen inbox mail")
    p.add_argument("--all", action="store_true", help="every unseen email, not just recent inbox mail")
    p.add_argument("--limit", type=int, help="max messages (default 200, unlimited with --all)")
    p.add_argument("--workers", type=int, default=1)
    args = p.parse_args()
    load_env()
    if args.command == "login":
        return gmail.login()
    cfg, svc = config.load(), gmail.service()
    if args.command == "classify":
        query = args.query or ("-in:chats" if args.all else None)
        limit = args.limit or (None if args.all else 200)
        results = engine.classify(svc, cfg, query, limit, args.dry_run, args.workers)
        print(collections.Counter(r["rule"] for r in results))
    elif args.command == "sweep":
        print(engine.sweep(svc, cfg, args.dry_run))
    else:
        engine.watch(svc)


if __name__ == "__main__":
    main()

"""Command line interface.

    python -m jobs_agent fetch --user <supabase-user-id>   # pull, score, dedupe, store
    python -m jobs_agent queue --user <supabase-user-id>   # show the review queue
    python -m jobs_agent stats --user <supabase-user-id>
    python -m jobs_agent serve                             # web UI over the above

Data is segregated per account (see storage/store.py), so fetch/queue/stats
need to know which account's data to operate on — pass its Supabase Auth
user id with ``--user`` (Supabase dashboard -> Authentication -> Users ->
copy the UID). ``serve`` doesn't take one: each request is scoped to
whoever is signed in in the browser.

Nothing in this tool submits an application. It ranks and stages; a human
approves and submits.

This module is argument parsing and console output only — the work itself
lives in pipeline.py and the packages it calls, so the web server can reuse
it without importing the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import KEYWORDS
from .pipeline import fetch_and_store
from .profile import load_profile
from .sources import NoSourcesConfigured
from .storage import open_store


def cmd_fetch(args) -> None:
    with open_store(args.db, user_id=args.user) as store:
        search_terms = list(load_profile(store).target_titles) or KEYWORDS
        print(f"Fetching {len(search_terms)} keywords from every configured board...")
        try:
            result = asyncio.run(
                fetch_and_store(store, per_keyword=args.per_keyword)
            )
        except NoSourcesConfigured as e:
            sys.exit(str(e))

    for warning in result.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    print(f"  {result.raw} raw postings")
    print(f"  {result.kept} passed filters ({result.excluded} excluded)")
    print(f"  {result.new} new, {result.duplicates} duplicates suppressed")


def cmd_queue(args) -> None:
    with open_store(args.db, user_id=args.user) as store:
        rows = list(store.queue(min_score=args.min_score, limit=args.limit,
                                status=args.status, location=args.location))
    if not rows:
        print("Queue empty.")
        return
    for r in rows:
        print(f"\n[{r['score']:>3}] {r['title']}")
        print(f"      {r['employer']}  ·  {r['location']}  ·  {r['contract_type'] or 'n/a'}")
        sal = (f"£{r['salary_min']:,.0f}" if r["salary_min"] else "not stated")
        print(f"      salary {sal}  ·  posted {r['posted'] or '?'}  ·  {r['source']}")
        print(f"      {r['score_reasons']}")
        print(f"      {r['url']}")
    print(f"\n{len(rows)} shown.")


def cmd_stats(args) -> None:
    with open_store(args.db, user_id=args.user) as store:
        counts = store.stats()
    for status, count in sorted(counts.items()):
        print(f"{status:>12}  {count}")


def cmd_serve(args) -> None:
    from .web import serve

    serve(db=args.db, port=args.port, open_browser=not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jobs_agent")
    ap.add_argument("--db", default=None,
                    help="Postgres connection string (default: DATABASE_URL "
                         "from the environment/.env)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="pull, score, dedupe, and store postings")
    f.add_argument("--user", required=True, help="Supabase Auth user id to fetch for")
    f.add_argument("--per-keyword", type=int, default=200)
    f.set_defaults(func=cmd_fetch)

    q = sub.add_parser("queue", help="print the review queue")
    q.add_argument("--user", required=True, help="Supabase Auth user id whose queue to print")
    q.add_argument("--min-score", type=int, default=30)
    q.add_argument("--limit", type=int, default=25)
    q.add_argument("--status", default="new")
    q.add_argument("--location", default="",
                   help="case-insensitive substring match against posting "
                        "location (default: anywhere)")
    q.set_defaults(func=cmd_queue)

    s = sub.add_parser("stats", help="count applications by status")
    s.add_argument("--user", required=True, help="Supabase Auth user id to count for")
    s.set_defaults(func=cmd_stats)

    w = sub.add_parser("serve", help="run the local review web UI")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-browser", action="store_true",
                   help="don't auto-open the UI in a browser tab")
    w.set_defaults(func=cmd_serve)

    return ap


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(format="  ! %(message)s", level=logging.WARNING,
                        stream=sys.stderr)
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

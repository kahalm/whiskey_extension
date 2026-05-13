#!/usr/bin/env python3
"""Whiskybase Scraper — 2-phase approach:
  Phase 1 (search):  Bulk-collect WBIDs + basic data via search queries
  Phase 2 (detail):  Crawl individual pages for full details
"""

import argparse
import asyncio
import logging
import sys

from scraper.crawler import run_search_collector, run_detail_crawler, run_releases_collector
from scraper.db import get_connection, init_db, get_last_wbid, get_whisky_count, get_detail_count, get_search_state, get_all_releases_states


def cmd_releases(args):
    """Phase 1b: Collect WBIDs via new-releases filter."""
    asyncio.run(
        run_releases_collector(
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            headless=not args.visible,
            votes=args.votes,
        )
    )


def cmd_search(args):
    """Phase 1: Collect WBIDs via search."""
    asyncio.run(
        run_search_collector(
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            headless=not args.visible,
        )
    )


def cmd_detail(args):
    """Phase 2: Crawl individual pages."""
    asyncio.run(
        run_detail_crawler(
            start_wbid=args.start,
            end_wbid=args.end,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            headless=not args.visible,
        )
    )


def cmd_status(args):
    conn = get_connection()
    init_db(conn)
    count = get_whisky_count(conn)
    detail = get_detail_count(conn)
    last_wbid = get_last_wbid(conn)
    last_query = get_search_state(conn)
    releases_states = get_all_releases_states(conn)
    conn.close()
    print(f"Whiskies in DB:      {count}")
    print(f"  with full details: {detail}")
    print(f"  basic only:        {count - detail}")
    if releases_states:
        for rs in releases_states:
            print(f"Releases [{rs['filter_key']}]: last year {rs['last_year']}")
    else:
        print(f"Releases:            not started")
    print(f"Last search query:   '{last_query}'")
    print(f"Last detail WBID:    {last_wbid}")


def cmd_reset_releases(args):
    conn = get_connection()
    init_db(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM releases_state")
    conn.commit()
    conn.close()
    print("Releases state reset. Next run starts from 2026.")


def cmd_reset(args):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS whiskies")
        cur.execute("DROP TABLE IF EXISTS scrape_state")
        cur.execute("DROP TABLE IF EXISTS search_state")
        cur.execute("DROP TABLE IF EXISTS releases_state")
    conn.commit()
    conn.close()
    print("All tables dropped.")


def main():
    parser = argparse.ArgumentParser(description="Whiskybase Scraper")
    sub = parser.add_subparsers(dest="command")

    # releases command (Phase 1b — default)
    p_releases = sub.add_parser("releases", help="Collect WBIDs via new-releases filter (year by year)")
    p_releases.add_argument("--delay-min", type=float, default=2.0, help="Min delay (seconds)")
    p_releases.add_argument("--delay-max", type=float, default=5.0, help="Max delay (seconds)")
    p_releases.add_argument("--visible", action="store_true", help="Show browser window")
    p_releases.add_argument("--votes", type=str, default="", help="Filter by votes (e.g. '0' for unrated)")
    p_releases.set_defaults(func=cmd_releases)

    # search command (Phase 1)
    p_search = sub.add_parser("search", help="Phase 1: Collect WBIDs via search queries")
    p_search.add_argument("--delay-min", type=float, default=2.0, help="Min delay (seconds)")
    p_search.add_argument("--delay-max", type=float, default=5.0, help="Max delay (seconds)")
    p_search.add_argument("--visible", action="store_true", help="Show browser window")
    p_search.set_defaults(func=cmd_search)

    # detail command (Phase 2)
    p_detail = sub.add_parser("detail", help="Phase 2: Crawl individual pages for full details")
    p_detail.add_argument("--start", type=int, default=None, help="Start WBID (default: resume)")
    p_detail.add_argument("--end", type=int, default=400000, help="End WBID")
    p_detail.add_argument("--delay-min", type=float, default=2.0, help="Min delay (seconds)")
    p_detail.add_argument("--delay-max", type=float, default=5.0, help="Max delay (seconds)")
    p_detail.add_argument("--visible", action="store_true", help="Show browser window")
    p_detail.set_defaults(func=cmd_detail)

    # status
    p_status = sub.add_parser("status", help="Show scraping progress")
    p_status.set_defaults(func=cmd_status)

    # reset-releases
    p_reset_rel = sub.add_parser("reset-releases", help="Reset releases progress (re-scrape all years)")
    p_reset_rel.set_defaults(func=cmd_reset_releases)

    # reset
    p_reset = sub.add_parser("reset", help="Delete database and start fresh")
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["releases"])

    args.func(args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    main()

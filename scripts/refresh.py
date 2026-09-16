"""
One command to re-read everything from the live website.

The site is not static - stock, prices and filter options change - so this is the job
that keeps the agent current. Run it on a schedule (see .github/workflows/refresh.yml)
or by hand whenever the site changes.

Steps:
  1. fetch_facets       - the search filter options the site currently offers
  2. crawl_inventory    - every vehicle in the live stock list
  3. add_body_types     - tag each vehicle with its body type
  4. build_stock_summary- regenerate the stock overview knowledge-base page

    python scripts/refresh.py                # refresh the data files only
    python scripts/refresh.py --deploy       # then push the result to Retell
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

STEPS = [
    ("reading the site's filter options", "fetch_facets.py"),
    ("crawling the live stock list", "crawl_inventory.py"),
    ("tagging body types", "add_body_types.py"),
    ("rebuilding the stock overview", "build_stock_summary.py"),
]


def run(script, extra=()):
    cmd = [sys.executable, os.path.join(HERE, script), *extra]
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"{script} failed with exit code {proc.returncode}")


def summarise():
    inv_path = os.path.join(ROOT, "data", "inventory.json")
    with open(inv_path, encoding="utf-8") as fh:
        inv = json.load(fh)
    reported = inv.get("total_reported")
    count = inv.get("count", 0)

    print("\n" + "=" * 62)
    print(f"  stock snapshot: {count} vehicles")
    if reported and count < reported:
        print(f"  note: the site reported {reported}; {reported - count} could not be "
              f"read from the pager")
    prices = [v["price_usd"] for v in inv["vehicles"] if v.get("price_usd")]
    if prices:
        print(f"  price range:    ${min(prices):,.0f} - ${max(prices):,.0f} FOB")
    untagged = [v for v in inv["vehicles"] if not v.get("body_type")]
    if untagged:
        print(f"  warning: {len(untagged)} vehicles have no body type")
    print(f"  crawled at:     {inv.get('crawled_at')}")
    print("=" * 62)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true",
                    help="push the refreshed data to Retell afterwards")
    args = ap.parse_args()

    for n, (label, script) in enumerate(STEPS, 1):
        print(f"\n[{n}/{len(STEPS)}] {label} ...")
        run(script)

    summarise()

    if args.deploy:
        print("\n[deploy] pushing to Retell ...")
        run("deploy_retell.py")
    else:
        print("\nData refreshed. Commit data/ so the agent's stock lookup picks it up,")
        print("or run with --deploy to also update the Retell knowledge base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

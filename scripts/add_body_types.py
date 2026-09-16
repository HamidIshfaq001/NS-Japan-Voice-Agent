"""
Tag each vehicle in data/inventory.json with its body_type.

The stock grid does not expose body type, but the site's search filter does. So we run
one search per body type and record which stock IDs come back.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawl_inventory import (  # noqa: E402
    RESULTS_URL, SEARCH_URL, UA, hidden_fields, parse_page, total_results,
)

import requests  # noqa: E402

INV = os.path.join(os.path.dirname(__file__), "..", "data", "inventory.json")
FACETS = os.path.join(os.path.dirname(__file__), "..", "data", "facets.json")


def body_types():
    """Read the body types the site currently offers, not a frozen list."""
    if not os.path.exists(FACETS):
        raise SystemExit(
            "data/facets.json is missing. Run scripts/fetch_facets.py first."
        )
    with open(FACETS, encoding="utf-8") as fh:
        return json.load(fh)["body_types"]


def search_body_type(session, body_type):
    home = session.get(SEARCH_URL, timeout=60).text
    f = hidden_fields(home)
    payload = {
        "__EVENTTARGET": "btnSearch", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "__VIEWSTATE": f.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": f.get("__VIEWSTATEGENERATOR", ""),
        "d_makes": "Make", "d_type": body_type, "d_year1": "Year", "d_year2": "Year",
        "d_model": "Model", "d_steer": "Steering", "d_price": "Price",
        "d_transmission": "Transmission", "d_fuel": "Fuel", "d_mileage": "Mileage",
        "txt_search": "",
    }
    session.post(SEARCH_URL, data=payload, allow_redirects=False,
                 headers={"Referer": SEARCH_URL}, timeout=60)
    page_html = session.get(RESULTS_URL, timeout=60).text
    total = total_results(page_html)

    ids, page = set(), 1
    while True:
        found = parse_page(page_html)
        new = {v["stock_id"] for v in found} - ids
        ids |= new
        if (total and len(ids) >= total) or not found or not new:
            break
        page += 1
        f = hidden_fields(page_html)
        nxt = {
            "__EVENTTARGET": "GridView1", "__EVENTARGUMENT": "Page$%d" % page,
            "__LASTFOCUS": "", "__VIEWSTATE": f.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": f.get("__VIEWSTATEGENERATOR", ""),
        }
        page_html = session.post(RESULTS_URL, data=nxt,
                                 headers={"Referer": RESULTS_URL}, timeout=60).text
        if page > 60:
            break
        time.sleep(0.3)
    return total, ids


def main():
    with open(INV, encoding="utf-8") as fh:
        snap = json.load(fh)

    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    mapping = {}
    for bt in body_types():
        total, ids = search_body_type(s, bt)
        print(f"{bt:18} reported={total} collected={len(ids)}", flush=True)
        for sid in ids:
            mapping[sid] = bt
        time.sleep(0.3)

    tagged = 0
    for v in snap["vehicles"]:
        bt = mapping.get(v["stock_id"])
        if bt:
            v["body_type"] = bt
            tagged += 1
    snap["body_types_tagged"] = tagged

    with open(INV, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, ensure_ascii=False)
    print(f"tagged {tagged}/{len(snap['vehicles'])} vehicles with a body type")


if __name__ == "__main__":
    sys.exit(main())

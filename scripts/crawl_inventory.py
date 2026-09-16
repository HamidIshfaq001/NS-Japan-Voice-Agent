"""
Crawl the full NS Japan Autos live stock list into data/inventory.json.

The site is ASP.NET WebForms: the search form posts back to "/" which 302s to
/listings_search/, and the result grid pages via __doPostBack('GridView1','Page$N').
So we keep one session, post the search once, then walk the pager.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import requests

BASE = "https://nsjapanautos.com"
SEARCH_URL = BASE + "/"
RESULTS_URL = BASE + "/listings_search/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "inventory.json")


def hidden_fields(markup):
    fields = {}
    for m in re.finditer(r"<input[^>]*type=\"hidden\"[^>]*>", markup):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return fields


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\ufffd", "'")
    return re.sub(r"[\s\xa0]+", " ", text).strip()


def label_value(row_html):
    """Pull 'Label value' pairs out of one grid cell table."""
    cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S)]
    return [c for c in cells if c]


def parse_page(markup):
    """Return a list of vehicle dicts from one results page."""
    vehicles = []
    # Each result row: <a href='../motor_info/Default.aspx?val=NSxxxxx'> ... <span id=GridView1_stk_N>
    blocks = re.split(r"<a href='\.\./motor_info/Default\.aspx\?val=", markup)[1:]
    for block in blocks:
        stock_m = re.match(r"(NS\d+)", block)
        if not stock_m:
            continue
        stock = stock_m.group(1)
        body = block.split("</a>")[0]

        title_m = re.search(
            r'font-size:20px;">(.*?)</td>', body, flags=re.S
        )
        title = clean(title_m.group(1)) if title_m else ""

        cells = label_value(body)
        rec = {"stock_id": stock, "title": title}

        # Summary strip: Mileage / Year / Engine / Transmission / Location / Price
        for cell in cells:
            m = re.match(r"^(Mileage|Year|Engine|Transmission|Location)\s+(.+)$", cell)
            # "Engine Code" is a spec-table label, not the summary "Engine 2400CC" value.
            if m and not (m.group(1) == "Engine" and m.group(2).strip() == "Code"):
                rec.setdefault(m.group(1).lower(), m.group(2).strip())
            price = re.match(r"^Price\s*:\s*\$\s*([\d,\.]+)", cell)
            if price:
                rec["price_usd"] = float(price.group(1).replace(",", ""))

        # Spec table is emitted as alternating label/value cells
        labels = {
            "Make": "make", "Model": "model", "Steering": "steering", "Fuel": "fuel",
            "Color": "color", "Seats": "seats", "Doors": "doors",
            "Engine Code": "engine_code", "Stock": "stock", "M3": "m3",
            "Weight": "weight", "Chassis": "chassis",
        }
        for i, cell in enumerate(cells):
            if cell in labels and i + 1 < len(cells):
                nxt = cells[i + 1]
                if nxt not in labels:
                    rec[labels[cell]] = nxt
        rec.pop("stock", None)

        # The site prints mileage in THOUSANDS of km but labels the field "km"
        # (a 2009 Hiace listed as "596" is a 596,000 km van, priced accordingly).
        # Keep what was published and expose the real figure alongside it.
        if str(rec.get("mileage", "")).isdigit():
            rec["mileage_published"] = rec["mileage"]
            rec["mileage_km"] = int(rec["mileage"]) * 1000

        if not rec.get("year"):
            ym = re.match(r"^(\d{4})\s", title)
            if ym:
                rec["year"] = ym.group(1)
        rec["url"] = f"{BASE}/motor_info/Default.aspx?val={stock}"
        vehicles.append(rec)
    return vehicles


def total_results(markup):
    m = re.search(r'id="lbl_header">You have (\d+) Search Results', markup)
    return int(m.group(1)) if m else None


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    home = s.get(SEARCH_URL, timeout=60).text
    f = hidden_fields(home)

    payload = {
        "__EVENTTARGET": "btnSearch", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "__VIEWSTATE": f.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": f.get("__VIEWSTATEGENERATOR", ""),
        "d_makes": "Make", "d_type": "Body Type", "d_year1": "Year", "d_year2": "Year",
        "d_model": "Model", "d_steer": "Steering", "d_price": "Price",
        "d_transmission": "Transmission", "d_fuel": "Fuel", "d_mileage": "Mileage",
        "txt_search": "",
    }
    s.post(SEARCH_URL, data=payload, allow_redirects=False,
           headers={"Referer": SEARCH_URL}, timeout=60)

    page_html = s.get(RESULTS_URL, timeout=60).text
    total = total_results(page_html)
    print(f"total results reported: {total}", flush=True)

    all_v, seen = [], set()
    page = 1
    while True:
        found = parse_page(page_html)
        new = [v for v in found if v["stock_id"] not in seen]
        for v in new:
            seen.add(v["stock_id"])
        all_v.extend(new)
        print(f"page {page}: parsed {len(found)}, new {len(new)}, running {len(all_v)}", flush=True)

        if total and len(all_v) >= total:
            break
        if not found:
            break
        if not new:
            # Pager clamps at the last page and keeps returning it.
            print("no new records - reached last page", flush=True)
            break

        page += 1
        f = hidden_fields(page_html)
        nxt = {
            "__EVENTTARGET": "GridView1", "__EVENTARGUMENT": f"Page${page}", "__LASTFOCUS": "",
            "__VIEWSTATE": f.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": f.get("__VIEWSTATEGENERATOR", ""),
            "__EVENTVALIDATION": f.get("__EVENTVALIDATION", ""),
        }
        for k in ("d_makes", "d_type", "d_year1", "d_year2", "d_model", "d_steer",
                  "d_price", "d_transmission", "d_fuel", "d_mileage"):
            sel = re.search(r'<select name="%s".*?</select>' % k, page_html, flags=re.S)
            if sel:
                cur = re.search(r'<option selected="selected" value="([^"]*)"', sel.group(0))
                nxt[k] = cur.group(1) if cur else ""
        nxt["txt_search"] = ""

        r = s.post(RESULTS_URL, data=nxt, headers={"Referer": RESULTS_URL}, timeout=60)
        page_html = r.text
        if page > 60:
            print("pager guard hit", flush=True)
            break
        time.sleep(0.4)

    snapshot = {
        "source": BASE,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "total_reported": total,
        "count": len(all_v),
        "vehicles": all_v,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=1, ensure_ascii=False)
    print(f"wrote {len(all_v)} vehicles -> {os.path.abspath(OUT)}")


if __name__ == "__main__":
    sys.exit(main())

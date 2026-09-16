"""
Read the search filter options straight off nsjapanautos.com into data/facets.json.

Everything downstream - the body-type tagging pass, the enums in the agent's
search_inventory tool - is generated from this file rather than hardcoded, so when the
site adds a make, a body type or a fuel type the agent picks it up on the next refresh.

    python scripts/fetch_facets.py
"""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

BASE = "https://nsjapanautos.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "facets.json")

# select element name -> (key in facets.json, placeholder options to drop, required)
# d_model is filled in by the page only after a make is picked, so it is empty here.
SELECTS = {
    "d_makes": ("makes", {"make"}, True),
    "d_type": ("body_types", {"body type"}, True),
    "d_transmission": ("transmissions", {"transmission"}, True),
    "d_fuel": ("fuels", {"fuel type", "fuel"}, True),
    "d_steer": ("steering", {"steering", "all"}, True),
    "d_model": ("models", {"model"}, False),
}


def options_for(markup, select_name):
    m = re.search(r'<select name="%s".*?</select>' % re.escape(select_name),
                  markup, flags=re.S)
    if not m:
        return []
    out = []
    for value, _label in re.findall(
        r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(0), flags=re.S
    ):
        value = html.unescape(value).strip()
        if value:
            out.append(value)
    return out


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    markup = s.get(BASE + "/", timeout=60).text

    facets = {
        "source": BASE,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    missing = []
    for select_name, (key, placeholders, required) in SELECTS.items():
        values = [v for v in options_for(markup, select_name)
                  if v.lower() not in placeholders]
        facets[key] = values
        if not values and required:
            missing.append(select_name)
        print(f"{key:16} {len(values):>4} options")

    if missing:
        # The page layout changed. Fail loudly rather than silently shipping empty
        # enums, which would quietly remove filters from the voice agent.
        raise SystemExit(
            "could not read these filters off the homepage: " + ", ".join(missing) +
            "\nThe site markup has probably changed - check scripts/fetch_facets.py"
        )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(facets, fh, indent=1, ensure_ascii=False)
    print("wrote " + os.path.abspath(OUT))


if __name__ == "__main__":
    sys.exit(main())

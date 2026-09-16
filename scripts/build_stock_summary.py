"""Generate agent/knowledge-base/08-current-stock-overview.md from data/inventory.json."""
import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INV = os.path.join(HERE, "..", "data", "inventory.json")
OUT = os.path.join(HERE, "..", "agent", "knowledge-base", "08-current-stock-overview.md")


def main():
    with open(INV, encoding="utf-8") as fh:
        snap = json.load(fh)
    v = snap["vehicles"]
    prices = sorted(x["price_usd"] for x in v if x.get("price_usd"))

    makes = collections.Counter(x.get("make", "?") for x in v)
    bodies = collections.Counter(x.get("body_type", "?") for x in v)
    trans = collections.Counter(x.get("transmission", "?") for x in v)
    fuels = collections.Counter(x.get("fuel", "?") for x in v if x.get("fuel"))
    steer = collections.Counter(x.get("steering", "?") for x in v)
    years = collections.Counter(str(x.get("year", "?")) for x in v)

    bands = [(0, 2000), (2000, 4000), (4000, 6000), (6000, 10000), (10000, 10**9)]
    band_labels = ["Under $2,000", "$2,000 - $4,000", "$4,000 - $6,000",
                   "$6,000 - $10,000", "Over $10,000"]

    L = []
    L.append("# Current Stock Overview")
    L.append("")
    L.append(f"Snapshot of the live stock list on nsjapanautos.com, taken {snap['crawled_at'][:10]}.")
    L.append("")
    L.append("This is an overview for setting customer expectations only. For any question")
    L.append("about a specific vehicle, price or availability, always use the")
    L.append("search_inventory function - stock changes daily and this page will be out of date.")
    L.append("")
    L.append(f"## Total vehicles listed: {len(v)}")
    L.append("")
    L.append("## Price range")
    L.append(f"- Lowest listed vehicle: ${prices[0]:,.0f} FOB")
    L.append(f"- Highest listed vehicle: ${prices[-1]:,.0f} FOB")
    L.append(f"- Median vehicle: about ${prices[len(prices)//2]:,.0f} FOB")
    L.append("")
    L.append("Vehicles per price band:")
    for (lo, hi), label in zip(bands, band_labels):
        n = sum(1 for p in prices if lo <= p < hi)
        L.append(f"- {label}: {n}")
    L.append("")
    L.append("## Makes in stock")
    for mk, n in makes.most_common():
        L.append(f"- {mk}: {n}")
    L.append("")
    L.append("## Body types in stock")
    for bt, n in bodies.most_common():
        L.append(f"- {bt}: {n}")
    L.append("")
    L.append("## Most common models")
    models = collections.Counter(f"{x.get('make','?')} {x.get('model','?')}" for x in v)
    for md, n in models.most_common(20):
        L.append(f"- {md}: {n}")
    L.append("")
    L.append("## Other characteristics")
    L.append("- Registration years: " + ", ".join(
        f"{y} ({n})" for y, n in sorted(years.items())))
    L.append("- Transmission: " + ", ".join(f"{k} ({n})" for k, n in trans.most_common()))
    L.append("- Fuel: " + ", ".join(f"{k} ({n})" for k, n in fuels.most_common()))
    L.append("- Steering: " + ", ".join(f"{k} ({n})" for k, n in steer.most_common()))
    L.append("")
    L.append("Note: almost all Japanese export stock is right-hand drive. If a customer needs")
    L.append("left-hand drive, check with search_inventory before answering, and if we have")
    L.append("none, offer to have the team source one.")
    L.append("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

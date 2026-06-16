"""
End-to-end CLI test: paste a ucymachines listing URL -> classify -> ranked CSV.
Mirrors what the bot does (core.build_reply), printed for the terminal.

  python3 run.py <listing_url>
"""
import sys, re
from listing import parse_listing
from classify import classify
from match import match, export_csv

def run(url, out=None):
    info = parse_listing(url)
    prof = classify(info["title"], url=url)
    print(f"machine   : {info['title']}")
    print(f"brand     : {prof['brand']}")
    print(f"type      : {prof['category']}")
    print(f"terms     : {prof['terms']}")
    print(f"classifier: {'HAIKU (AI-optimized terms)' if prof['used_haiku'] else prof['recognized']}\n")
    rows = match(prof["brand"], prof["terms"], prof["category"])
    brand = sum(1 for r in rows if r["tier"] >= 4)
    typ = sum(1 for r in rows if r["tier"] == 3)
    print(f"{len(rows)} leads  |  {brand} inquired-brand  /  {typ} inquired-type\n")
    for r in rows[:12]:
        print(f"  T{r['tier']} [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if out:
        export_csv(rows, out)
    return prof, rows

if __name__ == "__main__":
    url = sys.argv[1]
    safe = url.rstrip("/").split("/")[-1][:40] or "leads"
    run(url, out=f"data/{safe}.csv")

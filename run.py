"""
End-to-end: paste a ucymachines listing URL -> ranked, deduped CSV of historic
leads who could buy it. This is the logic the Slack bot will call.

  python3 run.py <listing_url> [limit]
"""
import sys
from listing import parse_listing
from match import match, export_csv

def run(url, limit=1000, out=None):
    info = parse_listing(url)
    print(f"machine : {info['title']}")
    print(f"brand   : {info['brand']}")
    print(f"category: {info['category']}")
    print(f"terms   : {info['terms']}\n")
    rows = match(info["brand"], info["terms"], info["category"], limit)
    print(f"{len(rows)} unique contacts matched\n")
    for r in rows[:12]:
        print(f"  [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if out:
        export_csv(rows, out)
    return info, rows

if __name__ == "__main__":
    url = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    safe = url.rstrip("/").split("/")[-1][:40] or "leads"
    run(url, limit, out=f"data/{safe}.csv")

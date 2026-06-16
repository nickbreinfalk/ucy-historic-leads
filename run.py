"""
End-to-end: paste a ucymachines listing URL -> ranked, deduped CSV of historic
leads who could buy it. This is the logic the Slack bot will call.

  python3 run.py <listing_url> [limit]
"""
import sys
from listing import parse_listing
from match import match, count_by_tier, tier_summary, export_csv

def run(url, limit=None, out=None, full=False):
    info = parse_listing(url)
    print(f"machine : {info['title']}")
    print(f"brand   : {info['brand']}")
    print(f"category: {info['category']}")
    print(f"terms   : {info['terms']}\n")
    counts = count_by_tier(info["brand"], info["terms"], info["category"])
    total, strong, cat, kw = tier_summary(counts)
    print(f"pool: {total} matched  |  {strong} brand  /  {cat} category  /  {kw} keyword-only")
    rows = match(info["brand"], info["terms"], info["category"], limit,
                 min_tier=(1 if full else 2))
    print(f"{len(rows)} in CSV (min_tier={'1' if full else '2'})\n")
    for r in rows[:12]:
        print(f"  T{r['tier']} [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if out:
        export_csv(rows, out)
    return info, rows

if __name__ == "__main__":
    url = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None  # default: no cap
    safe = url.rstrip("/").split("/")[-1][:40] or "leads"
    run(url, limit, out=f"data/{safe}.csv")

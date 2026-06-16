"""
Core matcher: given a machine (brand + machine-type search terms + optional category),
find historic leads who requested similar machines, deduped to one contact per email,
ranked by relevance, number of past requests, and recency.

Usage (manual test):
  python3 match.py --brand "OGP" \
      --terms '"coordinate measuring" or "optical gaging" or smartscope or cmm or "video measuring"' \
      --category "CMM / measuring" --limit 1000 --out data/ogp_leads.csv
"""
import os, csv, argparse, psycopg
from dotenv import load_dotenv

load_dotenv()

# One row per contact (email). Score: brand=3, category=2, plus full-text rank.
MATCH_SQL = """
with scored as (
    select email, company, first_name, last_name, phone, country, city,
           listing_title, category, created_date,
           ( case when %(brand)s <> '' and lower(brand) = lower(%(brand)s) then 3 else 0 end
           + case when %(category)s <> '' and category = %(category)s then 2 else 0 end
           + case when %(terms)s <> '' and to_tsvector('simple', listing_title)
                       @@ websearch_to_tsquery('simple', %(terms)s)
                  then ts_rank(to_tsvector('simple', listing_title),
                               websearch_to_tsquery('simple', %(terms)s))
                  else 0 end ) as score
    from leads
    where (%(brand)s <> '' and lower(brand) = lower(%(brand)s))
       or (%(category)s <> '' and category = %(category)s)
       or (%(terms)s <> '' and to_tsvector('simple', listing_title)
                @@ websearch_to_tsquery('simple', %(terms)s))
)
select email,
       max(company)        as company,
       max(first_name)     as first_name,
       max(last_name)      as last_name,
       max(phone)          as phone,
       max(country)        as country,
       max(city)           as city,
       count(*)            as past_requests,
       max(created_date)   as last_request,
       round(max(score)::numeric, 3) as relevance,
       (array_agg(distinct listing_title))[1:3] as example_requests
from scored
where score > 0
group by email
order by relevance desc, past_requests desc, last_request desc nulls last
limit %(limit)s
"""

def match(brand="", terms="", category="", limit=1000):
    params = {"brand": brand or "", "terms": terms or "",
              "category": category or "", "limit": limit}
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        cur = conn.execute(MATCH_SQL, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

def export_csv(rows, path):
    if not rows:
        print("no matches"); return
    fields = ["company", "first_name", "last_name", "email", "phone",
              "country", "city", "past_requests", "last_request",
              "relevance", "example_requests"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["example_requests"] = " | ".join(r.get("example_requests") or [])
            w.writerow(r)
    print(f"wrote {len(rows)} contacts -> {path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="")
    ap.add_argument("--terms", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rows = match(a.brand, a.terms, a.category, a.limit)
    print(f"{len(rows)} unique contacts matched")
    for r in rows[:15]:
        print(f"  [{r['relevance']}] {r['past_requests']}x  {(r['company'] or '')[:28]:28}  {r['email']:35}  {r['country']}")
    if a.out:
        export_csv(rows, a.out)

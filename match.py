"""
Core matcher: given a machine (brand + machine-type search terms + optional
category), find historic leads who requested similar machines, deduped to one
contact per email, ranked by an explicit TIER scheme so strong matches never
collapse into the keyword noise.

Tiers (per matching row):
  5  brand match (exact OR 'brand %' prefix) AND category match
  4  brand match only
  3  category match AND full-text keyword hit
  2  category match only
  1  full-text keyword hit only
A contact's tier = max over their matching rows.

relevance = tier*1000 + ts_rank*10 + ln(1+past_requests)
  -> tier dominates; ts_rank orders within a tier; repeat buyers float up,
     but can never cross a tier boundary.

Default CSV gates to tier >= 2 (drops the flat keyword-only tail where real
weak buyers and pure junk are indistinguishable). Pass min_tier=1 for --full.
"""
import os, csv, argparse, psycopg
from dotenv import load_dotenv

load_dotenv()

# Shared scored+tiered CTE. brand prefix match ('OGP %') rescues fragmented
# brands like "OGP ZIP", "Mazak Integrex", "Doosan Puma" into the brand tier.
SCORED_CTE = """
with scored as (
    select email, company, first_name, last_name, phone, country, city,
           listing_title, category, created_date,
           ( %(brand)s <> '' and ( lower(brand) = lower(%(brand)s)
                                    or lower(brand) like lower(%(brand)s) || ' %%' ) ) as brand_match,
           ( %(category)s <> '' and category = %(category)s ) as cat_match,
           ( %(terms)s <> '' and to_tsvector('simple', listing_title)
                 @@ websearch_to_tsquery('simple', %(terms)s) ) as ft_match,
           case when %(terms)s <> '' then
                  ts_rank(to_tsvector('simple', listing_title),
                          websearch_to_tsquery('simple', %(terms)s))
                else 0 end as rank
    from leads
    where ( %(brand)s <> '' and ( lower(brand) = lower(%(brand)s)
                                  or lower(brand) like lower(%(brand)s) || ' %%' ) )
       or ( %(category)s <> '' and category = %(category)s )
       or ( %(terms)s <> '' and to_tsvector('simple', listing_title)
                @@ websearch_to_tsquery('simple', %(terms)s) )
),
tiered as (
    select *,
        case when brand_match and cat_match then 5
             when brand_match            then 4
             when cat_match and ft_match  then 3
             when cat_match               then 2
             when ft_match                then 1
             else 0 end as tier
    from scored
)
"""

MATCH_SQL = SCORED_CTE + """
select email,
       (array_agg(company    order by created_date desc nulls last))[1] as company,
       (array_agg(first_name order by created_date desc nulls last))[1] as first_name,
       (array_agg(last_name  order by created_date desc nulls last))[1] as last_name,
       (array_agg(phone      order by created_date desc nulls last))[1] as phone,
       (array_agg(country    order by created_date desc nulls last))[1] as country,
       (array_agg(city       order by created_date desc nulls last))[1] as city,
       count(*)          as past_requests,
       max(created_date) as last_request,
       max(tier)         as tier,
       round((max(tier)*1000 + max(rank)*10 + ln(1 + count(*)))::numeric, 3) as relevance,
       (array_agg(distinct listing_title))[1:3] as example_requests
from tiered
group by email
having max(tier) >= %(min_tier)s
order by relevance desc, last_request desc nulls last
"""

# Honest pool size, broken down by tier (distinct contacts), no LIMIT.
COUNT_SQL = SCORED_CTE + """
select t.tier, count(*) from (
    select email, max(tier) as tier from tiered group by email
) t
where t.tier > 0
group by t.tier
order by t.tier desc
"""

def _params(brand, terms, category, min_tier=2):
    return {"brand": brand or "", "terms": terms or "",
            "category": category or "", "min_tier": min_tier}

def match(brand="", terms="", category="", limit=None, min_tier=2):
    """No cap by default — returns every tier>=min_tier contact, best-first.
    Pass an int `limit` only if you deliberately want a smaller slice."""
    p = _params(brand, terms, category, min_tier)
    sql = MATCH_SQL + (f"\nlimit {int(limit)}" if limit else "")
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        cur = conn.execute(sql, p)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

def count_by_tier(brand="", terms="", category=""):
    """{tier:int -> contacts:int}. Honest totals across the whole pool."""
    p = _params(brand, terms, category)
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        return {int(t): int(n) for t, n in conn.execute(COUNT_SQL, p).fetchall()}

TIER_LABEL = {5: "brand+category", 4: "brand", 3: "category+keyword",
              2: "category", 1: "keyword-only"}

def tier_summary(counts):
    total = sum(counts.values())
    strong = counts.get(5, 0) + counts.get(4, 0)
    cat = counts.get(3, 0) + counts.get(2, 0)
    kw = counts.get(1, 0)
    return total, strong, cat, kw

def export_csv(rows, path):
    if not rows:
        print("no matches"); return
    fields = ["company", "first_name", "last_name", "email", "phone", "country",
              "city", "tier", "past_requests", "last_request", "relevance",
              "example_requests"]
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
    ap.add_argument("--limit", type=int, default=0, help="0 = no cap (default)")
    ap.add_argument("--full", action="store_true", help="include tier-1 keyword-only matches")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    counts = count_by_tier(a.brand, a.terms, a.category)
    total, strong, cat, kw = tier_summary(counts)
    print(f"pool: {total} matched  |  {strong} brand  /  {cat} category  /  {kw} keyword-only")
    rows = match(a.brand, a.terms, a.category, a.limit or None, min_tier=(1 if a.full else 2))
    print(f"{len(rows)} in CSV (min_tier={'1' if a.full else '2'})")
    for r in rows[:15]:
        print(f"  T{r['tier']} [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if a.out:
        export_csv(rows, a.out)

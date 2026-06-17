"""
Tiered matcher. Given a machine profile (brand + discriminating type-terms +
optional category), find historic leads, deduped to one contact per email.

Tiers (per contact = max over their matching rows):
  5  bought this BRAND and this TYPE        (hottest)
  4  bought this BRAND (any machine)
  3  bought this TYPE (machine_type col exact hit, category col, OR a
     discriminating synonym in the title)
Rows that match neither brand nor type are not returned.

The `machine_type` column (AI-backfilled, brand-free, ~99% coverage) is matched
two ways: (a) exact equality against the listing's own classified type (`mtype`,
btree leads_machine_type_idx), and (b) full-text of the discriminating synonyms
against machine_type (GIN leads_machine_type_fts_idx) — drift-tolerant, so a
query for "fiber laser cutter" still hits a lead typed "fiber laser cutting
machine". This is the key recall win for TERSE leads — a row titled just
"Haas VF-2" shares no type-words with the title, but its machine_type
("vertical machining center") still matches.

relevance = tier*1000 + ts_rank*10 + ln(1+past_requests)
  -> tier dominates; within a tier, more-specific + repeat + recent rise.

Because the type-terms come from the self-learning classifier, they are
discriminating (no generic-noun noise), so tier 3 is high precision — there is
no separate junk/keyword tier to exclude. No row cap: every match, best-first.
"""
import os, re, csv, argparse, psycopg
from dotenv import load_dotenv

load_dotenv()

SCORED_CTE = """
with scored as (
    select email, company, first_name, last_name, phone, country, city,
           listing_title, category, created_date,
           ( %(brand)s <> '' and ( lower(brand) = lower(%(brand)s)
                                    or lower(brand) like lower(%(brand)s) || ' %%' ) ) as brand_match,
           ( ( %(category)s <> '' and category = %(category)s )
             or ( %(mtype)s <> '' and machine_type = %(mtype)s )
             or ( %(terms)s <> '' and to_tsvector('simple', machine_type)
                      @@ websearch_to_tsquery('simple', %(terms)s) )
             or ( %(terms)s <> '' and to_tsvector('simple', listing_title)
                      @@ websearch_to_tsquery('simple', %(terms)s) ) ) as type_match,
           case when %(terms)s <> '' then
                  ts_rank(to_tsvector('simple', listing_title),
                          websearch_to_tsquery('simple', %(terms)s))
                else 0 end as rank
    from leads
    where ( %(brand)s <> '' and ( lower(brand) = lower(%(brand)s)
                                  or lower(brand) like lower(%(brand)s) || ' %%' ) )
       or ( %(category)s <> '' and category = %(category)s )
       or ( %(mtype)s <> '' and machine_type = %(mtype)s )
       or ( %(terms)s <> '' and to_tsvector('simple', machine_type)
                @@ websearch_to_tsquery('simple', %(terms)s) )
       or ( %(terms)s <> '' and to_tsvector('simple', listing_title)
                @@ websearch_to_tsquery('simple', %(terms)s) )
),
tiered as (
    select *,
        case when brand_match and type_match then 5
             when brand_match               then 4
             when type_match                then 3
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

COUNT_SQL = SCORED_CTE + """
select t.tier, count(*) from (
    select email, max(tier) as tier from tiered group by email
) t
where t.tier > 0
group by t.tier order by t.tier desc
"""

def _params(brand, terms, category, mtype="", min_tier=3):
    brand = re.sub(r"([%_\\])", r"\\\1", brand or "")  # escape LIKE wildcards
    return {"brand": brand, "terms": terms or "", "category": category or "",
            "mtype": (mtype or "").lower().strip(),  # leads.machine_type is lowercase
            "min_tier": min_tier}

def match(brand="", terms="", category="", mtype="", limit=None, min_tier=3):
    p = _params(brand, terms, category, mtype, min_tier)
    sql = MATCH_SQL + (f"\nlimit {int(limit)}" if limit else "")
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        cur = conn.execute(sql, p)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

def count_by_tier(brand="", terms="", category="", mtype=""):
    p = _params(brand, terms, category, mtype)
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        return {int(t): int(n) for t, n in conn.execute(COUNT_SQL, p).fetchall()}

def tier_summary(counts):
    """-> (total, brand_buyers, type_buyers)"""
    brand = counts.get(5, 0) + counts.get(4, 0)
    typ = counts.get(3, 0)
    return brand + typ, brand, typ

def export_csv(rows, path):
    if not rows:
        print("no matches"); return
    fields = ["company", "first_name", "last_name", "email", "phone", "country",
              "city", "tier", "past_requests", "last_request", "relevance", "example_requests"]
    from listing import strip_location
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["example_requests"] = " | ".join(strip_location(t) for t in (r.get("example_requests") or []))
            w.writerow(r)
    print(f"wrote {len(rows)} contacts -> {path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="")
    ap.add_argument("--terms", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--mtype", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    counts = count_by_tier(a.brand, a.terms, a.category, a.mtype)
    total, brand, typ = tier_summary(counts)
    print(f"pool: {total} matched  |  {brand} brand  /  {typ} type")
    rows = match(a.brand, a.terms, a.category, a.mtype)
    for r in rows[:15]:
        print(f"  T{r['tier']} [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if a.out:
        export_csv(rows, a.out)

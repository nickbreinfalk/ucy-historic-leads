"""
Tiered matcher (v2 — deterministic, machine_type-anchored).

Given a posted machine's classified machine_type, find historic leads who
inquired about the SAME KIND of machine, deduped to one contact per email.

Type matching is DETERMINISTIC — it does NOT use the per-listing Haiku synonyms
(those varied call-to-call and dragged in adjacent machines). Instead it builds a
stable query from the machine_type's CORE tokens: discriminating words with
generic nouns and modifiers (vertical/horizontal/cnc/automatic...) removed, each
expanded by a small curated synonym set (cleaning<->washer, lathe<->turning...),
then AND-ed together. Requiring ALL core tokens means 'thread rolling' never
matches plain 'milling' or 'plate rolling'. It runs against the AI-backfilled
machine_type column (primary, ~99% coverage) and the listing_title (secondary,
for the few still-untyped leads). Same machine in -> same list out.

Output tiers (per contact = max over their rows):
  5  inquired about this BRAND and this TYPE   (hottest)
  3  inquired about this TYPE (any brand)
Brand-ONLY matches (same brand, a DIFFERENT machine) are intentionally EXCLUDED:
for selling a specific machine, "wanted this type" is the buying signal, and a
different machine from the same brand is a weak fit that otherwise mis-ranks above
real type matches. Brand loyalty still counts — it lifts a type match to tier 5.
relevance = tier*1000 + ts_rank*10 + ln(1 + past_requests)  -> tier dominates.
No row cap: every match, best-first.
"""
import os, re, csv, argparse, psycopg
from dotenv import load_dotenv
from listing import strip_location

load_dotenv()

# dropped from a machine_type before building the type query (carry no type meaning)
TYPE_GENERIC = {"machine", "machines", "system", "systems", "unit", "units", "line",
                "lines", "equipment", "plant", "complete", "used", "new", "sale",
                "for", "the", "with", "and", "set"}
# modifiers: narrow a type but aren't the type itself -> drop so sub-variants still match
TYPE_MODIFIER = {"vertical", "horizontal", "cnc", "automatic", "automated", "universal",
                 "manual", "semi", "mobile", "portable", "industrial", "heavy",
                 "compact", "double", "single", "twin", "mini", "micro", "large",
                 "small", "type", "series", "fully", "axis", "high", "speed",
                 "precision", "standard", "electric", "hydraulic",
                 # 'center'/'centre' is a suffix on machine-tool types, not the type
                 # itself — dropping it unifies 'machining center' with 'milling
                 # machine', and 'turning center' with 'lathe'.
                 "center", "centre"}
# curated SYMMETRIC synonyms so word-variants of the SAME machine still match
TYPE_SYN = {
    "cleaning": ["washer", "washing", "cleaner", "wash"],
    "washer": ["cleaning", "washing", "cleaner", "wash"],
    "washing": ["cleaning", "washer", "cleaner", "wash"],
    "cleaner": ["cleaning", "washer", "washing", "wash"],
    "wash": ["cleaning", "washer", "washing", "cleaner"],
    "lathe": ["turning"], "turning": ["lathe"],
    # a machining center IS a CNC milling machine — same buyer market. (Still NO
    # milling<->mill: "mill" is a homonym — ball/rolling/grain/saw mill aren't mills.)
    "milling": ["machining"], "machining": ["milling"],
    "grinding": ["grinder"], "grinder": ["grinding"],
    "printing": ["printer"], "printer": ["printing"],
    "molding": ["moulding"], "moulding": ["molding"],
    "welding": ["welder"], "welder": ["welding"],
    "bending": ["bender"], "bender": ["bending"],
    "forming": ["former"], "former": ["forming"],
    "extruder": ["extrusion"], "extrusion": ["extruder"],
    "sawing": ["saw"], "saw": ["sawing"],
    "conveyor": ["conveying"], "conveying": ["conveyor"],
    # aggregate-processing materials are interchangeable (gravel/sand/aggregate
    # washing & screening plants are the same machine for a buyer)
    "gravel": ["sand", "aggregate", "quarry"], "sand": ["gravel", "aggregate", "quarry"],
    "aggregate": ["gravel", "sand", "quarry"], "quarry": ["gravel", "sand", "aggregate"],
    # SMT / PCB solder-paste printing context (stencil printer == solder paste
    # printer == smd printer; 'smt' is context, not a distinct machine)
    "smt": ["smd", "stencil", "solder"], "smd": ["smt", "stencil", "solder"],
    "stencil": ["smt", "smd", "solder"], "solder": ["smt", "smd", "stencil"],
}

def core_tokens(mtype):
    """Discriminating tokens of a machine_type (generic nouns + modifiers stripped)."""
    toks, seen = [], set()
    for w in re.findall(r"[a-z0-9]+", (mtype or "").lower()):
        if len(w) <= 2 or w in TYPE_GENERIC or w in TYPE_MODIFIER or w in seen:
            continue
        seen.add(w); toks.append(w)
    return toks

def build_type_query(mtype):
    """Deterministic to_tsquery from a machine_type's core tokens, e.g.
    'parts cleaning machine' -> '(parts) & (cleaning | washer | washing | cleaner | wash)'."""
    toks = core_tokens(mtype)
    if not toks:
        return ""
    groups = []
    for t in toks:
        alts, s2 = [], set()
        for a in [t] + TYPE_SYN.get(t, []):
            if a not in s2:
                s2.add(a); alts.append(a)
        groups.append("(" + " | ".join(alts) + ")")
    return " & ".join(groups)

# too vague to be a useful "type" on their own — if the title only yields one of
# these, treat it as NOT grounded (fall through to the AI-confidence / brand path).
_VAGUE_TYPE = {"truck", "vehicle", "machine", "equipment", "system", "unit", "line",
               "plant", "device", "tool", "complete", "used", "automatic"}

# operation/material words so broad they don't define a buyer audience on their own:
# they span industries or whole machine families ('printer' = graphic/label/3D;
# 'filling' = liquid/powder/any product; 'grinding' = metal AND food). A type built
# ONLY of these is "thin" — it can't anchor a clean type match, so the matcher routes
# by BRAND instead (the brand pins the real audience). NB: single-domain operations
# kept OUT on purpose (milling/turning/lathe/boring/welding) — those match cleanly.
BROAD_TYPE_TOKENS = {
    "printer", "printing", "screen", "filling", "filler", "power", "roller",
    "rolling", "packaging", "coating", "mixing", "mixer", "cutting", "cutter",
    "grinding", "washing", "cleaning", "cleaner", "washer", "drilling", "sanding",
    "conveyor", "conveying", "heating", "cooling", "drying", "loading", "lifting",
    "feeding", "spraying", "pressing",
    # 'press' alone spans press brake / stamping / hydraulic / baling / filter press —
    # totally different buyers. Needs a qualifier ('press brake', 'filter press') to match.
    "press",
}
_THIN = _VAGUE_TYPE | BROAD_TYPE_TOKENS

def type_is_thin(mtype):
    """True if a machine_type has NO discriminating token — only vague/broad words
    that span domains. Such a type can't define a clean cross-brand audience."""
    toks = core_tokens(mtype)
    return (not toks) or all(t in _THIN for t in toks)

def type_grounded(mtype, title):
    """True if a MEANINGFUL, DISCRIMINATING machine type is actually stated in the
    title — not inferred from a cryptic model, not a vague word ('truck'/'machine'),
    and not only broad cross-domain words ('printer'/'filling'/'screen'). Routes the
    matcher: grounded -> match by TYPE; else -> AI-confidence / BRAND. Every core
    token (or a curated synonym) must appear in the title."""
    tl = (title or "").lower()
    toks = core_tokens(mtype)
    if not toks or type_is_thin(mtype):
        return False
    for t in toks:
        if not any(re.search(r"\b" + re.escape(a) + r"\b", tl) for a in [t] + TYPE_SYN.get(t, [])):
            return False
    return True

SCORED_CTE = """
with scored as (
    select email, company, first_name, last_name, phone, country, city,
           listing_title, created_date,
           ( %(brand)s <> '' and ( lower(brand) = lower(%(brand)s)
                                    or lower(brand) like lower(%(brand)s) || ' %%' ) ) as brand_match,
           ( ( %(mtype)s <> '' and machine_type = %(mtype)s )
             or ( %(typeq)s <> '' and to_tsvector('simple', machine_type) @@ to_tsquery('simple', %(typeq)s) )
             or ( %(typeq)s <> '' and to_tsvector('simple', listing_title) @@ to_tsquery('simple', %(typeq)s) )
           ) as type_match,
           case when %(typeq)s <> '' then
                  ts_rank(to_tsvector('simple', machine_type), to_tsquery('simple', %(typeq)s))
                else 0 end as rank
    from leads
    -- candidate = TYPE match only (brand-only rows excluded). brand_match is still
    -- computed above so a brand+type row becomes tier 5; pure-brand never enters.
    where ( %(mtype)s <> '' and machine_type = %(mtype)s )
       or ( %(typeq)s <> '' and to_tsvector('simple', machine_type) @@ to_tsquery('simple', %(typeq)s) )
       or ( %(typeq)s <> '' and to_tsvector('simple', listing_title) @@ to_tsquery('simple', %(typeq)s) )
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

# BRAND MODE — used when the title gives only a brand, no clear type. Returns every
# contact who inquired about that brand (any machine), ranked by inquiry count/recency.
_BRAND_WHERE = ("%(brand)s <> '' and ( lower(brand) = lower(%(brand)s) "
                "or lower(brand) like lower(%(brand)s) || ' %%' )")
BRAND_SQL = f"""
select email,
       (array_agg(company    order by created_date desc nulls last))[1] as company,
       (array_agg(first_name order by created_date desc nulls last))[1] as first_name,
       (array_agg(last_name  order by created_date desc nulls last))[1] as last_name,
       (array_agg(phone      order by created_date desc nulls last))[1] as phone,
       (array_agg(country    order by created_date desc nulls last))[1] as country,
       (array_agg(city       order by created_date desc nulls last))[1] as city,
       count(*)          as past_requests,
       max(created_date) as last_request,
       4                 as tier,
       round((4000 + ln(1 + count(*)))::numeric, 3) as relevance,
       (array_agg(distinct listing_title))[1:3] as example_requests
from leads
where {_BRAND_WHERE}
group by email
order by past_requests desc, last_request desc nulls last
"""

def _params(brand, mtype, min_tier=3):
    brand = re.sub(r"([%_\\])", r"\\\1", brand or "")  # escape LIKE wildcards
    mtype = (mtype or "").lower().strip()              # leads.machine_type is lowercase
    return {"brand": brand, "mtype": mtype, "typeq": build_type_query(mtype), "min_tier": min_tier}

def match(brand="", mtype="", brand_only=False, limit=None, min_tier=3):
    if brand_only:
        p = {"brand": re.sub(r"([%_\\])", r"\\\1", brand or "")}
        sql = BRAND_SQL
    else:
        p = _params(brand, mtype, min_tier)
        sql = MATCH_SQL
    sql += (f"\nlimit {int(limit)}" if limit else "")
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        cur = conn.execute(sql, p)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

def count_by_tier(brand="", mtype="", brand_only=False):
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        if brand_only:
            p = {"brand": re.sub(r"([%_\\])", r"\\\1", brand or "")}
            n = conn.execute(f"select count(distinct email) from leads where {_BRAND_WHERE}", p).fetchone()[0]
            return {4: int(n)}
        return {int(t): int(n) for t, n in conn.execute(COUNT_SQL, _params(brand, mtype)).fetchall()}

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
    ap.add_argument("--mtype", default="", help="machine_type (e.g. 'press brake')")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    print("type query:", build_type_query(a.mtype) or "(none)")
    counts = count_by_tier(a.brand, a.mtype)
    total, brand, typ = tier_summary(counts)
    print(f"pool: {total} matched  |  {brand} brand  /  {typ} type")
    rows = match(a.brand, a.mtype)
    for r in rows[:15]:
        print(f"  T{r['tier']} [{r['relevance']}] {r['past_requests']}x  "
              f"{(r['company'] or '')[:26]:26}  {r['email']:34}  {r['country']}")
    if a.out:
        export_csv(rows, a.out)

#!/usr/bin/env python3
"""Run the NEW pipeline (classify_stable -> _route -> size + samples) on all 100
validation URLs. Reuses data/golden_cache.json so the 30 golden cases aren't
re-classified. READ-ONLY. Output -> /tmp/final100.json + a routing-distribution
summary, so we can spot regressions (good type-matches lost to brand/abstain)."""
import os, sys, json, time, collections
from dotenv import load_dotenv
load_dotenv("/Users/nickbreinfalk/historic-machinery-leads/.env")
os.environ["SUPABASE_DB_URL"] = os.environ["SUPABASE_ADMIN_DB_URL"]
import psycopg, classify, core, match
from listing import fetch_title

cache = json.load(open("data/golden_cache.json", encoding="utf-8")) if os.path.exists("data/golden_cache.json") else {}
conn = psycopg.connect(os.environ["SUPABASE_ADMIN_DB_URL"], autocommit=True)
conn.execute("set statement_timeout='0'")
TYPE_SAMP = """select machine_type from leads
 where ( %(mtype)s<>'' and machine_type=%(mtype)s )
    or ( %(typeq)s<>'' and to_tsvector('simple',machine_type)@@to_tsquery('simple',%(typeq)s) )
 order by random() limit 6"""
BRAND_SAMP = """select machine_type from leads
 where %(brand)s<>'' and ( lower(brand)=lower(%(brand)s) or lower(brand) like lower(%(brand)s)||' %%' )
 order by random() limit 6"""

slugs = [l.strip() for l in open("data/final_test_urls.txt", encoding="utf-8") if l.strip()]
out, dist = [], collections.Counter()
for i, slug in enumerate(slugs):
    url = f"https://www.ucymachines.com/listings/{slug}"
    if slug in cache:
        raw, prof = cache[slug]["title"], cache[slug]["profile"]
    else:
        title, sl = fetch_title(url)
        raw = title or sl
        prof = classify.classify_stable(raw, slug=sl, url=url)
        cache[slug] = {"title": raw, "profile": prof}
    mode = core._route(prof, raw)
    mt, brand = prof.get("category") or "", prof.get("brand") or ""
    try:
        if mode == "brand":
            cnt = match.count_by_tier(brand, brand_only=True).get(4, 0)
            samp = [r[0] for r in conn.execute(BRAND_SAMP, {"brand": brand}).fetchall()]
        elif mode == "type":
            cnt = sum(match.count_by_tier(brand, mt).values())
            samp = [r[0] for r in conn.execute(TYPE_SAMP, match._params(brand, mt)).fetchall()]
        else:
            cnt, samp = 0, []
    except Exception as e:
        cnt, samp = -1, [repr(e)[:60]]
    dist[mode] += 1
    out.append({"slug": slug, "title": raw[:70], "brand": brand, "type": mt,
                "domain": prof.get("domain"), "conf": prof.get("confidence"),
                "mode": mode, "count": cnt, "sample": samp})
    if (i + 1) % 20 == 0:
        print(f"  {i+1}/{len(slugs)}", flush=True)

json.dump(cache, open("data/golden_cache.json", "w"), indent=0)
json.dump(out, open("/tmp/final100.json", "w"), indent=1)
print(f"\nrouting distribution over {len(slugs)}: {dict(dist)}")
big = [o for o in out if o["mode"] == "type" and o["count"] > 30000]
print(f"large type pools (>30k, check for firehose): {len(big)}")
for o in big:
    print(f"  {o['count']:>8,}  {o['type'][:30]:30}  {o['title'][:40]}")
print("DONE -> /tmp/final100.json")

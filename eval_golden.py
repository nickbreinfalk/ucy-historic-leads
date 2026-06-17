#!/usr/bin/env python3
"""Golden regression harness (Phase 0).

Runs the REAL pipeline (parse -> classify_stable -> _route -> size) on the labelled
listings in data/golden.jsonl and asserts the routing is SAFE. Catches silent
regressions when guards / synonyms / prompts change.

  python3 eval_golden.py            # uses cache (fast, ~free)
  python3 eval_golden.py --refresh  # re-fetch titles + re-classify (Haiku)

Per case it checks: the routing mode is in `ok_modes`; for type-mode, the type
contains an `expect_type` token (if given) and none of `forbid_type`.
Output: per-case PASS/FAIL + a summary (overall + former-disaster safety rate)."""
import os, sys, json, time
from dotenv import load_dotenv
load_dotenv("/Users/nickbreinfalk/historic-machinery-leads/.env")
os.environ["SUPABASE_DB_URL"] = os.environ["SUPABASE_ADMIN_DB_URL"]

REFRESH = "--refresh" in sys.argv
CACHE_PATH = "data/golden_cache.json"
GOLDEN = "data/golden.jsonl"

import classify, core, match
from listing import fetch_title

cache = {}
if os.path.exists(CACHE_PATH) and not REFRESH:
    cache = json.load(open(CACHE_PATH, encoding="utf-8"))

def get(slug):
    """Cached (title, profile) for a slug. title via page fetch+slug; profile via Haiku."""
    if slug in cache:
        return cache[slug]["title"], cache[slug]["profile"]
    url = f"https://www.ucymachines.com/listings/{slug}"
    title, sl = fetch_title(url)
    raw = (title or sl)
    prof = classify.classify_stable(raw, slug=sl, url=url)
    cache[slug] = {"title": raw, "profile": prof}
    return raw, prof

cases = [json.loads(l) for l in open(GOLDEN, encoding="utf-8") if l.strip()]
rows, npass = [], 0
for c in cases:
    raw, prof = get(c["slug"])
    mode = core._route(prof, raw)
    mt = prof.get("category") or ""
    brand = prof.get("brand") or ""
    # size (cheap): count only
    try:
        if mode == "brand":
            cnt = match.count_by_tier(brand, brand_only=True).get(4, 0)
        elif mode == "type":
            cnt = sum(match.count_by_tier(brand, mt).values())
        else:
            cnt = 0
    except Exception as e:
        cnt = -1
    # assertions
    ok = mode in c["ok_modes"]
    why = "" if ok else f"mode {mode} not in {c['ok_modes']}"
    if ok and mode == "type":
        et = c.get("expect_type")
        if et and not any(t in mt.lower() for t in et):
            ok, why = False, f"type '{mt}' lacks {et}"
        ft = c.get("forbid_type")
        if ft and any(t in mt.lower() for t in ft):
            ok, why = False, f"type '{mt}' hits forbidden {ft}"
    npass += ok
    rows.append((ok, c["slug"][:34], mode, prof.get("confidence"), prof.get("domain") or "-",
                 mt[:26], f"{cnt:,}" if cnt >= 0 else "ERR", why, c["note"]))

json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), indent=0)

print(f"\n{'':1}{'slug':34} {'mode':7} {'conf':4} {'domain':14} {'type':26} {'n':>7}  why")
print("-" * 130)
for ok, slug, mode, conf, dom, mt, cnt, why, note in rows:
    print(f"{'✓' if ok else '✗'} {slug:34} {mode:7} {(conf or '-'):4} {dom:14} {mt:26} {cnt:>7}  {why}")
print("-" * 130)
print(f"PASS {npass}/{len(cases)}")
# how many of the former 16 disasters are now safe (not type-mode-wrong)
DISASTER = {"9103561","7534977","6691645","8182308","8089502","9008116","8565050",
            "5426254","6755217","8727407","8727709","9110692","8334226","8724803",
            "9085664","9110658"}
dis = [(ok, slug) for ok, slug, *_ in rows if slug.split("-")[0] in DISASTER]
print(f"former-disaster safety: {sum(1 for ok,_ in dis if ok)}/{len(dis)}")

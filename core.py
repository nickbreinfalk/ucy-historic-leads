"""
Shared logic: turn a ucymachines listing URL into the Slack reply
(summary text + CSV bytes + filename). Used by both the live Socket Mode bot
(slackbot.py) and the scheduled cron poller (poll.py) so they never drift.

Flow: parse the listing -> classify it (self-learning, Haiku only on new types)
-> tiered match -> ranked CSV. The summary shows a 🧠 (Haiku learned a new type)
or ✓ (recognized from cache) tag so usage is always visible.
"""
import io, csv, re
from listing import parse_listing, strip_location
from classify import classify_stable
from match import match, type_grounded, type_is_thin
from registry import brand_domain, is_collision_brand
from gate import audit_list

_COUNTRY_ABBR = {"United States": "USA", "United States of America": "USA",
                 "United Arab Emirates": "UAE", "United Kingdom": "UK"}

def _clean_title(title):
    """Tidy a machine title for display: drop any URL, de-dup pipe-joined halves,
    strip the location/'for Sale' tail, collapse whitespace."""
    t = re.sub(r"https?://\S+", " ", title or "")
    parts = [p.strip() for p in t.split("|") if p.strip()]
    if parts:
        t = max(parts, key=len)
    t = strip_location(t).replace(" for Sale", "")
    return re.sub(r"\s+", " ", t).strip()[:70]

CSV_FIELDS = ["company", "first_name", "last_name", "email", "phone", "country",
              "city", "tier", "past_requests", "last_request", "relevance", "example_requests"]

def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        r = dict(r)
        r["example_requests"] = " | ".join(strip_location(t) for t in (r.get("example_requests") or []))
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

def _tag(profile):
    """Visible indicator of how the machine was classified."""
    cat = profile.get("category") or "—"
    if profile["used_haiku"]:
        return f":brain: _AI-optimized match · type: {cat}_"
    if profile["recognized"].startswith("rules"):
        return f":warning: _matched by rules (AI temporarily unavailable) · type: {cat}_"
    return f":information_source: _matched from cache (AI temporarily unavailable) · type: {cat}_"

# generic words that are NOT real machine brands — a listing that reduces to one of
# these (parts packages, equipment inventories, seller names) can't be matched.
_JUNK_BRAND = {"motor", "industrial", "complete", "equipment", "power", "used", "new",
               "machine", "package", "inventory", "products", "plant", "system", "line",
               "metalworking", "welding", "general", "misc", "various", "lot", "sds"}

def _is_junk_brand(b):
    b = (b or "").strip().lower()
    if not b or len(b) <= 2:
        return True
    # junk if ANY word in the "brand" is a generic term ("Motor Accessories Package",
    # "Industrial Metalworking ...") — these are descriptions/inventories, not brands.
    return bool({w for w in re.findall(r"[a-z]+", b)} & _JUNK_BRAND)

def _route(profile, raw_title):
    """Decide HOW to find the audience for a posted machine. Returns one of:
       'type'    — the type is reliable (stated in the title, or the AI confidently
                   recognises a specific discriminating type) -> everyone who wanted
                   THIS kind of machine (this brand+type, plus this type any brand).
       'brand'   — type is a guess but the brand is real -> everyone who inquired
                   about that brand (safe, on-brand, broad — never a wrong-type guess).
       'abstain' — neither a reliable type nor a safe brand -> don't blast garbage.

    Precision guards (registry-backed, deterministic):
      • a homonym brand ('Prima', 'Mechatronic') is never brand-blasted — wrong mix.
      • the AI's 'high' confidence is ignored when the type is THIN (only broad words
        like 'printer'/'filling'/'grinding') or when the brand's known industry
        contradicts the AI's domain (food brand + a metalworking type)."""
    mtype = profile.get("category") or ""
    brand = profile.get("brand") or ""
    hdomain = profile.get("domain") or ""
    grounded = type_grounded(mtype, raw_title)
    rdomain = brand_domain(brand)
    domain_conflict = bool(rdomain and hdomain and rdomain != hdomain)
    collision = is_collision_brand(brand)
    confident = (profile.get("confidence") == "high"
                 and not type_is_thin(mtype) and not domain_conflict)

    if grounded:                              # discriminating type literally in the title
        return "type"
    if confident:                             # AI genuinely recognises this exact type
        return "type"
    if collision:                             # homonym brand, type unproven -> too risky
        return "abstain"
    if brand and not _is_junk_brand(brand):   # real brand -> all its inquirers
        return "brand"
    return "abstain"

def build_reply(url):
    """Returns {info, profile, mode, rows, summary, csv, filename}. csv/filename None
    if there's nothing safe to send. Every CSV is one clean, blast-ready list — the
    whole audience that wanted a machine like this, never a wrong-type guess."""
    info = parse_listing(url)
    profile = classify_stable(info["title"], url=url)
    mtype = profile["category"] or ""
    brand = profile["brand"] or ""
    title = _clean_title(info["title"])

    mode = _route(profile, info["title"])
    grounded = type_grounded(mtype, info["title"])
    if mode == "abstain":
        return {"info": info, "profile": profile, "mode": mode, "rows": [], "csv": None, "filename": None,
                "summary": f":warning: Couldn't confidently identify *{title}* "
                           f"— not a clear machine type, and the brand is ambiguous/missing. Needs a human look."}
    by_brand = mode == "brand"
    rows = match(brand, brand_only=True) if by_brand else match(brand, mtype=mtype)
    # recall floor: a near-empty result for an AI-GUESSED type (not in the title) is
    # usually a slightly-off wording — fall back to the fuller on-brand list. A type
    # stated in the title is trusted even if small (genuinely few wanted it).
    if mode == "type" and not grounded and brand and not _is_junk_brand(brand) \
            and not is_collision_brand(brand) and len(rows) < 10:
        br = match(brand, brand_only=True)
        if len(br) > len(rows):
            rows, by_brand, mode = br, True, "brand"

    # --- final independent audience check + repair (Sonnet sees the ACTUAL leads,
    #     which the title-only classifier never did). Skipped for grounded type
    #     lists with a healthy pool (safe by construction); focused on the
    #     ungrounded / brand / thin / empty cases where mistakes actually happen. ---
    gate_note = ""
    if not (grounded and len(rows) >= 50):
        sample, seen = [], set()
        for r in rows[:12]:
            for t in (r.get("example_requests") or []):
                s = strip_location(t)
                if s and s not in seen:
                    seen.add(s); sample.append(s)
        g = audit_list(info["title"], mode, mtype, brand, len(rows), sample[:20])
        if g:
            if g["assessment"] == "repair" and g.get("repair_type"):
                rep = match(brand, mtype=g["repair_type"])
                if len(rep) > len(rows):
                    rows, mode, mtype = rep, "type", g["repair_type"]
                    gate_note = f"\n:wrench: _auto-expanded to type `{mtype}`_"
            elif g["assessment"] == "flag":
                gate_note = f"\n:warning: _auto-check: {g['reason']} — glance before sending_"
    by_brand = mode == "brand"

    if not rows:
        what = f"brand `{brand}`" if by_brand else f"type `{mtype or '—'}`"
        return {"info": info, "profile": profile, "mode": mode, "rows": [], "csv": None, "filename": None,
                "summary": f":mag: No leads for *{title}*  ({what})"}

    countries = {}
    for r in rows:
        c = (r.get("country") or "?").strip() or "?"
        c = _COUNTRY_ABBR.get(c, c)
        countries[c] = countries.get(c, 0) + 1
    ordered = sorted(countries.items(), key=lambda x: -x[1])
    top = " · ".join(f"{c} {n:,}" for c, n in ordered[:4])
    if len(ordered) > 4:
        top += f" · +{len(ordered) - 4} more"

    if by_brand:
        head = f"brand `{brand}`  ·  _type unclear → all {brand} inquirers_"
    else:
        t5 = sum(1 for r in rows if r["tier"] == 5)
        t3 = sum(1 for r in rows if r["tier"] == 3)
        head = f"type `{mtype or '—'}`  ·  brand `{brand or '—'}`"
        if t5:
            head += f"  ·  :fire: {t5:,} brand+type / {t3:,} type"

    summary = (f":dart: *{title}* — *{len(rows):,} leads*\n"
               f"{head}\n"
               f":earth_africa: top: {top}{gate_note}")
    filename = (re.sub(r"[^a-zA-Z0-9]+", "_", title)[:50] or "leads") + "_leads.csv"
    return {"info": info, "profile": profile, "mode": mode, "rows": rows,
            "summary": summary, "csv": _csv_bytes(rows), "filename": filename}

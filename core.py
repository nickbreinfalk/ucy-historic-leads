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
from classify import classify
from match import match

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

def build_reply(url):
    """Returns {info, rows, summary, csv, filename}. csv/filename None if no matches."""
    info = parse_listing(url)
    profile = classify(info["title"], url=url)
    # deterministic, machine_type-anchored match: the listing's classified type
    # drives a stable core-token query (no per-call Haiku-synonym instability).
    rows = match(profile["brand"], mtype=profile["category"])  # all tier>=3

    if not rows:
        nt = strip_location(info["title"]).replace(" for Sale", "").strip()
        return {"info": info, "profile": profile, "rows": [], "csv": None, "filename": None,
                "summary": f":mag: No matching leads for *{nt}*  (type: `{profile['category'] or '—'}`)"}

    t5  = sum(1 for r in rows if r["tier"] == 5)   # brand + type (hottest)
    typ = sum(1 for r in rows if r["tier"] == 3)   # this type (any brand)
    countries = {}
    for r in rows:
        c = (r.get("country") or "?").strip() or "?"
        countries[c] = countries.get(c, 0) + 1
    top = " · ".join(f"{c} {n}" for c, n in sorted(countries.items(), key=lambda x: -x[1])[:4])
    clean_title = strip_location(info["title"]).replace(" for Sale", "").strip()
    split = f"{t5:,} brand+type · {typ:,} type" if t5 else f"{typ:,} type"
    summary = (
        f":dart: *{clean_title}* — *{len(rows):,} leads*\n"
        f"type `{profile['category'] or '—'}`  ·  brand `{profile['brand'] or '—'}`\n"
        f":fire: {split}   ·   _ranked best-first; see `tier` column_\n"
        f":earth_africa: {top}"
    )
    filename = (re.sub(r"[^a-zA-Z0-9]+", "_", info["title"])[:50] or "leads") + "_leads.csv"
    return {"info": info, "profile": profile, "rows": rows,
            "summary": summary, "csv": _csv_bytes(rows), "filename": filename}

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
from match import match, type_grounded

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

def build_reply(url):
    """Returns {info, rows, summary, csv, filename}. csv/filename None if no matches.

    Routing: if the machine TYPE is stated in the title -> match by type (this
    brand+type + this type, any brand). If only a brand is identifiable (cryptic
    title) -> match by BRAND (everyone who inquired about that brand). Either way
    the CSV is one clean, blast-ready list — never a wrong-type guess."""
    info = parse_listing(url)
    profile = classify(info["title"], url=url)
    mtype = profile["category"] or ""
    brand = profile["brand"] or ""
    title = _clean_title(info["title"])

    by_brand = bool(brand) and not type_grounded(mtype, info["title"])
    rows = match(brand, brand_only=True) if by_brand else match(brand, mtype=mtype)

    if not rows:
        what = f"brand `{brand}`" if by_brand else f"type `{mtype or '—'}`"
        return {"info": info, "profile": profile, "rows": [], "csv": None, "filename": None,
                "summary": f":mag: No leads for *{title}*  ({what})"}

    countries = {}
    for r in rows:
        c = (r.get("country") or "?").strip() or "?"
        c = _COUNTRY_ABBR.get(c, c)
        countries[c] = countries.get(c, 0) + 1
    top = " · ".join(f"{c} {n}" for c, n in sorted(countries.items(), key=lambda x: -x[1])[:4])

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
               f":earth_africa: {top}")
    filename = (re.sub(r"[^a-zA-Z0-9]+", "_", title)[:50] or "leads") + "_leads.csv"
    return {"info": info, "profile": profile, "rows": rows,
            "summary": summary, "csv": _csv_bytes(rows), "filename": filename}

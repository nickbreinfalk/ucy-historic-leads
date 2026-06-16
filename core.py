"""
Shared logic: turn a ucymachines listing URL into the Slack reply
(summary text + CSV bytes + filename). Used by both the live Socket Mode bot
(slackbot.py) and the scheduled cron poller (poll.py) so they never drift.

Flow: parse the listing -> classify it (self-learning, Haiku only on new types)
-> tiered match -> ranked CSV. The summary shows a 🧠 (Haiku learned a new type)
or ✓ (recognized from cache) tag so usage is always visible.
"""
import io, csv, re
from listing import parse_listing
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
        r["example_requests"] = " | ".join(r.get("example_requests") or [])
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

def _tag(profile):
    """Visible indicator of whether Haiku fired."""
    if profile["used_haiku"]:
        return f":brain: _{profile['recognized']}_ (classified by AI — now cached, free next time)"
    if profile["recognized"].startswith("rules"):
        return ":warning: _classified by rules (AI unavailable)_"
    return f":white_check_mark: _{profile['recognized']} — recognized from cache, no AI used_"

def build_reply(url):
    """Returns {info, rows, summary, csv, filename}. csv/filename None if no matches."""
    info = parse_listing(url)
    profile = classify(info["title"], url=url)
    rows = match(profile["brand"], profile["terms"], profile["category"])  # all tier>=3

    if not rows:
        return {"info": info, "profile": profile, "rows": [], "csv": None, "filename": None,
                "summary": (f":mag: No matching buyers for *{info['title']}*.\n{_tag(profile)}")}

    brand = sum(1 for r in rows if r["tier"] >= 4)
    typ   = sum(1 for r in rows if r["tier"] == 3)
    countries = {}
    for r in rows:
        c = (r.get("country") or "?").strip() or "?"
        countries[c] = countries.get(c, 0) + 1
    top = ", ".join(f"{c} ({n})" for c, n in sorted(countries.items(), key=lambda x: -x[1])[:5])

    summary = (
        f":dart: *{len(rows):,} potential buyers* for *{info['title']}*  "
        f"— {brand:,} bought this brand · {typ:,} bought this type\n"
        f"> brand: `{profile['brand'] or '—'}`   type: `{profile['category'] or '—'}`\n"
        f"> top countries: {top}\n"
        f"CSV: all {len(rows):,} buyers, ranked best-first "
        f"(tier 5 = this brand+type, 4 = this brand, 3 = this type).\n"
        f"{_tag(profile)}"
    )
    filename = (re.sub(r"[^a-zA-Z0-9]+", "_", info["title"])[:50] or "leads") + "_leads.csv"
    return {"info": info, "profile": profile, "rows": rows,
            "summary": summary, "csv": _csv_bytes(rows), "filename": filename}

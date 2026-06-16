"""
Shared logic: turn a ucymachines listing URL into the Slack reply
(summary text + CSV bytes + filename). Used by both the live Socket Mode bot
(slackbot.py) and the scheduled cron poller (poll.py) so they never drift.
"""
import io, csv, re
from listing import parse_listing
from match import match

CSV_FIELDS = ["company", "first_name", "last_name", "email", "phone", "country",
              "city", "tier", "past_requests", "last_request", "relevance",
              "example_requests"]

def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        r = dict(r)
        r["example_requests"] = " | ".join(r.get("example_requests") or [])
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

def build_reply(url):
    """Returns {info, rows, summary, csv, filename}. csv/filename are None when
    there are no solid (tier>=2) matches."""
    info = parse_listing(url)
    all_rows = match(info["brand"], info["terms"], info["category"], min_tier=1)
    strong = sum(1 for r in all_rows if r["tier"] >= 4)       # brand / brand+category
    cat    = sum(1 for r in all_rows if r["tier"] in (2, 3))  # this machine type
    kw     = sum(1 for r in all_rows if r["tier"] == 1)       # weak keyword-only
    total  = strong + cat + kw
    rows   = [r for r in all_rows if r["tier"] >= 2]          # the CSV: genuine buyers

    if not rows:
        return {"info": info, "rows": [], "csv": None, "filename": None,
                "summary": (f":mag: No solid matches for *{info['title']}* "
                            f"({kw} weak keyword-only leads exist).")}

    countries = {}
    for r in rows:
        c = (r.get("country") or "?").strip() or "?"
        countries[c] = countries.get(c, 0) + 1
    top = ", ".join(f"{c} ({n})" for c, n in
                    sorted(countries.items(), key=lambda x: -x[1])[:5])
    headline = (f":dart: *{total:,} matched buyers* for *{info['title']}*  "
                f"— {strong:,} brand · {cat:,} category"
                + (f" · {kw:,} keyword-only (excluded)" if kw else ""))
    summary = (
        f"{headline}\n"
        f"> brand: `{info['brand'] or '—'}`   category: `{info['category'] or '—'}`\n"
        f"> top countries: {top}\n"
        f"CSV: all {len(rows):,} genuine buyers (tier ≥ 2), best-first. "
        f"Columns: company, contact, email, phone, tier, past requests, what they asked about.\n"
        f"> _tiers: 5=bought this brand+type · 4=this brand · 3=this type+keyword · 2=this type_"
    )
    filename = (re.sub(r"[^a-zA-Z0-9]+", "_", info["title"])[:50] or "leads") + "_leads.csv"
    return {"info": info, "rows": rows, "summary": summary,
            "csv": _csv_bytes(rows), "filename": filename}

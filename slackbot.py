"""
Slack bot (Socket Mode): post a ucymachines.com listing link in the channel and
the bot replies in-thread with a ranked CSV of historic leads who could buy it.

Run:  python3 slackbot.py
Needs SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in .env.
"""
import os, re, csv, io, tempfile, traceback
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from listing import parse_listing
from match import match, count_by_tier, tier_summary

load_dotenv()

LISTING_RE = re.compile(r"https?://(?:www\.)?ucymachines\.com/listings/\S+")
MAX_ROWS = 10000  # CSV cap; tiering ensures strong matches are never sliced first

app = App(token=os.environ["SLACK_BOT_TOKEN"])

def make_csv_bytes(rows):
    buf = io.StringIO()
    fields = ["company", "first_name", "last_name", "email", "phone",
              "country", "city", "tier", "past_requests", "last_request",
              "relevance", "example_requests"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        r = dict(r)
        r["example_requests"] = " | ".join(r.get("example_requests") or [])
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

@app.event("message")
def on_message(event, say, client, logger):
    # ignore bot's own messages, edits, threads-of-threads
    if event.get("bot_id") or event.get("subtype"):
        return
    text = event.get("text", "") or ""
    m = LISTING_RE.search(text)
    if not m:
        return
    url = m.group(0).rstrip(">").rstrip(".,")
    ch = event["channel"]
    ts = event["ts"]

    try:
        client.reactions_add(channel=ch, timestamp=ts, name="hourglass_flowing_sand")
    except Exception:
        pass

    try:
        info = parse_listing(url)
        # honest pool size, broken down by tier (whole pool, no cap)
        counts = count_by_tier(info["brand"], info["terms"], info["category"])
        total, strong, cat, kw = tier_summary(counts)
        # the CSV: strong + category tiers (>=2), capped; keyword-only excluded by default
        rows = match(info["brand"], info["terms"], info["category"], MAX_ROWS, min_tier=2)
        if not rows:
            say(thread_ts=ts,
                text=(f":mag: No solid matches for *{info['title']}* "
                      f"({kw} weak keyword-only leads exist — reply `full` if you want them)."))
            return

        countries = {}
        for r in rows:
            c = (r.get("country") or "?").strip() or "?"
            countries[c] = countries.get(c, 0) + 1
        top_countries = ", ".join(f"{c} ({n})" for c, n in
                                  sorted(countries.items(), key=lambda x: -x[1])[:5])

        # honest headline: true total + tier breakdown; note if the cap truncated
        headline = (f":dart: *{total:,} matched buyers* for *{info['title']}*  "
                    f"— {strong:,} brand · {cat:,} category"
                    + (f" · {kw:,} keyword-only (excluded)" if kw else ""))
        in_csv = len(rows)
        csv_note = f"CSV: top {in_csv:,} (tier ≥ 2, ranked by relevance)."
        if in_csv < strong + cat:
            csv_note += f" {strong + cat - in_csv:,} more matched than the {MAX_ROWS:,} cap — ask to widen."
        summary = (
            f"{headline}\n"
            f"> brand: `{info['brand'] or '—'}`   category: `{info['category'] or '—'}`\n"
            f"> top countries: {top_countries}\n"
            f"{csv_note} Columns: company, contact, email, phone, tier, past requests, what they asked about.\n"
            f"> _tiers: 5=bought this brand+type · 4=this brand · 3=this type+keyword · 2=this type_"
        )
        data = make_csv_bytes(rows)
        fname = (re.sub(r"[^a-zA-Z0-9]+", "_", info["title"])[:50] or "leads") + "_leads.csv"
        client.files_upload_v2(channel=ch, thread_ts=ts,
                               filename=fname, content=data, initial_comment=summary)
    except Exception as e:
        logger.error(traceback.format_exc())
        say(thread_ts=ts, text=f":warning: Error matching that listing: `{e}`")
    finally:
        # Only clear the processing hourglass. We deliberately do NOT add a
        # green check — that reaction is reserved for the user to mark a
        # listing as "mailed" once they've emailed the matched leads.
        try:
            client.reactions_remove(channel=ch, timestamp=ts, name="hourglass_flowing_sand")
        except Exception:
            pass

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ UCY lead bot running (Socket Mode). Paste a ucymachines listing link in the channel.")
    handler.start()

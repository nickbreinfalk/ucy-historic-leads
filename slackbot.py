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
from match import match

load_dotenv()

LISTING_RE = re.compile(r"https?://(?:www\.)?ucymachines\.com/listings/\S+")
MAX_ROWS = 2000  # cap per CSV

app = App(token=os.environ["SLACK_BOT_TOKEN"])

def make_csv_bytes(rows):
    buf = io.StringIO()
    fields = ["company", "first_name", "last_name", "email", "phone",
              "country", "city", "past_requests", "last_request",
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
        rows = match(info["brand"], info["terms"], info["category"], MAX_ROWS)
        if not rows:
            say(thread_ts=ts, text=f":mag: No historic leads matched *{info['title']}*.")
            return

        countries = {}
        for r in rows:
            c = (r.get("country") or "?").strip() or "?"
            countries[c] = countries.get(c, 0) + 1
        top_countries = ", ".join(f"{c} ({n})" for c, n in
                                  sorted(countries.items(), key=lambda x: -x[1])[:5])

        summary = (
            f":dart: *{len(rows)} potential buyers* for *{info['title']}*\n"
            f"> brand: `{info['brand'] or '—'}`   category: `{info['category'] or '—'}`\n"
            f"> top countries: {top_countries}\n"
            f"CSV attached — company, contact, email, phone, what they previously asked about, ranked by relevance."
        )
        data = make_csv_bytes(rows)
        fname = (re.sub(r"[^a-zA-Z0-9]+", "_", info["title"])[:50] or "leads") + "_leads.csv"
        client.files_upload_v2(channel=ch, thread_ts=ts,
                               filename=fname, content=data, initial_comment=summary)
    except Exception as e:
        logger.error(traceback.format_exc())
        say(thread_ts=ts, text=f":warning: Error matching that listing: `{e}`")
    finally:
        try:
            client.reactions_remove(channel=ch, timestamp=ts, name="hourglass_flowing_sand")
            client.reactions_add(channel=ch, timestamp=ts, name="white_check_mark")
        except Exception:
            pass

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ UCY lead bot running (Socket Mode). Paste a ucymachines listing link in the channel.")
    handler.start()

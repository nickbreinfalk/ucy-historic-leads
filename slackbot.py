"""
Live Slack bot (Socket Mode): post a ucymachines.com listing link and the bot
replies in-thread with a ranked CSV. This is the INSTANT-reply variant (needs an
always-on host). For the free GitHub-Actions cron variant, see poll.py.

Run:  python3 slackbot.py
Needs SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in .env.
"""
import os, re, traceback
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from core import build_reply

load_dotenv()
LISTING_RE = re.compile(r"https?://(?:www\.)?ucymachines\.com/listings/\S+")
app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.event("message")
def on_message(event, say, client, logger):
    if event.get("bot_id") or event.get("subtype"):   # ignore bot/system/edits
        return
    m = LISTING_RE.search(event.get("text", "") or "")
    if not m:
        return
    url = m.group(0).rstrip(">").rstrip(".,")
    ch, ts = event["channel"], event["ts"]
    try:
        client.reactions_add(channel=ch, timestamp=ts, name="hourglass_flowing_sand")
    except Exception:
        pass
    try:
        res = build_reply(url)
        if not res["rows"]:
            say(thread_ts=ts, text=res["summary"])
        else:
            client.files_upload_v2(channel=ch, thread_ts=ts, filename=res["filename"],
                                   content=res["csv"], initial_comment=res["summary"])
    except Exception as e:
        logger.error(traceback.format_exc())
        say(thread_ts=ts, text=f":warning: Error matching that listing: `{e}`")
    finally:
        # clear the processing hourglass; the green check is reserved for the
        # user to mark a listing as "mailed".
        try:
            client.reactions_remove(channel=ch, timestamp=ts, name="hourglass_flowing_sand")
        except Exception:
            pass

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ UCY lead bot running (Socket Mode). Paste a ucymachines listing link in the channel.")
    handler.start()

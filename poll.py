"""
Scheduled poller (runs on GitHub Actions cron). Every run:
  1. reads channel messages newer than the last processed timestamp,
  2. for each new message containing a ucymachines listing link, replies
     in-thread with the ranked CSV,
  3. advances the stored timestamp in Supabase (bot_state) so nothing is
     processed twice and nothing is missed between runs.

No always-on process needed — fits the same GitHub-Actions model as ucy-keyaccount.
Env: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, SUPABASE_DB_URL.
"""
import os, re, traceback, psycopg
from dotenv import load_dotenv
from slack_sdk import WebClient

from core import build_reply

load_dotenv()
LISTING_RE = re.compile(r"https?://(?:www\.)?ucymachines\.com/listings/\S+")
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
CHANNEL = os.environ["SLACK_CHANNEL_ID"]
DB = os.environ["SUPABASE_DB_URL"]

def get_last_ts(conn):
    # bot_state is created once via migrations/001_security.sql — no DDL here
    # (bot_app is least-privilege and intentionally cannot CREATE).
    row = conn.execute("select last_ts from bot_state where channel=%s", (CHANNEL,)).fetchone()
    return row[0] if row else "0"

def set_last_ts(conn, ts):
    conn.execute(
        "insert into bot_state(channel,last_ts) values(%s,%s) "
        "on conflict(channel) do update set last_ts=excluded.last_ts",
        (CHANNEL, ts))

def handle(msg):
    text = msg.get("text", "") or ""
    m = LISTING_RE.search(text)
    if not m:
        return
    url = m.group(0).rstrip(">").rstrip(".,")
    ts = msg["ts"]
    try:
        client.reactions_add(channel=CHANNEL, timestamp=ts, name="hourglass_flowing_sand")
    except Exception:
        pass
    try:
        res = build_reply(url)
        if not res["rows"]:
            client.chat_postMessage(channel=CHANNEL, thread_ts=ts, text=res["summary"])
        else:
            client.files_upload_v2(channel=CHANNEL, thread_ts=ts,
                                   filename=res["filename"], content=res["csv"],
                                   initial_comment=res["summary"])
    except Exception:
        traceback.print_exc()  # full detail stays in the (private) Actions log
        client.chat_postMessage(channel=CHANNEL, thread_ts=ts,
                                text=":warning: Couldn't process that listing — check the run logs.")
    finally:
        try:
            client.reactions_remove(channel=CHANNEL, timestamp=ts, name="hourglass_flowing_sand")
        except Exception:
            pass

def main():
    with psycopg.connect(DB, autocommit=True) as conn:
        last = get_last_ts(conn)
        resp = client.conversations_history(channel=CHANNEL, oldest=last,
                                            inclusive=False, limit=200)
        msgs = sorted(resp.get("messages", []), key=lambda m: float(m["ts"]))
        newest = last
        for msg in msgs:
            if not (msg.get("subtype") or msg.get("bot_id")):  # skip bot/system msgs
                handle(msg)
            if float(msg["ts"]) > float(newest):
                newest = msg["ts"]
        if newest != last:
            set_last_ts(conn, newest)
        print(f"processed up to ts={newest} ({len(msgs)} new messages)")

if __name__ == "__main__":
    main()

"""
Monthly reminder: posts to the channel and @-mentions the owner to upload the
latest leads from the Machinio system. Fired by .github/workflows/monthly_reminder.yml
on the 1st of each month.
"""
import os
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
CHANNEL = os.environ["SLACK_CHANNEL_ID"]
USER = os.environ["SLACK_USER_ID"]

MESSAGE = (
    f":calendar: <@{USER}> — *Monthly lead drop!* :inbox_tray:\n"
    f"Time to export the latest leads from the *Machinio system* and upload them, "
    f"so the historic-leads database stays current and the matcher keeps surfacing fresh leads."
)

if __name__ == "__main__":
    resp = client.chat_postMessage(channel=CHANNEL, text=MESSAGE)
    print("reminder posted, ts=", resp["ts"])

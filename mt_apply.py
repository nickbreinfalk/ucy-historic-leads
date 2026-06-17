#!/usr/bin/env python3
"""Apply the agent's classifications: read a {title: machine_type} JSON file and
write machine_type back to leads. Pairs with mt_fetch.py."""
import os, sys, json, psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]
data = json.load(open(sys.argv[1]))  # {title: machine_type}
c = psycopg.connect(url, autocommit=True)
c.execute("set statement_timeout = '0'")  # instance is IO-throttled; don't get cancelled
rows = 0
with c.cursor() as cur:
    for title, mt in data.items():
        mt = (mt or "").strip()
        if not mt:
            continue
        cur.execute(
            "update leads set machine_type=%s where listing_title=%s and machine_type is null",
            (mt, title),
        )
        rows += cur.rowcount
left = c.execute("select count(distinct listing_title) from leads where machine_type is null").fetchone()[0]
print(f"applied {len(data)} titles -> {rows} rows updated; distinct untyped titles left: {left:,}")

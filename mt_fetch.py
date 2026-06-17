#!/usr/bin/env python3
"""Print the next N distinct UNTYPED listing titles as a JSON array, for the
agent (running on the Claude Code subscription) to classify into machine_type.
Pairs with mt_apply.py. No API — the model doing the classifying is this session."""
import os, sys, json, psycopg
from dotenv import load_dotenv

load_dotenv()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
url = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]
c = psycopg.connect(url, autocommit=True)
rows = c.execute(
    "select listing_title from leads where machine_type is null "
    "group by listing_title order by count(*) desc, listing_title limit %s", (N,)
).fetchall()
print(json.dumps([r[0] for r in rows], ensure_ascii=False))

#!/usr/bin/env python3
"""One-time read: dump every untyped distinct listing_title (with how many leads
share it) to a local CSV, so classification can happen on the Mac with zero
Supabase round-trips. Highest-frequency titles first."""
import os, csv, psycopg
from dotenv import load_dotenv

load_dotenv()
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "untyped_titles.csv")
url = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]
c = psycopg.connect(url, autocommit=True)
c.execute("set statement_timeout = '0'")
rows = c.execute(
    "select listing_title, count(*) from leads where machine_type is null "
    "group by listing_title order by count(*) desc, listing_title"
).fetchall()
with open(OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["listing_title", "freq"])
    for t, n in rows:
        w.writerow([t, n])
print(f"exported {len(rows):,} untyped distinct titles -> {OUT}")

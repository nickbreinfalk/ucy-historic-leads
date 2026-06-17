#!/usr/bin/env python3
"""Apply the Sonnet-rescued machine_types (data/rescued_types.csv) for titles that
were 'unknown': (1) update the local source-of-truth title_types.csv, (2) push to
Supabase, overwriting ONLY rows where machine_type='unknown'."""
import os, csv, psycopg
from dotenv import load_dotenv

load_dotenv("/Users/nickbreinfalk/historic-machinery-leads/.env")
csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "data", "title_types.csv")
RESCUED = os.path.join(HERE, "data", "rescued_types.csv")

rescued = {}
for row in csv.reader(open(RESCUED, encoding="utf-8")):
    if len(row) >= 2 and row[1].strip():
        rescued[row[0]] = row[1].strip().lower()
print(f"rescued labels: {len(rescued):,}")

# 1. update local title_types.csv in place
rows = [r[:2] for r in csv.reader(open(TYPES, encoding="utf-8")) if len(r) >= 2]
changed = 0
for r in rows:
    if r[1] == "unknown" and r[0] in rescued:
        r[1] = rescued[r[0]]; changed += 1
with open(TYPES, "w", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows(rows)
print(f"local title_types.csv: {changed:,} rows updated unknown -> real type")

# 2. push to Supabase, overwriting only the still-unknown rows
url = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]
c = psycopg.connect(url, autocommit=True)
c.execute("set statement_timeout = '0'")
c.execute("create temp table tt (title text primary key, mt text)")
with c.cursor() as cur:
    with cur.copy("copy tt (title, mt) from stdin") as cp:
        for t, mt in rescued.items():
            cp.write_row((t, mt))
print("staged; running UPDATE (machine_type='unknown' only)...")
cur = c.execute(
    "update leads set machine_type = tt.mt from tt "
    "where leads.listing_title = tt.title and leads.machine_type = 'unknown'"
)
print(f"updated {cur.rowcount:,} lead rows")

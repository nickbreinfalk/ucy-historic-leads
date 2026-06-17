#!/usr/bin/env python3
"""One-shot bulk upload: push every locally-classified title->machine_type from
data/title_types.csv back to Supabase in a SINGLE write (temp table + COPY +
UPDATE join). Run this when classification is done (or periodically). Idempotent:
only fills rows where machine_type is still null."""
import os, csv, psycopg
from dotenv import load_dotenv

load_dotenv()
csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
DONE = os.path.join(HERE, "data", "title_types.csv")
url = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]

pairs = []
seen = set()
for row in csv.reader(open(DONE, encoding="utf-8")):
    if len(row) >= 2 and row[0] not in seen:
        seen.add(row[0])
        pairs.append((row[0], row[1]))
print(f"loaded {len(pairs):,} classified titles from local CSV")

c = psycopg.connect(url, autocommit=True)
c.execute("set statement_timeout = '0'")
# NB: no "on commit drop" — under autocommit each statement commits on its own,
# which would drop the temp table before COPY runs. Temp tables are session-lived
# by default (ON COMMIT PRESERVE ROWS) and vanish when the connection closes.
c.execute("create temp table tt (title text primary key, mt text)")
with c.cursor() as cur:
    with cur.copy("copy tt (title, mt) from stdin") as cp:
        for t, mt in pairs:
            cp.write_row((t, mt))
print("staged into temp table; running UPDATE join...")
cur = c.execute(
    "update leads set machine_type = tt.mt from tt "
    "where leads.listing_title = tt.title and leads.machine_type is null"
)
print(f"updated {cur.rowcount:,} lead rows")
typed, total = c.execute(
    "select count(machine_type), count(*) from leads"
).fetchone()
print(f"typed now: {typed:,}/{total:,} ({100*typed//total}%)")

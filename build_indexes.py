"""
Build search indexes on the already-loaded `leads` table.
Uses expression indexes (no stored columns) to avoid a full table rewrite.
Builds one at a time and reports size after each.
"""
import os, time, psycopg
from dotenv import load_dotenv

load_dotenv()

INDEXES = [
    ("leads_brand_key_idx", "create index if not exists leads_brand_key_idx on leads (lower(brand))"),
    ("leads_category_idx",  "create index if not exists leads_category_idx on leads (category)"),
    ("leads_email_idx",     "create index if not exists leads_email_idx on leads (email)"),
    ("leads_title_fts_idx", "create index if not exists leads_title_fts_idx on leads using gin (to_tsvector('simple', listing_title))"),
    ("leads_title_trgm_idx","create index if not exists leads_title_trgm_idx on leads using gin (listing_title gin_trgm_ops)"),
]

def main():
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], autocommit=True) as conn:
        conn.execute("create extension if not exists pg_trgm")
        conn.execute("set maintenance_work_mem = '256MB'")
        for name, sql in INDEXES:
            t0 = time.time()
            print(f"building {name} ...", flush=True)
            conn.execute(sql)
            size = conn.execute(f"select pg_size_pretty(pg_relation_size('{name}'))").fetchone()[0]
            print(f"  done in {time.time()-t0:.0f}s, size {size}", flush=True)
        conn.execute("analyze leads")
        total = conn.execute("select pg_size_pretty(pg_total_relation_size('leads'))").fetchone()[0]
        dbsz = conn.execute("select pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        print(f"\nALL DONE. leads (table+indexes): {total} | database total: {dbsz}")

if __name__ == "__main__":
    main()

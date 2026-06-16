"""
Load data/clean.csv into Supabase Postgres with search-ready indexes:
  - brand_key (lowercased brand)  -> exact/grouped brand matching
  - pg_trgm GIN on listing_title  -> fast ILIKE '%term%' substring search
  - tsvector GIN (title_fts)      -> ranked full-text keyword search
"""
import os, time, psycopg
from dotenv import load_dotenv

load_dotenv()
HERE = os.path.dirname(os.path.abspath(__file__))
CLEAN = os.path.join(HERE, "data", "clean.csv")
# admin task (DROP/CREATE/COPY) — needs the superuser credential, not bot_app
URL = os.environ.get("SUPABASE_ADMIN_DB_URL") or os.environ["SUPABASE_DB_URL"]

DDL = """
create extension if not exists pg_trgm;

drop table if exists leads;
create table leads (
    id            bigserial primary key,
    email         text not null,
    company       text,
    first_name    text,
    last_name     text,
    phone         text,
    country       text,
    city          text,
    listing_title text not null,
    brand         text,
    category      text,
    created_date  date
);
"""

POST = """
-- lowercased brand for case-insensitive grouping/matching
alter table leads add column brand_key text generated always as (lower(brand)) stored;
-- full-text vector over the title for ranked keyword search
alter table leads add column title_fts tsvector
    generated always as (to_tsvector('simple', coalesce(listing_title,''))) stored;

create index leads_brand_key_idx on leads (brand_key);
create index leads_category_idx  on leads (category);
create index leads_email_idx     on leads (email);
create index leads_title_trgm_idx on leads using gin (listing_title gin_trgm_ops);
create index leads_title_fts_idx  on leads using gin (title_fts);
analyze leads;
"""

def main():
    t0 = time.time()
    with psycopg.connect(URL, autocommit=True) as conn:
        print("creating table...")
        conn.execute(DDL)

        print("COPY loading clean.csv (this is the slow part)...")
        cols = "email,company,first_name,last_name,phone,country,city,listing_title,brand,category,created_date"
        copy_sql = f"copy leads ({cols}) from stdin with (format csv, header true, null '')"
        with open(CLEAN, "r", encoding="utf-8") as f, conn.cursor() as cur:
            with cur.copy(copy_sql) as cp:
                while chunk := f.read(1 << 20):
                    cp.write(chunk)
        n = conn.execute("select count(*) from leads").fetchone()[0]
        print(f"  loaded {n:,} rows in {time.time()-t0:.0f}s")
        print("Now run build_indexes.py to (re)build the expression indexes.")

if __name__ == "__main__":
    main()

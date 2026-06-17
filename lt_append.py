#!/usr/bin/env python3
"""Local apply: append a {title: machine_type} JSON object to
data/title_types.csv. No DB — purely local. Pairs with lt_fetch.py.
Run lt_upload.py at the end to push everything to Supabase in one write."""
import os, sys, json, csv

HERE = os.path.dirname(os.path.abspath(__file__))
DONE = os.path.join(HERE, "data", "title_types.csv")
data = json.load(open(sys.argv[1], encoding="utf-8"))

with open(DONE, "a", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    for title, mt in data.items():
        w.writerow([title, (mt or "unknown").strip().lower()])

# progress
total_done = sum(1 for r in csv.reader(open(DONE, encoding="utf-8")) if r)
print(f"appended {len(data)} -> title_types.csv ({total_done:,}/509,717 classified locally)")

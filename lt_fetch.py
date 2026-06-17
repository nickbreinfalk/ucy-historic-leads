#!/usr/bin/env python3
"""Local fetch: next N untyped titles (highest-freq first) NOT yet classified
locally. Reads data/untyped_titles.csv minus data/title_types.csv. No DB."""
import os, sys, csv, json

csv.field_size_limit(10**8)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
HERE = os.path.dirname(os.path.abspath(__file__))
UNTYPED = os.path.join(HERE, "data", "untyped_titles.csv")
DONE = os.path.join(HERE, "data", "title_types.csv")

done = set()
if os.path.exists(DONE):
    for row in csv.reader(open(DONE, encoding="utf-8")):
        if row:
            done.add(row[0])

out = []
with open(UNTYPED, encoding="utf-8") as f:
    r = csv.reader(f)
    next(r, None)  # header
    for row in r:
        if not row:
            continue
        if row[0] not in done:
            out.append(row[0])
            if len(out) >= N:
                break
print(json.dumps(out, ensure_ascii=False))

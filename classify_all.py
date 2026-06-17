#!/usr/bin/env python3
"""Bulk-classify every untyped listing title into a brand-free machine_type using
Haiku via the API. Reads data/untyped_titles.csv, writes data/title_types.csv
incrementally (resumable). Run lt_upload.py afterwards to push to Supabase.

Usage:
    python3 classify_all.py            # classify everything still untyped
    python3 classify_all.py --limit 150   # smoke test: only first 150 titles
"""
import os, sys, csv, json, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import anthropic, httpx

load_dotenv("/Users/nickbreinfalk/historic-machinery-leads/.env")
csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
UNTYPED = os.path.join(HERE, "data", "untyped_titles.csv")
DONE = os.path.join(HERE, "data", "title_types.csv")

MODEL = "claude-haiku-4-5"
BATCH = 60
WORKERS = 120
IN_PRICE = 1.0 / 1_000_000   # $/token
OUT_PRICE = 5.0 / 1_000_000

LIMIT = None
if "--limit" in sys.argv:
    LIMIT = int(sys.argv[sys.argv.index("--limit") + 1])

client = anthropic.Anthropic(
    max_retries=12,
    http_client=httpx.Client(
        limits=httpx.Limits(max_connections=250, max_keepalive_connections=250),
        timeout=httpx.Timeout(120.0),
    ),
)

SYSTEM = """You classify used industrial machinery listings. For each numbered title, output a concise lowercase machine_type: the generic CATEGORY of machine, 1-4 words.

Rules:
- Describe the KIND of machine, never the brand, model, year, location, serial, or capacity. Use the brand only as a clue to infer the type (Schramm -> drilling rig, Handtmann -> vacuum filler, Trumpf laser -> laser cutting machine, Gallus -> label printing press).
- Use industry-standard generic terms that match across brands: "cnc lathe", "vertical machining center", "press brake", "fiber laser cutting machine", "flexographic printing press", "injection molding machine", "drilling rig", "excavator", "vacuum filler", "depositor".
- Be specific enough to be useful but generic enough to match across brands: prefer "vertical machining center" over "cnc machine"; "press brake" over "metal forming machine".
- If the title is junk, a placeholder, deleted/empty, spare parts only, or you genuinely cannot tell the machine, output "unknown".
- Lowercase. No brand names, no years, no locations, no punctuation beyond hyphens.

Respond with ONLY a JSON array of lowercase machine_type strings, in the SAME ORDER as the numbered inputs and with EXACTLY one entry per input, e.g. ["cnc lathe","drilling rig","label printing press"]. No prose, no keys, no trailing commentary."""

_lock = threading.Lock()
stats = {"done": 0, "batches": 0, "in_tok": 0, "out_tok": 0, "fail": 0}


def load_done():
    done = set()
    if os.path.exists(DONE):
        for row in csv.reader(open(DONE, encoding="utf-8")):
            if row:
                done.add(row[0])
    return done


def extract_array(text):
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return arr if isinstance(arr, list) else None


def classify_batch(titles):
    """Return list of (title, machine_type). Strict: only accepts an array whose
    length matches the batch (guarantees correct title<->type alignment). On
    repeated mismatch, splits the batch so a bad response can never mislabel."""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    for attempt in range(5):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=SYSTEM,
                messages=[{"role": "user", "content":
                           f"Classify these {len(titles)} machine listing titles:\n\n{numbered}"}],
            )
        except Exception:
            time.sleep(2.0 * (attempt + 1))   # API hiccup / retry exhaustion — back off, try again
            continue
        with _lock:
            stats["in_tok"] += resp.usage.input_tokens
            stats["out_tok"] += resp.usage.output_tokens
        arr = extract_array("".join(b.text for b in resp.content if b.type == "text"))
        if arr is not None and len(arr) == len(titles):
            return [(t, str(mt).strip().lower() or "unknown") for t, mt in zip(titles, arr)]
        time.sleep(1.0 * (attempt + 1))
    # length never matched: split to isolate, so we never misalign labels
    if len(titles) > 1:
        mid = len(titles) // 2
        return classify_batch(titles[:mid]) + classify_batch(titles[mid:])
    with _lock:
        stats["fail"] += 1
    return [(titles[0], "unknown")]


def main():
    done = load_done()
    work = []
    with open(UNTYPED, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if row and row[0] not in done:
                work.append(row[0])
                if LIMIT and len(work) >= LIMIT:
                    break
    total = len(work)
    print(f"to classify: {total:,} titles  ({len(done):,} already done)  "
          f"batch={BATCH} workers={WORKERS} model={MODEL}", flush=True)
    if not total:
        print("nothing to do — all titles classified.")
        return

    batches = [work[i:i + BATCH] for i in range(0, total, BATCH)]
    out = open(DONE, "a", encoding="utf-8", newline="")
    writer = csv.writer(out)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(classify_batch, b): b for b in batches}
        for fut in as_completed(futs):
            try:
                pairs = fut.result()
            except Exception:
                pairs = [(t, "unknown") for t in futs[fut]]
                with _lock:
                    stats["fail"] += len(pairs)
            with _lock:
                for title, mt in pairs:
                    writer.writerow([title, mt or "unknown"])
                out.flush()
                stats["done"] += len(pairs)
                stats["batches"] += 1
                if stats["batches"] % 20 == 0 or stats["done"] >= total:
                    cost = stats["in_tok"] * IN_PRICE + stats["out_tok"] * OUT_PRICE
                    rate = stats["done"] / max(1e-9, time.time() - t0)
                    eta = (total - stats["done"]) / max(1e-9, rate) / 60
                    print(f"  {stats['done']:,}/{total:,}  "
                          f"({100*stats['done']//total}%)  "
                          f"${cost:,.2f}  {rate:,.0f}/s  eta {eta:,.0f}m  "
                          f"fails {stats['fail']}", flush=True)
    out.close()
    cost = stats["in_tok"] * IN_PRICE + stats["out_tok"] * OUT_PRICE
    print(f"\nDONE. classified {stats['done']:,} titles in "
          f"{(time.time()-t0)/60:,.1f}m. tokens in={stats['in_tok']:,} "
          f"out={stats['out_tok']:,}  total cost ${cost:,.2f}  "
          f"unknown/fails {stats['fail']:,}", flush=True)


if __name__ == "__main__":
    main()

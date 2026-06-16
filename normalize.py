"""
Stream the raw Salesforce Lead.csv, deduplicate on (email, listing_title),
heuristically extract brand + machine category from the listing title,
and write a lean clean.csv ready to COPY into Postgres.

No LLM, no DB writes. Just measures and produces data/clean.csv.
"""
import csv, re, sys, os

csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "Lead.csv")
OUT = os.path.join(HERE, "data", "clean.csv")

YEAR_RE = re.compile(r"^\s*(19|20)\d{2}\b\s*")
LOCATION_TAIL_RE = re.compile(r"\s+in\s+[A-Z].*$")  # strip " in Chicago, IL" tail
# leading noise tokens (quantities, units, filler) that precede the real brand
NOISE_TOKEN_RE = re.compile(
    r"^(used|new|for|sale|the|a|ton|tons|tonne|gallon|gallons|gal|kg|mm|cm|hp|kw|kva|"
    r"micron|microns|sqm|gpm|gph|lb|lbs|inch|piece|pieces|set|lot|approx|ca|model|"
    r"type|complete|line|plant|system|unit|machine|cnc)$", re.I)

# Machine-category keyword dictionary. First match wins (order = priority).
# Keys are clean category labels; values are lowercase substrings to look for.
CATEGORY_RULES = [
    ("laser cutting",        ["laser cut", "fiber laser", "fibre laser", "laser cnc", "trulaser", "bystar", "bysprint"]),
    ("press brake",          ["press brake", "abkant", "pressbrake", "trumabend", "bystronic xpert", "gasparini"]),
    ("punching",             ["punch press", "punching", "trupunch", "nibbler"]),
    ("waterjet",             ["waterjet", "water jet"]),
    ("plasma cutting",       ["plasma cut", "plasma cnc"]),
    ("guillotine shear",     ["guillotine", "shear", "schere"]),
    ("CMM / measuring",      ["cmm", "coordinate measuring", "smartscope", "measuring machine", "metrolog", "zeiss contura", "video measuring"]),
    ("machining center",     ["machining center", "machining centre", "bearbeitungszentrum", "vmc", "hmc", "5-axis", "5 axis"]),
    ("lathe / turning",      ["lathe", "turning center", "turning centre", "cnc turn", "drehmaschine", "doosan puma", "okuma lb"]),
    ("milling",              ["milling machine", "mill ", "fraes", "fräs", "universal mill"]),
    ("grinding",             ["grinding", "grinder", "schleif"]),
    ("EDM",                  ["edm", "wire erosion", "sinker", "die sink", "erodier"]),
    ("injection molding",    ["injection mold", "injection mould", "spritzgie", "arburg", "engel ", "krauss maffei"]),
    ("press / stamping",     ["stamping", "eccentric press", "hydraulic press", "exzenter", "stanz"]),
    ("welding",              ["welding", "schweiss", "schweiß", "welder"]),
    ("bending / rolling",    ["plate roll", "bending roll", "section bend", "rundbieg", "profilbieg"]),
    ("sawing",               ["band saw", "bandsaw", "cold saw", "saege", "säge"]),
    ("woodworking",          ["woodworking", "panel saw", "edgebander", "holzbearbeit", "cnc router wood"]),
    ("forklift / handling",  ["forklift", "stapler", "gabelstapler", "pallet truck"]),
    ("compressor",           ["compressor", "kompressor"]),
    ("generator",            ["generator", "genset"]),
]

def extract_brand(title):
    """Best-effort brand: drop leading year + the ' in <location>' tail, skip
    leading quantity/unit/filler tokens, then take the first 1-2 name-like words."""
    t = YEAR_RE.sub("", title).strip()
    t = LOCATION_TAIL_RE.sub("", t).strip()
    # cut at first comma (legal suffix / location) so we don't grab the whole line
    t = t.split(",")[0].strip()
    tokens = re.split(r"\s+", t)
    # drop leading noise / numeric / unit tokens
    while tokens and (NOISE_TOKEN_RE.match(tokens[0]) or re.fullmatch(r"[\d.\-/#x\"]+", tokens[0] or "")):
        tokens.pop(0)
    if not tokens:
        return ""
    brand = tokens[0]
    # machine-type acronyms/words that are NOT part of a brand name
    TYPE_WORDS = {"cmm", "cnc", "edm", "vmc", "hmc", "laser", "press", "lathe",
                  "mill", "milling", "grinder", "saw", "punch", "router", "boiler"}
    # append a 2nd word only if it's clearly part of a name (alphabetic, no digits)
    if (len(tokens) > 1 and re.fullmatch(r"[A-Za-z&]+", tokens[1] or "")
            and not NOISE_TOKEN_RE.match(tokens[1])
            and tokens[1].lower() not in TYPE_WORDS):
        if len(brand) <= 4 or tokens[1][0].isupper():
            brand = brand + " " + tokens[1]
    # drop obvious garbage (pure punctuation, redaction markers)
    if brand.upper() in ("[DELETED]", "WE", "404") or not re.search(r"[A-Za-z]", brand):
        return ""
    return brand[:60]

def extract_category(title):
    low = title.lower()
    for label, kws in CATEGORY_RULES:
        for kw in kws:
            if kw in low:
                return label
    return ""

def main():
    seen = set()
    total = kept = 0
    with open(RAW, encoding="utf-8", errors="replace", newline="") as fin, \
         open(OUT, "w", encoding="utf-8", newline="") as fout:
        r = csv.DictReader(fin)
        w = csv.writer(fout)
        w.writerow(["email", "company", "first_name", "last_name", "phone",
                    "country", "city", "listing_title", "brand", "category", "created_date"])
        for row in r:
            total += 1
            email = (row.get("Email") or "").strip().lower()
            title = (row.get("Listing_Title__c") or "").strip()
            if not email or not title:
                continue
            key = (email, title)
            if key in seen:
                continue
            seen.add(key)
            w.writerow([
                email,
                (row.get("Company") or "").strip(),
                (row.get("FirstName") or "").strip(),
                (row.get("LastName") or "").strip(),
                (row.get("Phone") or "").strip(),
                (row.get("Country") or "").strip(),
                (row.get("City") or "").strip(),
                title,
                extract_brand(title),
                extract_category(title),
                (row.get("CreatedDate") or "").strip()[:10],
            ])
            kept += 1
            if total % 250000 == 0:
                print(f"  ...{total:,} read, {kept:,} kept", file=sys.stderr)
    sz = os.path.getsize(OUT) / 1e6
    print(f"DONE: read {total:,}, kept {kept:,} unique (email,title) rows")
    print(f"clean.csv = {sz:.0f} MB")

if __name__ == "__main__":
    main()

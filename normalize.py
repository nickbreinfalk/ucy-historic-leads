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

# Leading tokens that are NOT the manufacturer — quantities, units, filler, and
# machine-type/process words. We pop these off the front until a real brand
# surfaces (handles "Hydraulic press KOJIMA", "Gear shaping machine MAAG",
# "55 Ton Amada", "500mm CNC lathe HAAS").
LEADING_STRIP = {
    # filler / quantity / units
    "used", "new", "for", "sale", "the", "a", "an", "approx", "ca", "model",
    "type", "complete", "ton", "tons", "tonne", "tonnes", "gallon", "gallons",
    "gal", "kg", "mm", "cm", "hp", "kw", "kva", "kv", "cv", "mw", "hz", "khz",
    "mhz", "psi", "bar", "rpm", "micron", "microns", "sqm", "gpm", "gph", "lb",
    "lbs", "inch", "piece", "pieces", "set", "sets", "lot", "pcs", "qty",
    "no", "nr", "stock", "ref", "x",
    # generic machine / line nouns
    "machine", "machines", "machining", "line", "plant", "system", "systems",
    "unit", "units", "center", "centers", "centre", "centres", "equipment",
    "lathes", "mills", "grinders", "presses", "saws", "axis", "forming",
    "fabrication", "cutter",
    # type / process words + acronyms (the real brand precedes/follows them)
    "cnc", "cmm", "vmc", "hmc", "edm", "smt", "ems",
    "hydraulic", "pneumatic", "manual", "automatic", "semi", "universal",
    "vertical", "horizontal", "gear", "shaping", "shaper", "hobbing", "drilling",
    "boring", "milling", "mill", "turning", "lathe", "grinding", "grinder",
    "welding", "plasma", "laser", "press", "brake", "punch", "punching", "shear",
    "shearing", "saw", "sawing", "router", "boiler", "extruder", "extrusion",
    "printing", "packaging", "injection", "molding", "moulding", "bending",
    "rolling", "cutting", "sheet", "plate", "stamping",
}

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
    # gear cutting BEFORE milling/grinding so "gear grinding" -> gear, not grinding
    ("gear cutting",         ["gear hobbing", "gear shaper", "gear shaping", "gear cutting", "gear grinding", "hobbing machine", "gleason", "pfauter"]),
    ("milling",              ["milling machine", "milling", "mill", "fraes", "fräs", "universal mill"]),
    ("grinding",             ["grinding", "grinder", "schleif"]),
    ("EDM",                  ["edm", "wire erosion", "sinker", "die sink", "erodier", "charmilles", "agie"]),
    ("injection molding",    ["injection mold", "injection mould", "spritzgie", "arburg", "engel", "krauss maffei"]),
    ("extrusion / plastics", ["extruder", "extrusion", "blown film", "blow molding", "blow moulding", "thermoform", "pelletiz", "calender", "plastic granulat"]),
    ("press / stamping",     ["stamping", "eccentric press", "hydraulic press", "exzenter", "stanz"]),
    ("welding",              ["welding", "schweiss", "schweiß", "welder"]),
    ("bending / rolling",    ["plate roll", "bending roll", "section bend", "rundbieg", "profilbieg"]),
    ("sawing",               ["band saw", "bandsaw", "cold saw", "saege", "säge"]),
    ("woodworking",          ["woodworking", "panel saw", "edgebander", "holzbearbeit", "cnc router wood"]),
    ("printing",             ["printing press", "offset press", "offset printing", "sheet-fed", "sheetfed", "web offset", "flexo", "flexographic", "screen printing", "speedmaster", "heidelberg", "komori", "manroland", "gto 52", "rotogravure", "digital press"]),
    ("packaging",            ["form fill", "fill seal", "flow pack", "flowpack", "case packer", "palletiz", "shrink wrap", "blister pack", "cartoner", "tray seal", "traysealer", "labeller", "labeler", "multivac", "bagging machine"]),
    ("textile",              ["weaving", "loom", "weefmachine", "knitting", "spinning machine", "textile", "rapier loom", "picanol", "dornier loom", "warping", "carding"]),
    ("forklift / handling",  ["forklift", "stapler", "gabelstapler", "pallet truck"]),
    ("compressor",           ["compressor", "kompressor"]),
    ("generator",            ["generator", "genset"]),
]

# Short acronyms / collision-prone words need BOTH boundaries (\bedm\b so it
# can't match 'Speedmaster' or 'Edmunds', \bmill\b so it can't match 'Miller').
# Everything else is treated as a stem with a leading boundary only, so
# 'plasma cut' still matches 'plasma cutting' and 'metrolog' matches 'metrology'.
WHOLE_WORD = {"edm", "cmm", "vmc", "hmc", "cnc", "mill", "saw", "mw", "ems", "smt"}

def _kw_regex(kw):
    esc = re.escape(kw)
    return r"\b" + esc + (r"\b" if kw.lower() in WHOLE_WORD else "")

CATEGORY_PATTERNS = [
    (label, re.compile("|".join(_kw_regex(k) for k in kws), re.I))
    for label, kws in CATEGORY_RULES
]

def _is_strip_token(tok):
    """True if a leading token is noise/unit/type and should be popped."""
    if not tok:
        return True
    if re.match(r"^\d", tok):                       # starts with a digit: 500mm, 55, 12in
        return True
    low = tok.lower().strip(".,&-#/\"'()")
    if len(low) <= 1:                               # stray single chars: 'x', '#'
        return True
    return low in LEADING_STRIP

def extract_brand(title):
    """Best-effort brand: drop leading year + ' in <location>' tail, then pop
    leading quantity/unit/type tokens until the manufacturer surfaces."""
    t = YEAR_RE.sub("", title).strip()
    t = LOCATION_TAIL_RE.sub("", t).strip()
    t = t.split(",")[0].strip()                      # cut legal suffix / location
    tokens = [tok for tok in re.split(r"\s+", t) if tok]
    while tokens and _is_strip_token(tokens[0]):     # pop chains of leading noise/type
        tokens.pop(0)
    if not tokens:
        return ""
    brand = tokens[0].strip(".,&-#/\"'()")
    # append a 2nd word only if clearly part of a name (alphabetic, not a strip word)
    if (len(tokens) > 1 and re.fullmatch(r"[A-Za-z&]+", tokens[1] or "")
            and tokens[1].lower() not in LEADING_STRIP):
        if len(brand) <= 4 or tokens[1][0].isupper():
            brand = (brand + " " + tokens[1]).strip()
    if (brand.upper() in ("[DELETED]", "WE", "404", "N/A", "NA")
            or len(brand) <= 1 or not re.search(r"[A-Za-z]", brand)):
        return ""
    return brand[:60]

def extract_category(title):
    """Word-boundary match on the title with the location tail removed (so a
    machine located 'in Heidelberg' isn't mislabeled as a printing press)."""
    t = LOCATION_TAIL_RE.sub("", title)
    for label, pat in CATEGORY_PATTERNS:
        if pat.search(t):
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

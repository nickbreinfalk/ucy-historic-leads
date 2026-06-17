#!/usr/bin/env python3
"""Turn the brand-registry research swarm output into data/registry.json.

Produces three deterministic guard structures used by the matcher:
  brand_domain : full-brand-token (lowercase) -> industry domain   (single-domain only)
  domain_first : unambiguous FIRST word         -> domain           (first-word fallback)
  multi_type   : brands that make many machine types (model alone ≠ type)
  collision    : homonym brand WORDS shared by unrelated companies

A brand that appears in >1 domain list, or carries an "ALSO:" note, is treated as
AMBIGUOUS and dropped from the domain registries (we never want to override on it).
"""
import json, re, sys, collections

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    "/private/tmp/claude-501/-Users-nickbreinfalk/5ef3d149-606a-460e-a524-15854ced88f7/tasks/wivjvevmj.output"

CANON = [  # (keyword in agent's domain string, canonical domain)
    ("metal", "metalworking"), ("fabric", "metalworking"),
    ("wood", "woodworking"),
    ("food", "food_processing"), ("beverage", "food_processing"),
    ("packag", "packaging"),
    ("plastic", "plastics_rubber"), ("rubber", "plastics_rubber"),
    ("semicon", "semiconductor_smt"), ("smt", "semiconductor_smt"), ("electronic", "semiconductor_smt"),
    ("textile", "textile"),
    ("print", "printing_graphic"), ("graphic", "printing_graphic"),
    ("aggregate", "aggregate_construction"), ("construction", "aggregate_construction"), ("mining", "aggregate_construction"),
    ("pharma", "pharma_chemical"), ("chemical", "pharma_chemical"),
]

def canon_domain(s):
    s = (s or "").lower()
    for kw, dom in CANON:
        if kw in s:
            return dom
    return None

def norm(b):
    return re.sub(r"\s+", " ", (b or "").lower().strip())

# generic words that are NOT real brand names — never let them into the domain map
STOP = {"type", "used", "new", "the", "and", "for", "with", "machine", "machines",
        "cnc", "control", "center", "centre", "line", "complete", "automatic",
        "industrial", "heavy", "hydraulic", "horizontal", "vertical", "compact",
        "euro", "advance", "master", "standard", "universal", "system", "unit",
        "laser", "power", "general", "various", "misc"}

raw = json.load(open(SRC, encoding="utf-8"))
res = raw["result"]

# 1. collect brand -> set(domains) and note flags
brand_domains = collections.defaultdict(set)   # brand token -> {domain,...}
flagged_also = set()
for d in res.get("domains", []) or []:
    dom = canon_domain(d.get("domain"))
    if not dom:
        continue
    for b in d.get("brands", []) or []:
        name = norm(b.get("name"))
        if not name or len(name) < 2 or name in STOP:
            continue
        note = (b.get("note") or "")
        if note.strip().upper().startswith("ALSO:"):
            flagged_also.add(name)
        brand_domains[name].add(dom)

# 2. brand_domain: keep only single-domain, non-flagged brands
brand_domain = {}
ambiguous = set()
for name, doms in brand_domains.items():
    if len(doms) == 1 and name not in flagged_also:
        brand_domain[name] = next(iter(doms))
    else:
        ambiguous.add(name)

# 3. first-word -> domain, only where ALL brands sharing that first word agree on ONE domain
first_doms = collections.defaultdict(set)
for name, dom in brand_domain.items():
    first_doms[name.split()[0]].add(dom)
# also let ambiguous brands' first words poison the first-word map
for name in ambiguous:
    fw = name.split()[0]
    for dd in brand_domains[name]:
        first_doms[fw].add(dd)
domain_first = {fw: next(iter(ds)) for fw, ds in first_doms.items() if len(ds) == 1 and len(fw) >= 3}

# 4. multi-type brands -> set of normalized names + first words
multi_type = set()
mt = res.get("multiType") or {}
for b in mt.get("brands", []) or []:
    name = norm(b.get("name"))
    if name and len(name) >= 2:
        multi_type.add(name)
        multi_type.add(name.split()[0])
# manual augment: brands the research missed that clearly make many machine types
# (a bare model doesn't reveal the type) — verified from the golden audit.
for name in ["holzher", "holz-her", "felder", "hammer", "format-4", "format 4",
             "weeke", "ima", "scm", "casadei", "griggio", "scheer", "stroch",
             "weinig", "bystronic", "amada", "prima power", "salvagnini",
             # panel-saw / woodworking-panel brands the classifier confuses with
             # edgebanders (Holzma HPP = beam saw, not an edge bander)
             "holzma", "schelling", "giben", "striebig", "selco", "hebrock"]:
    multi_type.add(name)
    multi_type.add(name.split()[0])

# 5. collision WORDS (single homonym tokens). Generic mis-extracted words are handled
#    separately by core._is_junk_brand, but we fold obvious ones in too.
collision = set()
col = res.get("collision") or {}
for b in col.get("brands", []) or []:
    name = norm(b.get("name"))
    if not name:
        continue
    # collision guard works on a WORD; keep single tokens (the homonym itself)
    for w in name.split():
        if len(w) >= 3:
            collision.add(w)

# a collision WORD must never carry a domain — drop it from both domain maps so a
# bare homonym ("prima") routes to the collision guard, not a misleading domain.
for w in collision:
    brand_domain.pop(w, None)
    domain_first.pop(w, None)

out = {
    "brand_domain": brand_domain,
    "domain_first": domain_first,
    "multi_type": sorted(multi_type),
    "collision": sorted(collision),
    "ambiguous": sorted(ambiguous),
}
json.dump(out, open("data/registry.json", "w", encoding="utf-8"), indent=0, sort_keys=True)

print(f"domains parsed     : {len(res.get('domains', []))}")
print(f"brand_domain (1:1) : {len(brand_domain)}")
print(f"  metalworking     : {sum(1 for v in brand_domain.values() if v=='metalworking')}")
print(f"  woodworking      : {sum(1 for v in brand_domain.values() if v=='woodworking')}")
print(f"  food_processing  : {sum(1 for v in brand_domain.values() if v=='food_processing')}")
print(f"domain_first words : {len(domain_first)}")
print(f"ambiguous (multi-d): {len(ambiguous)}")
print(f"multi_type tokens  : {len(multi_type)}")
print(f"collision words    : {len(collision)}")
print("collision sample   :", sorted(collision)[:30])
print("ambiguous sample   :", sorted(ambiguous)[:20])
# spot-check the known failure brands
for probe in ["karl schnell", "holzher", "holz-her", "prima", "prima power", "mechatronic",
              "biesse", "homag", "altendorf", "trumpf", "haas", "lindemann", "tornos"]:
    fw = probe.split()[0]
    print(f"  probe {probe:14} domain={brand_domain.get(probe) or domain_first.get(fw, '—'):16} "
          f"multi={'Y' if (probe in multi_type or fw in multi_type) else '-'} "
          f"collision={'Y' if fw in collision else '-'}")

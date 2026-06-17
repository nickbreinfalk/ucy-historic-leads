"""Deterministic brand guards (built offline by build_registry.py from a research
swarm). These keep WRONG audiences out of a blast — the core precision job:

  brand_domain(b)        -> industry domain for a brand, or None (unknown/ambiguous)
  is_collision_brand(b)  -> True if the brand is a homonym shared by unrelated firms
  is_multi_type_brand(b) -> True if the brand makes many machine types (model ≠ type)

A brand that resolves to ONE specific company (e.g. 'prima power') is never a
collision even though its first word ('prima') is; a single ambiguous word ('prima',
'mechatronic') is.
"""
import os, json, re

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "registry.json")
try:
    _D = json.load(open(_PATH, encoding="utf-8"))
except FileNotFoundError:
    _D = {"brand_domain": {}, "domain_first": {}, "multi_type": [], "collision": []}

BRAND_DOMAIN = _D.get("brand_domain", {})     # "prima power" -> "metalworking"
DOMAIN_FIRST = _D.get("domain_first", {})     # "trumpf"      -> "metalworking"
MULTI_TYPE   = set(_D.get("multi_type", []))  # {"trumpf","biesse","holzher",...}
COLLISION    = set(_D.get("collision", []))   # {"prima","mechatronic","delta",...}

def _norm(b):
    return re.sub(r"\s+", " ", (b or "").lower().strip())

def brand_domain(brand):
    """Industry domain for a brand, or None if unknown / ambiguous (spans domains)."""
    b = _norm(brand)
    if not b:
        return None
    if b in BRAND_DOMAIN:
        return BRAND_DOMAIN[b]
    return DOMAIN_FIRST.get(b.split()[0])

def is_collision_brand(brand):
    """True if matching this brand string alone would pull a wrong-audience mix
    (the same word is used by unrelated companies). A specific multi-word brand
    that resolves to one company is NOT a collision."""
    b = _norm(brand)
    if not b or b in BRAND_DOMAIN:
        return False
    return b.split()[0] in COLLISION

def is_multi_type_brand(brand):
    """True if the brand makes many distinct machine TYPES, so a bare model number
    does not reveal the type (Trumpf, Amada, Biesse, Holz-Her, Mazak...)."""
    b = _norm(brand)
    if not b:
        return False
    return b in MULTI_TYPE or b.split()[0] in MULTI_TYPE

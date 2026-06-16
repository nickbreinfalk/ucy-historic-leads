"""
Self-learning machine classifier.

Given a listing, produce a search profile {brand, category, synonyms} used to
match historic leads. Decision order, cheapest first:

  1. Recognize from the learned cache (Supabase `learned_patterns`) by matching
     a known discriminating synonym in the title  -> FREE, no Haiku.
  2. Novel type -> call Haiku once to extract {brand, category, synonyms,
     noise_terms}, store it, and it's free forever after.
  3. Haiku unavailable / errors -> fall back to the rule-based extractor.

`used_haiku` and `recognized` are returned so the bot can show a 🧠 / ✓ tag.
"""
import os, re, json, traceback
import psycopg
from dotenv import load_dotenv

from listing import extract_brand, build_terms as rule_build_terms, GENERIC_NOUNS
from normalize import extract_category as rule_extract_category

load_dotenv()

DB = os.environ["SUPABASE_DB_URL"]
HAIKU_SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string", "description": "manufacturer only, e.g. 'OGP', 'Trumpf' ('' if unknown)"},
        "category": {"type": "string", "description": "canonical machine type, lowercase, e.g. 'coordinate measuring machine'"},
        "synonyms": {"type": "array", "items": {"type": "string"},
                     "description": "discriminating terms that identify THIS machine TYPE in other listings"},
        "noise_terms": {"type": "array", "items": {"type": "string"},
                        "description": "tokens in this title that are model codes/years/brand fragments to ignore"},
    },
    "required": ["brand", "category", "synonyms", "noise_terms"],
    "additionalProperties": False,
}
HAIKU_SYSTEM = (
    "You classify used industrial-machine listings so a dealer can find prior buyers of the same TYPE.\n"
    "Return: the manufacturer brand; a canonical lowercase machine category; a list of DISCRIMINATING "
    "synonyms that would appear in other listings of this same machine type (the category name, common "
    "abbreviations, and strong type-specific terms); and noise_terms (tokens in THIS title that are model "
    "numbers, years, dimensions, or brand fragments).\n"
    "Synonyms must be specific to the machine TYPE — include things like 'coordinate measuring', 'cmm', "
    "'thread roller'. NEVER include generic words ('machine', 'used', 'automatic', 'cnc', 'line') or the "
    "model number/year. 4-8 synonyms is ideal."
)

def _conn():
    return psycopg.connect(DB, autocommit=True)

def _load_patterns(conn):
    rows = conn.execute(
        "select category, brand_hint, synonyms, noise_terms from learned_patterns"
    ).fetchall()
    return [{"category": r[0], "brand_hint": r[1], "synonyms": r[2] or [], "noise_terms": r[3] or []}
            for r in rows]

def _recognize(text, patterns):
    """Return (best_pattern, confident). confident=True only when the match is
    specific (a phrase or a >=4-char token) AND unambiguous (one type matched, or
    the best match clearly beats the runner-up). When not confident we prefer to
    spend a Haiku call rather than risk a wrong type — correctness over cost."""
    low = text.lower()
    matches = []  # (pattern, best_synonym_len, is_phrase)
    for p in patterns:
        blen, phrase = 0, False
        for syn in p["synonyms"]:
            s = (syn or "").lower().strip()
            if len(s) < 3:
                continue
            hit = (s in low) if " " in s else re.search(r"\b" + re.escape(s) + r"\b", low)
            if hit and len(s) > blen:
                blen, phrase = len(s), (" " in s)
        if blen:
            matches.append((p, blen, phrase))
    if not matches:
        return None, False
    matches.sort(key=lambda m: -m[1])
    best, blen, phrase = matches[0]
    distinct_cats = len({m[0]["category"] for m in matches})
    dominant = distinct_cats == 1 or blen >= matches[1][1] + 2
    confident = (phrase or blen >= 4) and dominant
    return best, confident

def _haiku_classify(title):
    """One Haiku call for a genuinely new machine type. None on any failure."""
    try:
        from anthropic import Anthropic
        client = Anthropic()  # reads ANTHROPIC_API_KEY
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=HAIKU_SYSTEM,
            messages=[{"role": "user", "content": f"Listing title: {title}"}],
            output_config={"format": {"type": "json_schema", "schema": HAIKU_SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        data["synonyms"] = [s for s in data.get("synonyms", []) if s and len(s) >= 3][:10]
        return data if data["synonyms"] else None
    except Exception:
        traceback.print_exc()
        return None

def _store(conn, prof):
    conn.execute(
        """insert into learned_patterns(category, brand_hint, synonyms, noise_terms, source, hits)
           values (%s, %s, %s, %s, 'haiku', 1)
           on conflict (category) do update
             set synonyms = excluded.synonyms, hits = learned_patterns.hits + 1""",
        (prof["category"], prof.get("brand", ""), prof["synonyms"], prof.get("noise_terms", [])),
    )

def _terms_from_synonyms(brand, synonyms):
    """OR full-text query from discriminating synonyms + bare brand token."""
    parts = []
    for s in synonyms:
        s = s.strip()
        if s and s.lower() not in GENERIC_NOUNS:
            parts.append(f'"{s}"' if " " in s else s)
    if brand:
        parts.append(brand.split()[0])
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower()); out.append(p)
    return " or ".join(out)

def classify(title, slug="", url=""):
    """Return a profile dict: brand, category, terms, used_haiku, recognized."""
    if not slug and url:
        m = re.search(r"/listings/\d+-(.+?)/?$", url)
        slug = m.group(1).replace("-", " ") if m else ""
    brand = extract_brand(title) or extract_brand(slug)
    text = f"{title} {slug}"
    try:
        with _conn() as conn:
            patterns = _load_patterns(conn)
            hit, confident = _recognize(text, patterns)
            if hit and confident:  # strong, unambiguous cache match -> FREE
                return {"brand": brand, "category": hit["category"],
                        "terms": _terms_from_synonyms(brand, hit["synonyms"]),
                        "used_haiku": False, "recognized": f"known type: {hit['category']}"}
            # novel OR low-confidence match -> spend a Haiku call for correct data
            prof = _haiku_classify(title)
            if prof:
                _store(conn, prof)
                hb = prof.get("brand") or brand
                return {"brand": hb, "category": prof["category"],
                        "terms": _terms_from_synonyms(hb, prof["synonyms"]),
                        "used_haiku": True, "recognized": f"learned new type: {prof['category']}"}
            if hit:  # Haiku unavailable but we had a weak cache match -> use it
                return {"brand": brand, "category": hit["category"],
                        "terms": _terms_from_synonyms(brand, hit["synonyms"]),
                        "used_haiku": False, "recognized": f"cache (low-confidence, AI unavailable): {hit['category']}"}
    except Exception:
        traceback.print_exc()
    # fallback: rule-based extraction (Haiku/DB unavailable)
    cat = rule_extract_category(title)
    return {"brand": brand, "category": cat,
            "terms": rule_build_terms(title, slug, brand, cat),
            "used_haiku": False, "recognized": "rules (fallback)"}

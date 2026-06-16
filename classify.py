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
    "You classify a used industrial-machine listing so a dealer can find, in a database of past "
    "machine-enquiry titles, every contact who inquired about the SAME TYPE of machine. Accuracy and "
    "recall of the synonym set directly determine how good the buyer list is — be thorough.\n\n"
    "Return:\n"
    "- brand: the manufacturer ONLY (e.g. 'OGP', 'Trumpf', 'DMG'); '' if unclear. Use the canonical "
    "manufacturer name, not a sub-brand or model line.\n"
    "- category: a canonical lowercase machine TYPE (e.g. 'coordinate measuring machine', 'press brake', "
    "'fiber laser cutter').\n"
    "- synonyms: 5-10 DISCRIMINATING terms a buyer of THIS machine type would have in their own enquiry "
    "titles. Include the category name, its common abbreviations/acronyms (e.g. 'cmm'), alternate "
    "spellings, the manufacturer name and well-known aliases (e.g. 'optical gaging products' for OGP), and "
    "strong type-specific terms (sensing method, sub-type). Think about what genuinely-interested buyers "
    "actually typed. Each synonym must be specific enough that a match almost certainly means the same "
    "machine type — err toward precision, but cover the real variants.\n"
    "- noise_terms: tokens in THIS title that are model numbers, years, dimensions, or fragments to ignore.\n\n"
    "NEVER put generic words in synonyms ('machine', 'used', 'automatic', 'cnc', 'line', 'system', 'new') "
    "or the model number/year/dimensions — those pull in unrelated machines."
)

def _conn():
    return psycopg.connect(DB, autocommit=True)

def _load_patterns(conn):
    rows = conn.execute(
        "select category, brand_hint, synonyms, noise_terms from learned_patterns"
    ).fetchall()
    return [{"category": r[0], "brand_hint": r[1], "synonyms": r[2] or [], "noise_terms": r[3] or []}
            for r in rows]

def _overlap_synonyms(text, patterns):
    """Union of synonyms from EVERY learned pattern that shares a term with `text`.
    Lets a search use all accumulated knowledge for a type even if it's split across
    rows (e.g. a seed 'CMM / measuring' + a Haiku 'coordinate measuring machine')."""
    low = text.lower()
    out = []
    for p in patterns:
        for s in p["synonyms"]:
            s2 = (s or "").lower().strip()
            if len(s2) < 3:
                continue
            hit = (s2 in low) if " " in s2 else re.search(r"\b" + re.escape(s2) + r"\b", low)
            if hit:
                out.extend(p["synonyms"]); break
    return out

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

def _merge(*lists, cap=25):
    """Union of synonym lists, order-preserving + de-duped, capped to keep the
    full-text query sane. This is how knowledge accumulates per machine type."""
    seen, out = set(), []
    for lst in lists:
        for s in (lst or []):
            s = (s or "").strip()
            if s and s.lower() not in seen:
                seen.add(s.lower()); out.append(s)
    return out[:cap]

def _store(conn, category, brand, synonyms, noise):
    conn.execute(
        """insert into learned_patterns(category, brand_hint, synonyms, noise_terms, source, hits)
           values (%s, %s, %s, %s, 'haiku', 1)
           on conflict (category) do update
             set synonyms = excluded.synonyms,
                 brand_hint = coalesce(nullif(excluded.brand_hint, ''), learned_patterns.brand_hint),
                 hits = learned_patterns.hits + 1""",
        (category, brand or "", synonyms, noise or []),
    )

# short tokens that ARE valid machine terms (so we don't strip them as model codes)
INDUSTRIAL_OK = {"cmm", "edm", "vmc", "hmc", "cnc", "smt", "ems", "saw", "mig", "tig", "co2", "plc"}

def _clean_synonyms(synonyms, noise=()):
    """Strip anything that would pollute the search: generic nouns, the listing's
    own noise terms (model #/year/dimensions per Haiku), and bare short model codes
    like 'zip' (a 3-char single token that isn't a known industrial acronym)."""
    noiseset = {(n or "").lower().strip() for n in (noise or [])}
    out = []
    for s in synonyms:
        s = (s or "").strip()
        low = s.lower()
        if not s or low in GENERIC_NOUNS or low in noiseset:
            continue
        if " " not in s and len(low) <= 3 and low not in INDUSTRIAL_OK:
            continue
        out.append(s)
    return out

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
            # Quality-first: always ask Haiku for the best brand + terms for THIS exact
            # machine (~$0.003 a call). Then COMPOUND it into what we already know about
            # this machine type, and search with the accumulated union — the system gets
            # smarter with every machine posted.
            prof = _haiku_classify(title)
            if prof:
                syn_text = " ".join(prof["synonyms"])
                # cluster onto an existing learned type by synonym overlap (so phrasing
                # drift in the category label doesn't fragment the knowledge)
                existing, _ = _recognize(syn_text, patterns)
                category = existing["category"] if existing else prof["category"]
                noise = prof.get("noise_terms", [])
                # accumulate Haiku's terms into the canonical row (the cache learns),
                # sanitized so model fragments never get stored
                stored = _clean_synonyms(_merge(existing["synonyms"] if existing else [], prof["synonyms"]), noise)
                hb = prof.get("brand") or brand
                _store(conn, category, hb, stored, noise)
                # search with EVERYTHING known about this type (all overlapping rows) + fresh Haiku
                query_syn = _clean_synonyms(_merge(_overlap_synonyms(syn_text, patterns), prof["synonyms"]), noise)
                return {"brand": hb, "category": category,
                        "terms": _terms_from_synonyms(hb, query_syn),
                        "used_haiku": True, "recognized": f"AI-classified: {category}"}
            # Haiku unavailable -> use the accumulated knowledge from the cache
            hit, _ = _recognize(text, patterns)
            if hit:
                return {"brand": brand, "category": hit["category"],
                        "terms": _terms_from_synonyms(brand, _clean_synonyms(hit["synonyms"])),
                        "used_haiku": False, "recognized": f"cache (AI unavailable): {hit['category']}"}
    except Exception:
        traceback.print_exc()
    # fallback: rule-based extraction (Haiku/DB unavailable)
    cat = rule_extract_category(title)
    return {"brand": brand, "category": cat,
            "terms": rule_build_terms(title, slug, brand, cat),
            "used_haiku": False, "recognized": "rules (fallback)"}

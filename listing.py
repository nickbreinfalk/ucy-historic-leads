"""
Parse a ucymachines.com listing URL into (brand, category, search terms)
so a posted link can drive the matcher automatically.

Uses the page <title>/<h1>/og:title plus the URL slug, then reuses the same
brand/category heuristics from normalize.py and expands the detected category
into full-text search terms.
"""
import re, sys, requests
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from normalize import extract_brand, extract_category, CATEGORY_RULES

CAT_KEYWORDS = dict(CATEGORY_RULES)  # label -> [keywords]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UCYLeadBot/1.0)"}
# Hosts we trust to follow a redirect to. Machinio/ucymachines very commonly
# 301s a short link (id only, or a stale slug) to the canonical id+slug URL on
# the SAME host — refusing those was dropping the real title and abstaining.
ALLOWED_HOSTS = {"ucymachines.com", "www.ucymachines.com"}

def _get_same_host(url, max_hops=5):
    """GET `url`, manually following redirects ONLY while they stay on an allowed
    host (SSRF-safe: an off-host Location is never fetched). Returns the final
    Response (200 or the last hop), or None on error. Also returns the final URL
    so the caller can recover the canonical slug."""
    seen = set()
    cur = url
    for _ in range(max_hops):
        r = requests.get(cur, headers=HEADERS, timeout=20, allow_redirects=False)
        if r.status_code not in (301, 302, 303, 307, 308):
            return r, cur
        loc = r.headers.get("location") or ""
        if not loc:
            return r, cur
        # resolve relative redirects against the current URL
        nxt = requests.compat.urljoin(cur, loc)
        host = (urlsplit(nxt).hostname or "").lower()
        if host not in ALLOWED_HOSTS or nxt in seen:
            return r, cur            # off-host or loop -> stop, don't chase
        seen.add(nxt); cur = nxt
    return None, cur

def fetch_title(url):
    """Best machine title we can get from the page; fall back to the URL slug.
    Same-host redirects ARE followed (ucymachines canonicalises id-only / stale
    links), so the real og:title is recovered instead of abstaining on a bare slug."""
    title = ""
    final = url
    try:
        r, final = _get_same_host(url)
        soup = BeautifulSoup(r.text if (r and r.status_code == 200) else "", "html.parser")
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        if not title and soup.h1:
            title = soup.h1.get_text(" ", strip=True)
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
    except Exception as e:
        print(f"  (page fetch failed: {e}; using slug)", file=sys.stderr)
    # always fold in the slug words — they're clean and reliable. Prefer the
    # FINAL (canonical) URL's slug: the original may be id-only or a stale stub.
    m = re.search(r"/listings/\d+-(.+?)/?$", final) or re.search(r"/listings/\d+-(.+?)/?$", url)
    slug = m.group(1).replace("-", " ") if m else ""
    return title, slug

# Generic machine nouns carry no discrimination (every CMM title says "machine";
# every press-brake title says "press") -> they pull in the whole adjacent vertical.
GENERIC_NOUNS = {
    "machine", "machines", "press", "boring", "milling", "cutting", "drilling",
    "grinding", "welding", "lathe", "router", "saw", "line", "center", "centre",
    "vertical", "horizontal", "machining", "automatic", "series", "used", "new",
    "for", "sale", "in", "the", "and", "with", "mm", "ton", "system", "unit",
}
# Short tokens that ARE discriminating industrial terms -> keep even before a number.
INDUSTRIAL_ALLOW = {"ogp", "cmm", "edm", "vmc", "hmc", "cnc", "smt", "ems"}

# Machinio appends a broker location: "... in <City>, <State/Country>" (or "... in
# <Country>"). It's irrelevant to us and is noise in the matchable text / CSV display.
# Strip only a TRAILING " in <Capitalized place>" so machine names like "bag in box"
# (lowercase) are never touched.
LOCATION_RE = re.compile(r"\s+in\s+[A-Z][\w.&'’\- ]*(?:,\s*[A-Za-z.\- ]+)?\s*$")

def strip_location(title):
    """Remove the trailing Machinio location tag from a listing title."""
    return LOCATION_RE.sub("", title or "").strip()

def build_terms(title, slug, brand, category):
    """OR-query of: detected-category keywords + bare brand + distinctive slug words.
    Filters generic nouns and bare model designators (e.g. 'zip' in 'ZIP 400')
    that otherwise match unrelated machines (band saws, zip-lock lines)."""
    parts = []
    if category and category in CAT_KEYWORDS:
        for kw in CAT_KEYWORDS[category]:
            parts.append(f'"{kw}"' if " " in kw else kw)
    if brand:
        # full brand as a phrase, NOT the bare first word — a generic first word
        # ("Turbo" of "Turbo Clean") matches unrelated machines. Single-word brands
        # pass through as-is.
        b = brand.strip()
        parts.append(f'"{b}"' if " " in b else b)
    # add meaningful slug words
    toks = [w.strip() for w in re.split(r"\s+", slug) if w.strip()]
    for i, w in enumerate(toks):
        wl = w.lower()
        if len(wl) < 3 or wl.isdigit() or wl in GENERIC_NOUNS:
            continue
        if re.fullmatch(r"(19|20)\d{2}", wl):
            continue
        # model-designator guard: a 3-4 char alpha token immediately before a pure
        # number is a model code ('zip 400', 'max 300'), not a machine type -> skip,
        # unless it's a known industrial term.
        if (3 <= len(wl) <= 4 and wl.isalpha() and wl not in INDUSTRIAL_ALLOW
                and i + 1 < len(toks) and toks[i + 1].isdigit()):
            continue
        parts.append(wl)
    # de-dupe preserving order
    seen, out = set(), []
    for p in parts:
        k = p.lower()
        if k not in seen:
            seen.add(k); out.append(p)
    return " or ".join(out)

def parse_listing(url):
    title, slug = fetch_title(url)
    basis = (title or "") + " " + (slug or "")
    brand = extract_brand(title) if title else extract_brand(slug)
    category = extract_category(basis)
    terms = build_terms(title, slug, brand, category)
    return {"url": url, "title": title or slug, "brand": brand,
            "category": category, "terms": terms}

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.ucymachines.com/listings/9109203-used-2005-ogp-cmm-smartscope-zip-400"
    info = parse_listing(url)
    for k, v in info.items():
        print(f"{k:10}: {v}")

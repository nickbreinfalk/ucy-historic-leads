"""
Parse a ucymachines.com listing URL into (brand, category, search terms)
so a posted link can drive the matcher automatically.

Uses the page <title>/<h1>/og:title plus the URL slug, then reuses the same
brand/category heuristics from normalize.py and expands the detected category
into full-text search terms.
"""
import re, sys, requests
from bs4 import BeautifulSoup
from normalize import extract_brand, extract_category, CATEGORY_RULES

CAT_KEYWORDS = dict(CATEGORY_RULES)  # label -> [keywords]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UCYLeadBot/1.0)"}

def fetch_title(url):
    """Best machine title we can get from the page; fall back to the URL slug."""
    title = ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        if not title and soup.h1:
            title = soup.h1.get_text(" ", strip=True)
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
    except Exception as e:
        print(f"  (page fetch failed: {e}; using slug)", file=sys.stderr)
    # always fold in the slug words — they're clean and reliable
    m = re.search(r"/listings/\d+-(.+?)/?$", url)
    slug = m.group(1).replace("-", " ") if m else ""
    return title, slug

def build_terms(title, slug, brand, category):
    """OR-query of: detected-category keywords + brand + distinctive model/slug words."""
    parts = []
    if category and category in CAT_KEYWORDS:
        for kw in CAT_KEYWORDS[category]:
            parts.append(f'"{kw}"' if " " in kw else kw)
    if brand:
        parts.append(f'"{brand}"' if " " in brand else brand)
    # add meaningful slug words (skip years, pure numbers, filler, units)
    stop = {"used", "new", "for", "sale", "in", "the", "and", "with", "mm", "ton"}
    for w in re.split(r"\s+", slug):
        w = w.strip().lower()
        if len(w) >= 3 and not w.isdigit() and w not in stop and not re.fullmatch(r"(19|20)\d{2}", w):
            parts.append(w)
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

"""Deterministic product matching (spec R7, R8; Phase 1 clarifications).

Order of operations:
1. Normalise (lowercase, strip accents, canonical size/container/variant tokens).
2. Exact alias match on the whole normalised text.
3. Hard filters: variant (must match exactly, so no variant word → base SKUs only),
   container (lata ≠ latão) and size. A product that fails a filter is never a candidate.
4. Score the remaining name words against each product's name keys:
   0.7 × token_set_ratio + 0.3 × token_sort_ratio. The sort term stops a key that is only a
   subset of the text ("antarctica" inside "guarana antarctica") from tying with the full key.
   With no name words but a variant word ("duplo malte lata"), every filtered product scores 100.
"""

import re
import unicodedata

from rapidfuzz import fuzz

from bees_order.domain.models import MatchResult, Product

HIGH = 88.0  # single candidate at or above this → matched
LOW = 75.0  # below this → unknown
MARGIN = 3.0  # candidates within this of the best are tied

_SIZE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(ml|l|lt|lts|litro|litros|liter|liters|litre|litres)\b"
)

# Bare numbers that are pack sizes when they appear in product text ("brahma 600").
_BARE_SIZES = {250, 269, 300, 330, 350, 355, 473, 500, 600}

# Multi-word phrases first, then single-word synonyms → canonical token.
_PHRASES = [
    (r"\bsem acucar\b", " zero "),
    (r"\b(?:com gas|sparkling|gaseificada|gasosa)\b", " comgas "),
    (r"\b(?:sem gas|still)\b", " "),
    (r"\bduplo malte\b", " duplomalte "),
    (r"\bdouble malt\b", " duplomalte "),
    (r"\blong necks?\b", " longneck "),
    (r"\btall cans?\b", " latao "),
]
_SYNONYMS = {
    "lata": "lata", "latas": "lata", "latinha": "lata", "latinhas": "lata",
    "can": "lata", "cans": "lata",
    "latao": "latao", "latoes": "latao",
    "litrao": "litrao", "litroes": "litrao", "litrinho": "litrao",
    "longneck": "longneck", "longnecks": "longneck", "ln": "longneck",
    "garrafa": "garrafa", "garrafas": "garrafa", "bottle": "garrafa", "bottles": "garrafa",
    "pet": "pet",
    "zero": "zero", "diet": "zero",
    "duplomalte": "duplomalte",
    "comgas": "comgas",
}
CONTAINERS = {"lata", "latao", "litrao", "longneck", "garrafa", "pet"}
VARIANTS = {"zero", "duplomalte", "comgas"}  # comgas: no SKU has it, so it filters to unknown
# A text container word is compatible with these product containers.
_CONTAINER_OK = {
    "lata": {"lata"},
    "latao": {"latao"},
    "litrao": {"litrao"},
    "longneck": {"longneck"},
    "garrafa": {"garrafa", "longneck", "litrao"},
    "pet": {"pet"},
}
_STOPWORDS = {
    "cx", "cxs", "caixa", "caixas", "fardo", "fardos", "engradado", "engradados", "pack",
    "packs", "case", "cases", "crate", "crates", "un", "und", "unid", "unidade", "unidades",
    "unit", "units", "de", "da", "do", "das", "dos", "of", "the", "a", "an", "e", "and",
    "mais", "more", "com", "with", "por", "favor", "please", "manda", "send", "me",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("'", "").replace("!", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.,\s]", " ", text)).strip()


def extract(text: str) -> tuple[str, str | None, int | None, str | None]:
    """Return (name words, container, size_ml, variant) from free product text."""
    t = normalize(text)
    size_ml = None
    m = _SIZE_RE.search(t)
    if m:
        value = float(m.group(1).replace(",", "."))
        size_ml = round(value if m.group(2) == "ml" else value * 1000)
        t = t[: m.start()] + " " + t[m.end():]
    for pattern, repl in _PHRASES:
        t = re.sub(pattern, repl, t)
    container = variant = None
    name_words = []
    for word in t.split():
        canon = _SYNONYMS.get(word)
        if canon in CONTAINERS:
            container = canon
        elif canon in VARIANTS:
            variant = canon
        elif word.isdigit() and int(word) in _BARE_SIZES and size_ml is None:
            size_ml = int(word)
        elif word not in _STOPWORDS and not word.replace(".", "").replace(",", "").isdigit():
            name_words.append(word)
    if container == "litrao" and size_ml is None:
        size_ml = 1000
    return " ".join(name_words), container, size_ml, variant


def _name_keys(p: Product) -> list[str]:
    keys = {normalize(p.brand), normalize(p.line)}
    for alias in p.aliases:
        keys.add(extract(alias)[0])
    return [k for k in keys if k]


def _score(name: str, p: Product) -> float:
    return max(0.7 * fuzz.token_set_ratio(name, k) + 0.3 * fuzz.token_sort_ratio(name, k)
               for k in _name_keys(p))


def _passes_filters(p: Product, container: str | None, size_ml: int | None,
                    variant: str | None) -> bool:
    if p.variant != variant:
        return False
    if container and p.container not in _CONTAINER_OK[container]:
        return False
    return not (size_ml and p.size_ml != size_ml)


def match(text: str, products: dict[str, Product] | list[Product]) -> MatchResult:
    plist = list(products.values()) if isinstance(products, dict) else list(products)
    norm = normalize(text)

    # Exact alias on the whole text ("brahminha"), only when one base SKU owns that alias.
    owners = [p for p in plist
              if norm and p.variant is None and norm in {normalize(a) for a in p.aliases}]
    if len(owners) == 1:
        return MatchResult(status="matched", sku=owners[0].sku, candidates=[owners[0].sku])

    name, container, size_ml, variant = extract(text)
    if not name and not variant:
        return MatchResult(status="unknown")

    scored = []
    for p in plist:
        if not _passes_filters(p, container, size_ml, variant):
            continue
        scored.append((_score(name, p) if name else 100.0, p.sku))
    if not scored:
        return MatchResult(status="unknown")

    best = max(s for s, _ in scored)
    if best < LOW:
        return MatchResult(status="unknown")
    tied = [sku for s, sku in sorted(scored, reverse=True) if s >= best - MARGIN]
    if len(tied) == 1 and best >= HIGH:
        return MatchResult(status="matched", sku=tied[0], candidates=tied)
    return MatchResult(status="ambiguous", candidates=tied)


def resolve_among(answer: str, candidates: list[str],
                  products: dict[str, Product]) -> MatchResult:
    """Match a clarification answer (e.g. "2L", "the can") only against the offered SKUs."""
    _, container, size_ml, variant = extract(answer)
    subset = [products[s] for s in candidates]
    hits = [p.sku for p in subset
            if (container is None or p.container in _CONTAINER_OK[container])
            and (size_ml is None or p.size_ml == size_ml)
            and (variant is None or p.variant == variant)]
    if (container or size_ml or variant) and len(hits) == 1:
        return MatchResult(status="matched", sku=hits[0], candidates=hits)
    result = match(answer, subset)
    if result.status == "matched":
        return result
    return MatchResult(status="ambiguous", candidates=hits or candidates)


_SPLIT_RE = re.compile(r"\s*(?:,|;|\n|\be\b|\band\b)\s*")


def split_answers(answer: str, n: int) -> list[str]:
    """One message may answer several questions in order ("2L and lata"). If the parts don't
    line up, the whole answer applies to every question, except a bare number: it can't say
    which question it answers, so every question is asked again."""
    parts = [p for p in _SPLIT_RE.split(answer) if p]
    if len(parts) == n:
        return parts
    return ["" if n > 1 and answer.isdigit() else answer] * n


def resolve_answer(question: dict, answer: str, attempts: int,
                   products: dict[str, Product], max_attempts: int = 2) -> dict:
    """R8: resolve one open question from the customer's answer, an option number or words.
    Returns the fields to update on that edit; {} means "still unclear, ask again"."""
    cands = question["candidates"]
    if answer == "cancel":
        return {"status": "dropped"}
    if answer.isdigit() and 1 <= int(answer) <= len(cands):
        return {"status": "matched", "sku": cands[int(answer) - 1], "candidates": []}
    m = resolve_among(answer, cands, products)
    if m.status == "matched":
        return {"status": "matched", "sku": m.sku, "candidates": []}
    if attempts >= max_attempts:  # give up: an add becomes a "couldn't tell which" line
        return {"status": "not_added" if question["action"] == "add" else "unclear"}
    return {}

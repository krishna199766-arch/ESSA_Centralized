"""
Map a free-text invoice description onto the Product Category master.

Suppliers write whatever they like on a bill — "Women's T-shirt", "LADIES TSHIRT",
"GENTS SHIRT FS", "baba suit 3pc" — while the master (from GRN PRODUCT DETAILS.xlsx)
uses a fixed vocabulary of ~690 names like LADIES-T-SHIRT / MENS-SHIRT / KIDS-BABASUIT.
This module bridges the two deterministically: no model call, no network, and the
same input always gives the same answer, so a mis-mapping can be reasoned about
and fixed by editing the rules below.

Two steps:

 1. Section. Gender/age words in the description are canonicalised to the master's
    own vocabulary ("women's", "womens", "female", "ladies" -> LADIES). This is
    what makes "Women's T-shirt" reach LADIES-T-SHIRT instead of the bare
    OVERALL "T-SHIRT" entry.

 2. Garment. The canonicalised text is scored against every candidate name. A
    category carrying a DIFFERENT gender than the one detected is excluded
    outright — a women's tee must never land in MENS-T-SHIRT, no matter how well
    the rest of the string scores.

The caller decides what to do with a weak result: `confident` is only True when
the score clears AUTO_THRESHOLD and beats the runner-up by MIN_MARGIN, so vague
descriptions ("ASSORTED", "GARMENTS") stay unmapped and get reviewed instead of
being silently filed in the wrong place.
"""
import re
from rapidfuzz import fuzz
from .. import models

# a category is only applied automatically at or above this score...
AUTO_THRESHOLD = 86
# ...and only when it beats the next-best candidate by this much, so a genuine
# toss-up between two categories is escalated rather than decided by a coin flip
MIN_MARGIN = 3

# description wording -> the master's own section vocabulary. Longer phrases are
# matched first so "baby girl" doesn't get read as "girl".
SECTION_WORDS = [
    ("KIDS", ["BABY BOY", "BABY GIRL", "BABY", "BABA", "KIDS", "KID", "CHILDREN",
              "CHILD", "BOYS", "BOY", "GIRLS", "GIRL", "INFANT", "TODDLER",
              "JUNIOR", "NEW BORN", "NEWBORN"]),
    ("LADIES", ["LADIES", "LADIE", "LADY", "WOMENS", "WOMEN", "WOMAN", "WOMENS",
                "FEMALE", "GIRLS LADIES", "MAHILA"]),
    ("MENS", ["MENS", "MEN", "MAN", "GENTS", "GENT", "GENTLEMEN", "MALE",
              "BOYS MENS", "PURUSH"]),
]
# tokens that mean a gender is being asserted — used to exclude cross-gender hits
GENDER_TOKENS = {"KIDS": "KIDS", "LADIES": "LADIES", "MENS": "MENS",
                 "LW": "LADIES", "MW": "MENS", "KW": "KIDS"}

# noise that carries no category signal and only dilutes the score
STOPWORDS = {"OF", "THE", "AND", "WITH", "FOR", "PCS", "PC", "NOS", "NO", "SET",
             "ASSTD", "ASST", "QTY", "PRINTED", "PLAIN", "NEW", "FANCY", "SUPER",
             "DELUXE", "BRANDED", "QUALITY", "MIX", "MIXED"}

_PUNCT = re.compile(r"[^A-Z0-9]+")
_SIZE_RUN = re.compile(r"\b(?:XS|S|M|L|XL|XXL|XXXL|\d+X\d+|\d+)\b")

# Glued and variant spellings the fuzzy scorer cannot bridge on its own, because
# they change the token COUNT: "TSHIRT" is one token where the master has two
# ("T-SHIRT" -> T, SHIRT), which drags the real match below "LADIES-SHIRT".
# Applied to descriptions AND category names so both sides speak one vocabulary;
# each rule is idempotent, so running it over an already-canonical name is a no-op.
# Every target below is a spelling that actually appears in categories.json —
# rewriting to a word the master does not use would only match by luck.
SYNONYMS = [
    (re.compile(r"\bT ?SHIRTS?\b"), "T SHIRT"),           # TSHIRT / T SHIRTS -> T-SHIRT
    (re.compile(r"\bTEE ?SHIRTS?\b"), "T SHIRT"),
    (re.compile(r"\bBAB(?:Y|A) ?SUITS?\b"), "BABASUIT"),
    (re.compile(r"\bNIGHT(?:IE|Y|IES)\b"), "NIGHTY"),
    (re.compile(r"\bCH[UR]+I?[DT]H?AR\b"), "CHUDITHAR"),  # churidar/chudidhar/...
    (re.compile(r"\bLEGGIN(?:G|GS|S)\b"), "LEGGINGS"),
    (re.compile(r"\bSAREES\b"), "SAREE"),
    (re.compile(r"\bFULL SLEEVES?\b|\bF ?S\b"), ""),      # sleeve length isn't a category
    (re.compile(r"\bHALF SLEEVES?\b|\bH ?S\b"), ""),
]


def normalise(text, apply_synonyms=True):
    """Uppercase, strip punctuation/possessives, apply the spelling rules above,
    collapse whitespace.

    `apply_synonyms=False` is needed for section detection: some rules glue a
    gender word into a garment term ("BABY SUIT" -> "BABASUIT"), which would erase
    the very signal the section is read from."""
    t = (text or "").upper().replace("'S", " ").replace("’S", " ")
    t = _PUNCT.sub(" ", t)
    t = " ".join(t.split())
    if apply_synonyms:
        for pat, repl in SYNONYMS:
            t = pat.sub(repl, t)
    return " ".join(t.split())


def _tokens(text, drop_sizes=False, apply_synonyms=True):
    t = normalise(text, apply_synonyms=apply_synonyms)
    if drop_sizes:
        t = _SIZE_RUN.sub(" ", t)
    return [w for w in t.split() if w not in STOPWORDS and len(w) > 0]


def detect_section(description):
    """The master section a description is talking about, or None if it doesn't say.

    KIDS is checked first: "baby girl top" is a kids item, not a ladies one."""
    words = _tokens(description, apply_synonyms=False)
    joined = " ".join(words)
    for section, needles in SECTION_WORDS:
        for n in needles:
            if re.search(rf"\b{re.escape(n)}\b", joined):
                return section
    return None


def _canonical_text(description, section):
    """Description with its gender wording replaced by the master's own word, so
    the two strings can be compared token for token."""
    words = _tokens(description, drop_sizes=True)
    if not section:
        return " ".join(words)
    aliases = {n for sec, needles in SECTION_WORDS if sec == section for n in needles}
    single = {a for a in aliases if " " not in a}
    out = [w for w in words if w not in single]
    return " ".join([section] + out)


def _category_gender(name_norm):
    """The gender a category name asserts, or None for a neutral one."""
    for tok in name_norm.split():
        if tok in GENDER_TOKENS:
            return GENDER_TOKENS[tok]
    return None


SECTION_BONUS = 6      # a sectioned sheet entry beats the OVERALL catch-all


def _score(search_text, name_norm, section, cat_section):
    """Agreement between a description and one category name, as
    (rank_score, name_score).

    `name_score` is how well the NAMES agree, 0..100 — that is what a human should
    be shown. `rank_score` adds the section bonus and is what ordering uses; it is
    deliberately left unclamped, because clamping would flatten every strong
    candidate to exactly 100 and the runner-up margin could never separate them.

    token_set_ratio alone is unusable here: it returns 100 whenever one string's
    tokens are a subset of the other's, so "LADIES T SHIRT" scores a perfect 100
    against the bare "T-SHIRT" and ties with the correct LADIES-T-SHIRT. Blending
    in token_sort_ratio, which penalises the missing word, separates them."""
    name_score = 0.55 * fuzz.token_set_ratio(search_text, name_norm) \
        + 0.45 * fuzz.token_sort_ratio(search_text, name_norm)
    rank = name_score + (SECTION_BONUS if section and cat_section == section else 0)
    return rank, name_score


def suggest(db, description, limit=5, section=None):
    """Rank the category master against a description.

    Returns {section, query, best, confident, candidates:[{name, section, score}]}.
    `best` is None when nothing scores usefully. Pass `section` to override the
    detected one (e.g. the operator picked LADIES by hand)."""
    detected = section or detect_section(description)
    search = _canonical_text(description, detected)
    if not search:
        return {"section": detected, "query": search, "best": None,
                "confident": False, "candidates": []}

    scored = []
    for c in db.query(models.Category).all():
        name_norm = normalise(c.name)
        gender = _category_gender(name_norm)
        # a category that asserts a different gender is wrong however well it scores
        if detected and gender and gender != detected:
            continue
        rank, name_score = _score(search, name_norm, detected, c.section)
        scored.append((rank, name_score, c))

    if not scored:
        return {"section": detected, "query": search, "best": None,
                "confident": False, "candidates": []}

    # best score per distinct name (OVERALL duplicates most sectioned entries, so
    # the same name can appear twice — keep the sectioned, higher-ranking one)
    by_name = {}
    for rank, name_score, c in scored:
        cur = by_name.get(c.name)
        if cur is None or rank > cur[0]:
            by_name[c.name] = (rank, name_score, c)
    ranked = sorted(by_name.values(), key=lambda t: (-t[0], t[2].name))

    cands = [{"name": c.name, "section": c.section,
              "score": round(min(ns, 100.0), 1),
              "section_match": bool(detected and c.section == detected)}
             for _, ns, c in ranked[:limit]]
    top_rank = ranked[0][0]
    runner_rank = ranked[1][0] if len(ranked) > 1 else 0
    confident = top_rank >= AUTO_THRESHOLD and (top_rank - runner_rank) >= MIN_MARGIN
    return {"section": detected, "query": search, "best": cands[0],
            "confident": confident, "candidates": cands}


def categorise_product(db, product, force=False):
    """Set product.category from its description when the match is confident.

    Returns the suggestion dict (with an added `applied` flag). Never overwrites a
    category someone already set unless `force`."""
    res = suggest(db, product.description)
    res["applied"] = False
    if res["confident"] and (force or not product.category):
        product.category = res["best"]["name"]
        product.category_section = res["best"]["section"]
        res["applied"] = True
    return res

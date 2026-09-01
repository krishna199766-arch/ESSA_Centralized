"""
Map a free-text invoice description onto the Product Category master.

Suppliers write whatever they like on a bill — "Women's T-shirt", "LADIES TSHIRT",
"GENTS SHIRT FS", "baba suit 3pc" — while the master (from GRN PRODUCT DETAILS.xlsx)
uses a fixed vocabulary of ~690 names like LADIES-T-SHIRT / MENS-SHIRT / KIDS-BABASUIT.
Mapping the two is what keeps ONE product master across every supplier: the same
garment described four ways becomes one record, one QR and one stock figure,
instead of four near-duplicates nobody can report on.

This module bridges them deterministically: no model call, no network, and the
same input always gives the same answer, so a mis-mapping can be reasoned about
and fixed by editing the rules below.

Three steps:

 0. What we have been told. A wording a human has already mapped (CategoryAlias)
    wins outright — see learn_alias. Suppliers keep inventing descriptions and no
    hand-written rule list ever finishes, so the engine learns from each correction
    instead of waiting on a developer.

 1. Section. Gender/age words in the description are canonicalised to the master's
    own vocabulary ("women's", "womens", "female", "ladies" -> LADIES). This is
    what makes "Women's T-shirt" reach LADIES-T-SHIRT instead of the bare
    OVERALL "T-SHIRT" entry.

 2. Garment. The canonicalised text is scored against every candidate name. A
    category carrying a DIFFERENT gender than the one detected is excluded
    outright — a women's tee must never land in MENS-T-SHIRT, no matter how well
    the rest of the string scores.

The caller decides what to do with a weak result: `confident` is only True when the
score clears AUTO_THRESHOLD, beats the runner-up by MIN_MARGIN, and shares a whole
WORD with the category name — so vague descriptions ("ASSORTED", "GARMENTS") and
near-spellings of the wrong thing ("GENTS TEE" against MENS-TIE) stay unmapped and
get reviewed instead of being silently filed in the wrong place.
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

# Words that say which section AND survive into the search text, because the master
# uses them to tell two categories apart: KIDS-BOYS PANT and KIDS-GIRLS PANT are
# both KIDS and both PANT. Stripping them as ordinary section synonyms left one
# search text ("KIDS PANT") for three different categories, and whichever scored
# highest took every one of them.
#
# BABY is deliberately NOT here, though the master has KIDS-BABY TOWEL. Keeping it
# does fix that one name, but the master also has the catch-all KIDS-BABY ITEMS,
# and a preserved BABY drags every unrecognised "BABY <something>" into it — a
# measured "BABY DRESS" went from honest review to auto-filing as BABY ITEMS. A
# bucket category that shares the qualifier attracts everything, and silently
# wrong beats loudly unsure. BOYS/GIRLS have no such twin.
QUALIFIERS = {"BOYS", "BOY", "GIRLS", "GIRL"}

# noise that carries no category signal and only dilutes the score.
# "SET" is deliberately NOT here: 17 master names end in it and six of those are
# distinguished from a twin by that word alone (DHOTI SET vs DHOTI, KIDS-MIDI SET
# vs KIDS-MIDI). Dropping it made every one of those categories unreachable — a
# "DHOTI SET" line filed silently as DHOTI. A *counted* set ("2 SET") is a pack
# quantity and is stripped by the SYNONYMS rule below instead.
STOPWORDS = {"OF", "THE", "AND", "WITH", "FOR", "PCS", "PC", "NOS", "NO",
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
    # ...and the bare word, which has to come AFTER the rule above so "TEE SHIRT"
    # is already "T SHIRT" and doesn't become "T SHIRT SHIRT". Without this,
    # "Ladies Tee" has no garment token the master shares and fuzzy matching
    # drifts to whatever is closest by characters — LADIES-SAREE, and worse,
    # "Gents Tee" scored MENS-TIE high enough to auto-apply. No category in the
    # master contains "TEE", so rewriting it is safe on both sides.
    (re.compile(r"\bTEES?\b"), "T SHIRT"),
    (re.compile(r"\bBAB(?:Y|A) ?SUITS?\b"), "BABASUIT"),
    (re.compile(r"\bNIGHT(?:IE|Y|IES)\b"), "NIGHTY"),
    # churidar / chudidar / chudidhar / churidhar -> the master's CHUDITHAR. The
    # earlier pattern could not match "CHUDIDHAR", the spelling it named: after
    # CH[UR]+ had taken the U it needed an optional I before the D, and the real
    # word puts the I after it.
    (re.compile(r"\bCH[UI][RD]I[DT]H?ARS?\b"), "CHUDITHAR"),
    # a counted pack is quantity, not identity: "BABA SUIT 3PC" and "BABA SUIT"
    # are the same category. Stripping it also means one alias covers "3PC",
    # "3 PC" and "3 PCS" instead of three. Note the count is REQUIRED: a bare
    # "SET" is part of the name (DHOTI SET), while "2 SET" is two of them.
    (re.compile(r"\b\d+\s?(?:PC|PCS|PIECE|PIECES|SET|SETS)\b"), " "),
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
    single = {a for a in aliases if " " not in a} - QUALIFIERS
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


# two words count as the same when they are this close — a plural or a one-letter
# misspelling, not a different garment. SHIRT/SKIRT score 80 and TEE/TIE 67, so
# both stay apart; CHUDIDHAR/CHUDITHAR (89) and PANT/PANTS (89) come together.
TOKEN_NEAR = 85
TOKEN_MIN_LEN = 4          # short words are where near-misses turn into wrong stock


def _shares_a_word(search_text, name_norm, section):
    """True when the description and the category name have a word in common —
    the same word, or one spelt within a letter of it — ignoring the section word
    they may both carry.

    Character similarity across the WHOLE string must never be enough to file
    stock automatically: "GENTS TEE" and "MENS TIE" differ by one letter and
    scored 87.5, which was confident enough to post t-shirts into MENS-TIE.
    Requiring a shared word means a wording the rules don't cover yet goes to a
    human instead of somewhere plausible-looking — and the human's answer is then
    remembered (see learn_alias), so nobody is asked about it twice."""
    words = {w for w in search_text.split() if w != section}
    names = {w for w in name_norm.split() if w != section}
    if words & names:
        return True
    return any(fuzz.ratio(w, n) >= TOKEN_NEAR
               for w in words if len(w) >= TOKEN_MIN_LEN
               for n in names if len(n) >= TOKEN_MIN_LEN)


# a strong enough name match stands on its own — this is the "LADIES-NIGHTY" vs
# "LADIES NIGHTIE" case, already bridged by SYNONYMS, kept as a safety valve
STRONG_SCORE = 97


def _alias_key(db, canonical, catalogue_id=None):
    """The stored key for one wording in one catalogue.

    The default line keeps the BARE canonical text — which is what every alias
    written before catalogues existed already has, so none of them has to be
    rewritten and none of them stops matching. Any other line prefixes its code,
    which is what keeps "plain cotton" meaning one thing in garments and another
    in silks despite `key` being a single unique column. See models.CategoryAlias.
    """
    from . import catalogues as cat_svc
    if not canonical:
        return None
    if not catalogue_id:
        return canonical
    default = cat_svc.default_catalogue(db)
    if default and default.id == catalogue_id:
        return canonical
    c = db.get(models.Catalogue, catalogue_id)
    return f"{c.code}:{canonical}" if c else canonical


def _alias_for(db, key):
    if not key:
        return None
    return db.query(models.CategoryAlias).filter(models.CategoryAlias.key == key).first()


def _catalogue_categories(db, catalogue_id):
    """The category rows one business line may classify into.

    A blank catalogue means every row, which is what every caller that predates
    catalogues passes and what a single-line install means by the question."""
    q = db.query(models.Category)
    if catalogue_id:
        q = q.filter((models.Category.catalogue_id == catalogue_id)
                     | (models.Category.catalogue_id.is_(None)))
    return q.all()


def learn_alias(db, description, category_name, section=None, source="human",
                catalogue_id=None):
    """Remember that this wording means this category, because a human said so.

    Called when someone sets the category on a GRN line by hand — the one moment
    the system is being told, unambiguously, what a supplier's words mean. Re-teaching
    the same wording overwrites it, so a mapping that turns out wrong is corrected
    the same way it was created: set the right category on any line that reads that
    way. Passing an empty category forgets it.

    Learned per catalogue: what a silk supplier's wording means says nothing about
    what the same words mean on a garment invoice."""
    canonical = _canonical_text(description, detect_section(description))
    key = _alias_key(db, canonical, catalogue_id)
    if not key:
        return None
    row = _alias_for(db, key)
    if not category_name:
        if row:
            db.delete(row)
            db.flush()
        return None
    names = [c for c in _catalogue_categories(db, catalogue_id)
             if c.name == category_name]
    if not names:
        # never learn a name this catalogue's master doesn't have — an alias
        # pointing at another line's category would classify goods into a list
        # their own screens do not even show
        return None
    cat = names[0]
    sec = section or next((c.section for c in names
                           if c.section and c.section != "OVERALL"), cat.section)
    if row:
        row.category, row.section, row.source = category_name, sec, source
        row.sample = description
        row.catalogue_id = catalogue_id
    else:
        row = models.CategoryAlias(key=key, sample=description, category=category_name,
                                   section=sec, source=source, hits=0,
                                   catalogue_id=catalogue_id)
        db.add(row)
    db.flush()
    return row


def suggest(db, description, limit=5, section=None, catalogue_id=None):
    """Rank the category master against a description.

    Returns {section, query, best, confident, via, candidates:[{name, section, score}]}.
    `best` is None when nothing scores usefully. Pass `section` to override the
    detected one (e.g. the operator picked LADIES by hand). `via` is "alias" when a
    human has already told the system what this wording means, else "rules"."""
    detected = section or detect_section(description)
    search = _canonical_text(description, detected)
    if not search:
        return {"section": detected, "query": search, "best": None,
                "confident": False, "via": "rules", "candidates": []}

    # What a human has already said beats what the rules can infer — that is the
    # whole point of having been told.
    alias = (_alias_for(db, _alias_key(db, search, catalogue_id))
             if section is None else None)
    if alias:
        best = {"name": alias.category, "section": alias.section, "score": 100.0,
                "section_match": bool(detected and alias.section == detected)}
        return {"section": detected, "query": search, "best": best, "confident": True,
                "via": "alias", "learned_from": alias.sample, "candidates": [best]}

    scored = []
    # Only this business line's categories. A silk warehouse whose catalogue is
    # still empty gets NO suggestion, which is the truthful answer — the
    # alternative is filing a Kanchipuram saree as ESSA KIDS-TRUNK because that
    # is the only master the scorer had to choose from.
    for c in _catalogue_categories(db, catalogue_id):
        name_norm = normalise(c.name)
        gender = _category_gender(name_norm)
        # a category that asserts a different gender is wrong however well it scores
        if detected and gender and gender != detected:
            continue
        rank, name_score = _score(search, name_norm, detected, c.section)
        scored.append((rank, name_score, c))

    if not scored:
        return {"section": detected, "query": search, "best": None,
                "confident": False, "via": "rules", "candidates": []}

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
    top_rank, top_name_score, top_cat = ranked[0]
    runner_rank = ranked[1][0] if len(ranked) > 1 else 0
    confident = (top_rank >= AUTO_THRESHOLD
                 and (top_rank - runner_rank) >= MIN_MARGIN
                 and (_shares_a_word(search, normalise(top_cat.name), detected)
                      or top_name_score >= STRONG_SCORE))
    return {"section": detected, "query": search, "best": cands[0],
            "confident": confident, "via": "rules", "candidates": cands}


def categorise_product(db, product, force=False):
    """Set product.category from its description when the match is confident.

    Returns the suggestion dict (with an added `applied` flag). Never overwrites a
    category someone already set unless `force`.

    Classified within the item's OWN business line — the categories its warehouse
    trades in — so nothing can be filed into a master its own screens do not show.
    """
    cat_id = getattr(product, "catalogue_id", None)
    res = suggest(db, product.description, catalogue_id=cat_id)
    res["applied"] = False
    if res["confident"] and (force or not product.category):
        product.category = res["best"]["name"]
        product.category_section = res["best"]["section"]
        res["applied"] = True
        if res.get("via") == "alias":
            # count the use, not the render: suggest() runs every time a GRN is
            # opened, so only an actual mapping is evidence the alias earns its keep
            row = _alias_for(db, _alias_key(db, res["query"], cat_id))
            if row:
                row.hits = (row.hits or 0) + 1
    return res

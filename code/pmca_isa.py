"""pmca_isa — the is_a mechanism (Jonathan's design, 2026-10-25;
D1/D2/D3 ruled 2026-10-28 as ISA-1 / ISA-2 / ISA-3).

"We branch it": if a word feeds into a genus, the GENUS is defined
as its own entry; words gathered under it take their categorization
from it, and that categorization IS their is_a. Membership is always
word -> genus, never word -> word. A genus entry answers to all its
attested forms under ONE form-neutral identifier (ISA-1). A referral
is CANDIDATE on filing and promotes to VERIFIED only at >= 2
DISTINCT provenance lineages (ISA-2, the standing threshold,
unchanged). Runtime enforcement (2026-10-29, PROM-1): what_is /
recognize count distinct provenance lineages in the referral's
stored "provenances" before honouring a VERIFIED status; the
offline builder's counting is no longer the only gate. What-is answers read VERIFIED referrals only and
COMPOSE the genus entry's definition with the word's own
differentia at answer time (ISA-3): the composed answer is stored
nowhere; each part remains its own stored fact. The categorization
doubles as a secondary recognition step: a word is recognized once
on its own entry and again through the genus it sits under —
VERIFIED referrals only; a candidate confers nothing.

This module reads the genus store (a JSON file; in deployment the
same records live in the stored-once space). It writes nothing.
Unknown word -> HOLD with the typed gap "kind: not stored";
candidate-only -> HOLD with gap "genus: candidate only".
No membership is ever computed at answer time: what is stored is
what is answered, or the answer is a HOLD.
"""

import json
import os

import pmca_lineage_table

_DEFAULT_STORE = os.path.expanduser(
    "~/workspace/goals/pmca-simulation/hidden_files/"
    "isa-build-2026-10-28/genus_store.json")

_store_path = None
_store_cache = None


def configure_genus_store(path):
    """Point the module at a genus store file (mirrors the
    analyzer's configure_dictionary pattern)."""
    global _store_path, _store_cache
    _store_path = path
    _store_cache = None


def _path():
    if _store_path:
        return _store_path
    return os.environ.get("PMCA_GENUS_STORE", _DEFAULT_STORE)


def load_store(path=None):
    """Load the genus store. A missing file is an empty store
    (no genera, no referrals) — every query HOLDs, by design."""
    global _store_cache
    p = path or _path()
    if path is None and _store_cache is not None:
        return _store_cache
    try:
        with open(p, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {"schema": "pmca_genus_store/v1",
                 "identifiers": {}, "genera": {}}
    if path is None:
        _store_cache = store
    return store


def empty_store():
    return {"schema": "pmca_genus_store/v1",
            "identifiers": {}, "genera": {}}


def _cf(s):
    return str(s).casefold()


def find_genus(form, store=None):
    """Resolve any attested form of a genus to its ONE genus
    entry (ISA-1). Returns the genus record or None."""
    store = store if store is not None else load_store()
    for gid, genus in (store.get("genera") or {}).items():
        for f in genus.get("forms") or []:
            if _cf(f) == _cf(form):
                return genus
    return None


def genus_id_for(form, store=None):
    g = find_genus(form, store=store)
    return g["id"] if g else None


def referral_for(word, store=None):
    """The stored referral for a word, or None. Reads every genus
    entry's members; a word has an is_a only where a referral is
    stored — nothing is inferred."""
    store = store if store is not None else load_store()
    for gid, genus in (store.get("genera") or {}).items():
        for key, ref in (genus.get("members") or {}).items():
            if ref.get("member_kind") == "word" \
                    and _cf(ref.get("word", "")) == _cf(word):
                return gid, ref
    return None, None


def _differentia(word):
    """The word's own differentia: its first stored dictionary
    definition, read at answer time from the dictionary tier
    (its own stored fact, quoted — never copied into the genus
    entry). Returns (text, provenance) or (None, None)."""
    try:
        import pmca_dictionary
        looked = pmca_dictionary.lookup(word)
    except Exception:
        looked = None
    if looked and looked.get("definitions"):
        return looked["definitions"][0], {
            "source": "Webster 1913", "lineage": "Webster",
            "headword": looked.get("headword"),
            "license": "Public domain"}
    return None, None


def _distinct_lineages(ref):
    """The referral's distinct provenance lineage CLASSES,
    read through the governed source->lineage table
    (Candidacy Rule Set C-3; pmca_lineage_table): spelling
    variants of one lineage are ONE class (Ledger D-2,
    fixed 2026-11-01), and a provenance record with a
    missing/empty lineage (and no usable source) counts
    as NO class, never as a class (Ledger D-3, fixed
    2026-11-01; pre-fix, a missing lineage counted as the
    None class, so [{}, {lineage: Webster}] honoured a
    VERIFIED referral on one real lineage)."""
    return pmca_lineage_table.provenance_classes(
        ref.get("provenances") or [],
        context="pmca_isa._distinct_lineages")


def _honoured_verified(ref):
    return ref.get("status") == "VERIFIED" \
        and len(_distinct_lineages(ref)) >= 2


def what_is(word, store=None):
    """What-is(X): follow X's VERIFIED is_a referral to its genus
    entry and compose the answer (ISA-3): the genus entry's stored
    definition, with X's own differentia alongside, never merged.
    CANDIDATE referral -> HOLD naming the candidate. No referral
    -> HOLD, gap "kind: not stored"."""
    store = store if store is not None else load_store()
    gid, ref = referral_for(word, store=store)
    if ref is None:
        return {"kind": "what_is", "verdict": "HOLD",
                "status": "not_stored", "word": word,
                "genus": None, "differentia": None,
                "gaps": ["kind: not stored"]}
    genus = store["genera"][gid]
    if ref.get("status") != "VERIFIED":
        return {"kind": "what_is", "verdict": "HOLD",
                "status": "candidate", "word": word,
                "genus": {"id": gid, "forms": genus.get("forms")},
                "differentia": None,
                "gaps": ["genus: candidate only"]}
    if not _honoured_verified(ref):
        return {"kind": "what_is", "verdict": "HOLD",
                "status": "candidate", "word": word,
                "genus": {"id": gid, "forms": genus.get("forms")},
                "differentia": None,
                "gaps": ["genus: verified status without two "
                         "distinct lineages (found %d)"
                         % len(_distinct_lineages(ref))]}
    diff_text, diff_prov = _differentia(word)
    gaps = []
    if diff_text is None:
        gaps.append("differentia: not stored")
    return {
        "kind": "what_is", "verdict": "ANSWER",
        "status": "verified", "word": word,
        "genus": {"id": gid,
                  "forms": genus.get("forms"),
                  "definition": (genus.get("definition") or {})
                  .get("text"),
                  "definition_provenance": (genus.get("definition")
                                            or {}).get("provenance")},
        "differentia": ({"text": diff_text,
                         "provenance": diff_prov}
                        if diff_text is not None else None),
        "composition": "ISA-3: genus definition and the word's own "
                       "differentia, composed at answer time; "
                       "stored nowhere",
        "gaps": gaps,
    }


def recognize(word, store=None):
    """Secondary recognition (Jonathan): a word is recognized on
    its own entry, and again through the genus it sits under.
    Only a VERIFIED referral confers genus recognition."""
    store = store if store is not None else load_store()
    gid, ref = referral_for(word, store=store)
    own = False
    try:
        import pmca_dictionary
        own = pmca_dictionary.lookup(word) is not None
    except Exception:
        own = False
    via = gid if (ref is not None
                  and _honoured_verified(ref)) else None
    return {"word": word, "own_entry": own,
            "via_genus": via,
            "candidate_genus": (gid if ref is not None
                                and via is None else None),
            "recognized": bool(own or via)}


def who_is_under(form_or_id, store=None):
    """Who-is-under(G): every stored referral to the genus —
    a stored lookup over the genus entry's members, never a
    scan-and-infer. Accepts a genus id or any attested form."""
    store = store if store is not None else load_store()
    genera = store.get("genera") or {}
    gid = form_or_id if form_or_id in genera \
        else genus_id_for(form_or_id, store=store)
    if gid is None:
        return {"genus": None, "members": []}
    members = []
    for key, ref in (genera[gid].get("members") or {}).items():
        members.append({
            "member_kind": ref.get("member_kind"),
            "word": ref.get("word"),
            "genus": ref.get("genus"),
            "coordinate": ref.get("coordinate"),
            "status": ref.get("status"),
            "provenance_lineages": sorted(
                _distinct_lineages(ref))})
    return {"genus": gid, "members": members}

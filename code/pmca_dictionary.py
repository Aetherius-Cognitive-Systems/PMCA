"""PMCA Dictionary - Webster 1913 definition lookup tier.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

Ruling (Jonathan 2026-10-20): "We have to [point] the system
t[o] the dictionary for definitions." This module is that
pointer: a read-only lookup tier over the stored Webster's
Revised Unabridged Dictionary (1913) database, built
2026-10-18 into data/webster1913.db (SQLite, table defs:
word -> JSON list of up to 5 definition strings). The local
original reader (data/webster_lookup.py) stays untouched;
this module carries the same normalization and schema beside
the analyzer, plus the inflection resolution and form reading
the analyzer's wiring needs.

What a lookup returns is MEANING only: the stored definitions,
and a form read off the stored text. Definitions never assign
harm and never create relationships on their own (Jonathan
2026-10-15). Form is a property of the text, not a text match
for meaning: a headword is form "verb" iff any of its
definitions is a verb sense under the ONE shared cleaned
decoding (pmca_verb_capable.form_of — apparatus stripped,
then the GCIDE "To " convention), else form "other"
(Candidacy Rule Set C-4: CANDIDATE evidence only; the former
RAW unstripped test in this module is RETIRED as a mechanism,
DICT-1 Part 1, WG2 2026-11-01).

Resolution: exact headword first; else inflection candidates
generated in one fixed order (see _candidates): -ies words
try the "s" strip, then ies -> y, then the "es" strip; -ing
and -ed words try the stem + "e" (silent-e restoration), then
the undoubled stem, then the raw stem; plain -es / -s words
strip the suffix. The way dictionaries cross-reference
inflections, with the standard orthographic repairs (ies -> y,
silent-e restoration, consonant undoubling) tried before raw
slicing, which remains the last resort. No other stemming.
First candidate that hits a headword wins.

Configuration (exactly as _resolve_path implements it):
configure(path) explicit call wins; else the
PMCA_DICTIONARY_DB environment variable; else None — the
tier is OFF. There is no default path and no fallback: an
earlier <module_dir>/data/webster1913.db default was REMOVED
by Pack G (2026-10-26), because it let guard lookups resolve
against the stored dictionary in processes that had never
configured it while the analyzer reported the tier off and
its code manifest omitted the dictionary (claims audit 06,
synthesis D12); it is recorded here as history only and is
not implemented. An absent or unreadable DB means the tier
is simply off: nothing raises at import time, and lookup
returns None.

Deterministic. Stdlib only. Same word + same DB bytes gives
the same result, always.
"""

import hashlib
import json
import os
import sqlite3
import threading

import pmca_verb_capable as _vc

_UNSET = object()
_configured = _UNSET
_conn = None
_conn_path = None
_lock = threading.Lock()
_hash_cache = {}

def _undoubled(stem):
    """The stem with a doubled final consonant undoubled, or
    None when the stem does not end in one: "runn" -> "run",
    "stopp" -> "stop". A doubled vowel never counts ("see"
    is not an undoubling of anything)."""
    if len(stem) >= 2 and stem[-1] == stem[-2] \
            and stem[-1] not in "aeiou":
        return stem[:-1]
    return None


def _candidates(w):
    """Inflection candidates for a non-headword w, in the ONE
    fixed order lookup checks them (first headword hit wins):

      -ies words: strip "s" (cookies -> cookie, where the -ie
        form is the base), then ies -> y (tries -> try,
        carries -> carry), then strip "es" (trailing below);
      -ing words: stem + "e" (silent-e restoration:
        caring -> care, believing -> believe), then the
        undoubled stem (zipping -> zip), then the raw stem
        (walking -> walk);
      -ed words: stem + "e" (silent-e restoration), then the
        undoubled stem (zipped -> zip), then the raw stem
        (walked -> walk);
      plain -es / -s words: strip the suffix (the original
        raw slicing).

    Raw slicing is the last resort inside each family. A word
    matches at most one of the -ies / -ing / -ed families;
    within the s-family the original strip precedence holds
    ("s" before "es": cares -> care, never car), with ies -> y
    inserted between them — ahead of the "es" slice that used
    to land varies on "vari" (claims audit 06, synthesis D23)."""
    out = []

    def add(cand):
        if cand and cand != w and cand not in out:
            out.append(cand)

    if w.endswith("ies") and len(w) > 3:
        add(w[:-1])
        add(w[:-3] + "y")
    if w.endswith("ing") and len(w) > 3:
        stem = w[:-3]
        add(stem + "e")
        add(_undoubled(stem))
        add(stem)
    elif w.endswith("ed") and len(w) > 2:
        stem = w[:-2]
        add(stem + "e")
        add(_undoubled(stem))
        add(stem)
    if w.endswith("s") and len(w) > 1:
        add(w[:-1])
    if w.endswith("es") and len(w) > 2:
        add(w[:-2])
    return out


def configure(path):
    """Set the DB path explicitly. path None marks the tier
    off (an explicit off wins over the environment). Any open
    connection is dropped, so the next lookup reopens against
    the newly configured path."""
    global _configured, _conn, _conn_path
    with _lock:
        _configured = path
        if _conn is not None:
            _conn.close()
            _conn = None
            _conn_path = None


def _resolve_path():
    """The configured path, else the environment's, else None
    (unconfigured means the tier is OFF — there is no default
    path; Pack G, 2026-10-26). None also when explicitly off."""
    if _configured is not _UNSET:
        return _configured
    env = os.environ.get("PMCA_DICTIONARY_DB")
    if env:
        return env
    return None


def db_path():
    """The resolved DB path when it names an existing file,
    else None (the tier is off)."""
    path = _resolve_path()
    if path and os.path.isfile(path):
        return path
    return None


def _normalize(word):
    """Same normalization as data/webster_lookup.py: strip,
    lowercase, drop a trailing possessive "'s"."""
    w = word.strip().lower()
    if w.endswith("'s"):
        w = w[:-2]
    return w


def _connection(path):
    """The one read-only connection, reopened when the path
    changes. Caller holds the lock."""
    global _conn, _conn_path
    if _conn is None or _conn_path != path:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect("file:" + path + "?mode=ro",
                                uri=True, check_same_thread=False)
        _conn_path = path
    return _conn


def _fetch(con, word):
    """Definition list for an exact headword, or None when the
    headword is absent (or its stored value is not a JSON list
    of strings). Caller holds the lock."""
    row = con.execute("SELECT defs FROM defs WHERE word=?",
                      (word,)).fetchone()
    if row is None:
        return None
    try:
        defs = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    if not isinstance(defs, list):
        return None
    return [d for d in defs if isinstance(d, str)]


def _form(definitions):
    """Form read off the stored text, under the Candidacy Rule
    Set (DICT-1 Part 1, WG2 2026-11-01): the ONE shared
    implementation, pmca_verb_capable.form_of — the cleaned
    Webster-convention decoding (apparatus labels and sense
    markers stripped before the "To " test), held at
    CANDIDATE tier by C-4. This module's former RAW test (the
    unstripped string) is RETIRED as a mechanism; the value's
    tier and provenance are read via
    pmca_verb_capable.pos_answer."""
    return _vc.form_of(definitions)


def lookup(word):
    """Look one word up. Return None when the tier is off or
    the word resolves to no headword; else {"headword": str,
    "definitions": [str, ...], "form": "verb" | "other"},
    where headword is the stored headword the word resolved
    to (the word itself on an exact hit, else the first
    inflection candidate, in _candidates order, that hits)."""
    path = db_path()
    if path is None:
        return None
    w = _normalize(word)
    if not w:
        return None
    with _lock:
        try:
            con = _connection(path)
            defs = _fetch(con, w)
            if defs is not None:
                return {"headword": w, "definitions": defs,
                        "form": _form(defs)}
            for cand in _candidates(w):
                defs = _fetch(con, cand)
                if defs is not None:
                    return {"headword": cand,
                            "definitions": defs,
                            "form": _form(defs)}
        except sqlite3.Error:
            return None
    return None


def file_hash():
    """sha256 hex of the DB file bytes, for the analyzer's
    code manifest. None when no DB is configured/found.
    Computed once per path per process."""
    path = db_path()
    if path is None:
        return None
    if path not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
        _hash_cache[path] = h.hexdigest()
    return _hash_cache[path]

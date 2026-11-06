"""PMCA Math Parse - the Tools base's expression forms, parsed exactly.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's
architecture. Build of 2026-10-28 (math-parse pack).

What this module IS: a deterministic parser and evaluator for
arithmetic / relational expressions, of the kind the Tools
(mathematic/science) base supplies as forms. The Tools base
supplies forms and processes; central logic applies FROM it,
never TO it - so this module offers the form (a stored-form
tree) and a separate evaluation step, and never lets the two
fuse.

Rules it is built under:

- NON-INTEGERS ARE RATIONAL PAIRS (ruling, 2026-10-20). Every
  quantity is an exact rational (fractions.Fraction). Decimals
  become rationals from their digit string. No float is
  constructed anywhere in this module: 0.1 + 0.2 is exactly
  3/10 here, by construction, not by rounding luck.
- Meaning is looked up, never computed or assumed. Evaluation
  resolves a variable ONLY through the caller-supplied stored
  bindings. No binding -> HOLD with a typed gap record
  (unknown_variable:<name>). Never a default, never 0.
- NO SILENT GAPS. Division by a zero rational, a non-integer
  exponent, an unusable binding, and malformed input each
  HOLD with exactly one typed gap record. No exception
  escapes this module.
- Parse != evaluate. The parse result is a stored-form tree
  of nested records {op or value, children, source span};
  numbers render as rational pairs [numerator, denominator].
  Parsing an expression with unknown variables succeeds
  structurally; only evaluation can HOLD on them.
- Records are CANDIDATES in a dedicated store, stored once:
  each public call emits at most one record (kind parse |
  evaluation | gap), id = sha256 of its canonical form,
  provenance "math_parse", no timestamps. Re-running an
  input adds zero records.

Grammar (recursive descent - chosen because precedence and
associativity live explicitly in the call chain and error
positions map directly onto source offsets):

  relation       := additive (REL additive)?      REL in = < > <= >=
  additive       := multiplicative (('+'|'-') multiplicative)*
  multiplicative := unary (('*'|'/') unary)*
  unary          := ('-'|'+') unary | power
  power          := atom ('^' unary)?             right-associative
  atom           := NUMBER | IDENT | '(' additive ')'

UNARY RULE: exponentiation binds tighter than unary minus,
so -3^2 = -(3^2) = -9; (-3)^2 = 9. Relations appear only at
the top level, at most one per expression.

What this module is NOT:

- No symbolic algebra, no equation solving, no simplification,
  no units, no functions, no float path. Closed-form symbolic
  work belongs to the R5 formal tools (SymPy), not here.
- R15 Kracht sign algebra is deliberately NOT instantiated
  here. The record (gathered geometry record, R15; consolidated
  solutions section 2.7) specifies sigma = <e, c, m> - exponent
  string, syntactic category, math term - a categorial sign
  whose five modes (add, mul, diff_sum, diff_prod, equals) are
  category-gated composition / derivative rules over math
  terms. It is not a {+, -, 0} value-sign propagation algebra,
  and the record contains no such propagation tables; the
  v18 engine source is not present in this tree. Inventing
  tables and calling them Kracht would be fabrication, so
  this is recorded as an explicit gap in the build report.
"""

import hashlib
import json
import os
import re
from fractions import Fraction

__all__ = ["parse_expression", "evaluate", "canonical"]


# ------------------------------------------------------------------
# Canonical form + record identity (house convention)
# ------------------------------------------------------------------

def canonical(obj):
    """The one canonical JSON form used for identities and the
    store: sorted keys, tight separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pair(frac):
    """Render a Fraction as a rational pair [numerator, denominator]."""
    return [frac.numerator, frac.denominator]


# F-9 (2026-10-29): _MAX_DIGITS is an engineering parameter - the
# estimated decimal-digit bound for any numerator/denominator the
# evaluator will materialize. It sits below Python's 4300-digit
# int<->str conversion limit so canonical record emission can
# never trip that limit on an exact-but-huge result.
_MAX_DIGITS = 4096


def _est_digits(n):
    """Estimated decimal digit count of an int, WITHOUT str():
    log10(2) scaled by 100000, from the bit length only - never
    converts, so it is safe on ints past the conversion limit."""
    return (abs(n).bit_length() * 30103) // 100000 + 1


# Alias under the unprefixed name used in the F-9 design note.
est_digits = _est_digits


# ------------------------------------------------------------------
# Tokenizer
# ------------------------------------------------------------------

class _ParseError(Exception):
    def __init__(self, message, position):
        super().__init__(message)
        self.message = message
        self.position = position


_TOKEN_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?|\.\d+)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<rel><=|>=|==|=|<|>)"
    r"|(?P<op>[+\-*/^()])"
    r"|(?P<ws>\s+)"
)


class _Token(object):
    __slots__ = ("kind", "text", "start", "end")

    def __init__(self, kind, text, start, end):
        self.kind = kind
        self.text = text
        self.start = start
        self.end = end


def _tokenize(text):
    tokens = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise _ParseError(
                "unexpected character %r" % text[pos], pos)
        kind = m.lastgroup
        if kind != "ws":
            tokens.append(_Token(kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return tokens


def _number_value(text):
    """Exact rational from a decimal token's digit string."""
    if text.startswith("."):
        text = "0" + text
    return Fraction(text)


# ------------------------------------------------------------------
# Parser (recursive descent). Tree nodes are plain dicts:
#   {"kind": "num", "value": [n, d], "span": [s, e]}
#   {"kind": "var", "name": str, "span": [s, e]}
#   {"kind": "neg", "operand": node, "span": [s, e]}
#   {"kind": "binop", "op": "+|-|*|/|^", "left": node,
#    "right": node, "span": [s, e]}
#   {"kind": "relation", "op": "=|<|>|<=|>=", "left": node,
#    "right": node, "span": [s, e]}
# ------------------------------------------------------------------

class _Parser(object):
    def __init__(self, text):
        self.text = text
        self.tokens = _tokenize(text)
        self.i = 0

    def _peek(self):
        if self.i < len(self.tokens):
            return self.tokens[self.i]
        return None

    def _next(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _fail(self, message, tok=None):
        if tok is not None:
            raise _ParseError(message, tok.start)
        raise _ParseError(message, len(self.text))

    def parse(self):
        if not self.tokens:
            self._fail("empty input", _Token("eof", "", 0, 0))
        node = self._relation()
        tok = self._peek()
        if tok is not None:
            self._fail("unexpected trailing input %r" % tok.text, tok)
        return node

    def _relation(self):
        left = self._additive()
        tok = self._peek()
        if tok is not None and tok.kind == "rel":
            self._next()
            op = "=" if tok.text == "==" else tok.text
            right = self._additive()
            return {"kind": "relation", "op": op, "left": left,
                    "right": right,
                    "span": [left["span"][0], right["span"][1]]}
        return left

    def _additive(self):
        node = self._multiplicative()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != "op" or tok.text not in "+-":
                return node
            self._next()
            right = self._multiplicative()
            node = {"kind": "binop", "op": tok.text, "left": node,
                    "right": right,
                    "span": [node["span"][0], right["span"][1]]}

    def _multiplicative(self):
        node = self._unary()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != "op" or tok.text not in "*/":
                return node
            self._next()
            right = self._unary()
            node = {"kind": "binop", "op": tok.text, "left": node,
                    "right": right,
                    "span": [node["span"][0], right["span"][1]]}

    def _unary(self):
        tok = self._peek()
        if tok is not None and tok.kind == "op" and tok.text in "+-":
            self._next()
            operand = self._unary()
            if tok.text == "+":
                return operand
            return {"kind": "neg", "operand": operand,
                    "span": [tok.start, operand["span"][1]]}
        return self._power()

    def _power(self):
        base = self._atom()
        tok = self._peek()
        if tok is not None and tok.kind == "op" and tok.text == "^":
            self._next()
            exponent = self._unary()
            return {"kind": "binop", "op": "^", "left": base,
                    "right": exponent,
                    "span": [base["span"][0], exponent["span"][1]]}
        return base

    def _atom(self):
        tok = self._peek()
        if tok is None:
            self._fail("unexpected end of input")
        if tok.kind == "num":
            self._next()
            return {"kind": "num",
                    "value": _pair(_number_value(tok.text)),
                    "span": [tok.start, tok.end]}
        if tok.kind == "ident":
            self._next()
            return {"kind": "var", "name": tok.text,
                    "span": [tok.start, tok.end]}
        if tok.kind == "op" and tok.text == "(":
            self._next()
            node = self._additive()
            closing = self._peek()
            if closing is None or closing.text != ")":
                self._fail("expected ')'", closing)
            self._next()
            return node
        self._fail("unexpected token %r" % tok.text, tok)


def _parse_tree(text):
    return _Parser(text).parse()


# ------------------------------------------------------------------
# Evaluation by lookup
# ------------------------------------------------------------------

class _Hold(Exception):
    def __init__(self, gap):
        super().__init__(gap["type"])
        self.gap = gap


def _check_size(frac, span=None):
    """F-9: HOLD (result_too_large) if a Fraction's numerator or
    denominator is estimated past _MAX_DIGITS decimal digits."""
    if (_est_digits(frac.numerator) > _MAX_DIGITS
            or _est_digits(frac.denominator) > _MAX_DIGITS):
        gap = {"type": "result_too_large"}
        if span is not None:
            gap["span"] = span
        raise _Hold(gap)


def _normalize_binding(name, value, span):
    """A stored binding becomes an exact Fraction, or HOLDs.
    Accepted stored forms: Fraction, int (not bool), a
    [numerator, denominator] pair, or a string Fraction()
    accepts ("3/4", "0.25", "7"). Anything else is not a
    stored quantity and cannot be assumed into one."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        pass  # a truth value is not a quantity; fall through to HOLD
    elif isinstance(value, int):
        return Fraction(value)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        num, den = value
        if (isinstance(num, int) and not isinstance(num, bool)
                and isinstance(den, int) and not isinstance(den, bool)
                and den != 0):
            return Fraction(num, den)
    elif isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError):
            pass
    raise _Hold({"type": "invalid_binding:%s" % name,
                 "variable": name, "span": span})


def _eval(node, bindings):
    kind = node["kind"]
    if kind == "num":
        result = Fraction(node["value"][0], node["value"][1])
        _check_size(result, node.get("span"))
        return result
    if kind == "var":
        name = node["name"]
        if name not in bindings:
            raise _Hold({"type": "unknown_variable:%s" % name,
                         "variable": name, "span": node["span"]})
        result = _normalize_binding(name, bindings[name], node["span"])
        _check_size(result, node.get("span"))
        return result
    if kind == "neg":
        result = -_eval(node["operand"], bindings)
        _check_size(result, node.get("span"))
        return result
    if kind == "binop":
        left = _eval(node["left"], bindings)
        right = _eval(node["right"], bindings)
        op = node["op"]
        if op == "+":
            result = left + right
        elif op == "-":
            result = left - right
        elif op == "*":
            result = left * right
        elif op == "/":
            if right == 0:
                raise _Hold({"type": "division_by_zero",
                             "span": node["span"]})
            result = left / right
        elif op == "^":
            if right.denominator != 1:
                raise _Hold({"type": "non_integer_exponent",
                             "span": node["span"]})
            if left == 0 and right < 0:
                raise _Hold({"type": "division_by_zero",
                             "span": node["span"]})
            e = right.numerator
            # F-9 power PRE-check: estimate the result size and
            # HOLD before computing left ** e (which may be a
            # ~370M-digit integer for e.g. 9^9^9). Skipped for
            # bases 0/1/-1 and e == 0, whose results stay tiny.
            # For negative e the result's numerator/denominator
            # are den^|e| / num^|e| in some order, so the max()
            # of the base's part estimates covers both.
            if left not in (0, 1, -1) and e != 0:
                estimate = abs(e) * max(
                    _est_digits(left.numerator),
                    _est_digits(left.denominator))
                if estimate > _MAX_DIGITS:
                    raise _Hold({"type": "result_too_large",
                                 "span": node["span"]})
            result = left ** e
        else:
            raise _Hold({"type": "internal_error:unknown_node",
                         "span": node.get("span")})
        _check_size(result, node.get("span"))
        return result
    if kind == "relation":
        left = _eval(node["left"], bindings)
        right = _eval(node["right"], bindings)
        op = node["op"]
        if op == "=":
            return left == right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
    raise _Hold({"type": "internal_error:unknown_node",
                 "span": node.get("span")})


# ------------------------------------------------------------------
# Records: CANDIDATE, stored once, deterministic ids
# ------------------------------------------------------------------

def _bindings_for_record(bindings):
    """Deterministic record form of the bindings: each value in
    its normalized rational pair when it normalizes, else a
    typed placeholder. Used for record identity only."""
    out = {}
    for name in sorted(bindings):
        try:
            normalized = _normalize_binding(name, bindings[name],
                                            None)
        except _Hold:
            out[name] = ["invalid", type(bindings[name]).__name__]
            continue
        # F-9: an oversized normalized binding must NOT be recorded
        # as its [num, den] pair - serializing that pair is what
        # raised past the conversion limit. Use a typed placeholder
        # instead, in the style of the non-pair branch above, so
        # the HOLD path's own _emit stays safe.
        if (_est_digits(normalized.numerator) > _MAX_DIGITS
                or _est_digits(normalized.denominator) > _MAX_DIGITS):
            out[name] = ["oversized", type(bindings[name]).__name__]
        else:
            out[name] = _pair(normalized)
    return out


def _emit(store_path, body):
    """Append one CANDIDATE record unless its id is already in
    the store (stored-once). Returns True if a record was added."""
    if store_path is None:
        return False
    record = dict(body)
    record["id"] = _sha256_text(canonical(body))
    existing = set()
    if os.path.exists(store_path):
        with open(store_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    existing.add(json.loads(line)["id"])
    if record["id"] in existing:
        return False
    with open(store_path, "a", encoding="utf-8") as fh:
        fh.write(canonical(record) + "\n")
    return True


def _gap_body(kind_input, gap):
    return {"kind": "gap", "status": "CANDIDATE",
            "provenance": "math_parse", "input": kind_input,
            "gap": gap}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def parse_expression(text, store_path=None):
    """Parse only. Returns {"status": "ok", "tree": ...} or
    {"status": "HOLD", "gap": {...}}. Emits one parse record
    on success, one gap record on failure."""
    try:
        tree = _parse_tree(text)
    except _ParseError as exc:
        gap = {"type": "parse_error", "message": exc.message,
               "position": exc.position}
        _emit(store_path, _gap_body(text, gap))
        return {"status": "HOLD", "gap": gap}
    except Exception as exc:  # safety net: nothing escapes
        gap = {"type": "internal_error:%s" % type(exc).__name__}
        _emit(store_path, _gap_body(text, gap))
        return {"status": "HOLD", "gap": gap}
    _emit(store_path, {"kind": "parse", "status": "CANDIDATE",
                       "provenance": "math_parse", "input": text,
                       "tree": tree})
    return {"status": "ok", "tree": tree}


def evaluate(tree_or_text, bindings=None, store_path=None):
    """Evaluate by lookup. Accepts a source string or a tree
    from parse_expression. Returns one of:
      {"status": "value", "value": [n, d]}
      {"status": "bool", "value": True|False}
      {"status": "HOLD", "gap": {...}}
    Emits one evaluation record on resolution, one gap record
    on HOLD. When given a string, parsing is internal and
    emits no separate parse record - one call, at most one
    record."""
    if bindings is None:
        bindings = {}
    if isinstance(tree_or_text, str):
        input_form = tree_or_text
        try:
            tree = _parse_tree(tree_or_text)
        except _ParseError as exc:
            gap = {"type": "parse_error", "message": exc.message,
                   "position": exc.position}
            _emit(store_path, _gap_body(input_form, gap))
            return {"status": "HOLD", "gap": gap}
        except Exception as exc:  # safety net: nothing escapes
            gap = {"type": "internal_error:%s" % type(exc).__name__}
            _emit(store_path, _gap_body(input_form, gap))
            return {"status": "HOLD", "gap": gap}
    else:
        tree = tree_or_text
        try:
            input_form = canonical(tree)
        except Exception as exc:  # F-9: a hand-built tree carrying
            # >4300-digit literals raises here, outside the eval
            # try - record it instead of leaving a silent gap.
            gap = {"type": "internal_error",
                   "error": type(exc).__name__}
            body = _gap_body(None, gap)
            try:
                body["bindings"] = _bindings_for_record(bindings)
            except Exception:
                body["bindings"] = {}
            _emit(store_path, body)
            return {"status": "HOLD", "gap": gap}
    record_bindings = _bindings_for_record(bindings)
    try:
        result = _eval(tree, bindings)
    except _Hold as hold:
        body = _gap_body(input_form, hold.gap)
        body["bindings"] = record_bindings
        _emit(store_path, body)
        return {"status": "HOLD", "gap": hold.gap}
    except Exception as exc:  # safety net: nothing escapes
        gap = {"type": "internal_error:%s" % type(exc).__name__}
        body = _gap_body(input_form, gap)
        body["bindings"] = record_bindings
        _emit(store_path, body)
        return {"status": "HOLD", "gap": gap}
    if isinstance(result, bool):
        outcome = {"status": "bool", "value": result}
    else:
        outcome = {"status": "value", "value": _pair(result)}
    body = {"kind": "evaluation", "status": "CANDIDATE",
            "provenance": "math_parse", "input": input_form,
            "bindings": record_bindings, "result": outcome}
    _emit(store_path, body)
    return outcome

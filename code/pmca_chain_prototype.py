"""PMCA Chain Rule Prototype - Item 1.

Architecture and design: Jonathan Wayne Fleuren (the builder).
Code logic implemented by Alder (Muse) per Jonathan's architecture.

Rulings used here (Jonathan 2026-10-16):
- Target = subject (primary), object (secondary).
- Layer 1 = definers, identifiers (POS, permanent, code, foundational).
- Layer 2 = nuance of context, dictionary definitions.
- Layer 3 = removes unnecessary definitions, applies actions.
- Seed is a map. Do not flip a map. Change trajectory.
  Read edge from target outward (knife to sharp).
- Harm from structure, not predefined. Slurs at max.
  No slurs in test cases, so no harm values here. Identifiers only.

Rulings added here (Jonathan 2026-10-17):
- Direction is context to target, target to target.
- Consequence is outcome, results.
- Chain is connectivity of structure points, progression of
  events, compounding of actions, adjectives, and adverbs.
- Five Chain build suggestions approved by Jonathan:
  link in Direction order; progression as Cause to Reason
  to Consequence; compounding by accumulation, not
  replacement; verify each link by lookup, HOLD if missing;
  harm on edges along the Chain, not on the Chain as a whole.

Rules for this prototype:
- Target-only for simple cases. Target = subject. Read via trajectory.
- Chain cases (compound actions, coercion, passive holding)
  go through build_chain: links by lookup only, in Direction
  order, labelled Cause / Reason / Consequence, accumulated
  not replaced. Complete trace resolves. A missing point
  HOLDs and names the missing link (example: the unnamed
  holder of a captive gives captor_undefined).
- HOLD for ambiguity (two definitions, no context to select).
  Marker "ambiguous".
- HOLD for anything else unmapped.

Deterministic. Stdlib only. Integers only. No non-deterministic choice. No statistics.
Same input + same state gives same output, always.
"""

# Layer 1: definers, identifiers. Word -> part of speech.
LAYER1 = {
    "he": "pronoun",
    "made": "verb",
    "loaf": "noun",
    "pumpernickel": "adjective",
    "bread": "noun",
    "knife": "noun",
    "had": "verb",
    "sharp": "adjective",
    "serrated": "adjective",
    "edge": "noun",
    "surgery": "noun",
    "treats": "verb",
    "injury": "noun",
    "is": "verb",
    "a": "article",
    "tool": "noun",
    "bomb": "noun",
    "causes": "verb",
    "explosion": "noun",
    "of": "preposition",
    "flour": "noun",
    "quickly": "adverb",
    "cut": "verb",
    "boulder": "noun",
    "rolls": "verb",
    "down": "preposition",
    "hill": "noun",
    "and": "conjunction",
    "crushes": "verb",
    "person": "noun",
    "chef": "noun",
    "was": "verb",
    "held": "verb",
    "captive": "adjective",
    "being": "verb",
    "forced": "verb",
    "cook": "verb",
    "for": "preposition",
    "criminal": "noun",
    "himself": "pronoun",
    "in": "preposition",
    "fear": "noun",
    "chicken": "noun",
    "ready": "adjective",
    "to": "preposition",
    "eat": "verb",
    "the": "article",
}

# Layer 2: nuance of context. Word -> list of definitions.
# Chicken has two definitions. That is ambiguity, not an error.
LAYER2 = {
    "he": ["person pronoun"],
    "knife": ["tool with a handle and blade"],
    "bread": ["food made from flour and water"],
    "surgery": ["procedure to repair damage"],
    "injury": ["damage to a person"],
    "tool": ["object used to do work"],
    "bomb": ["explosive weapon"],
    "explosion": ["sudden release of energy"],
    "flour": ["powder made from grain"],
    "sharp": ["having a fine edge"],
    "quickly": ["in a fast manner"],
    "cut": ["sever with a sharp tool"],
    "chicken": ["bird kept for eggs and meat", "meat from a chicken bird"],
    "boulder": ["giant rock or stone"],
    "rolls": ["moves by turning over"],
    "crushes": ["presses with force"],
    "person": ["human being"],
    "chef": ["person who cooks"],
    "captive": ["person held against will"],
    "criminal": ["person who commits crimes"],
    "fear": ["emotion of being afraid"],
    "edge": ["thin side of a blade"],
    "hill": ["raised ground"],
}


def tokenize(sentence):
    """Split sentence into lowercase words. Strip punctuation."""
    cleaned = []
    for ch in sentence.lower():
        if ch in "abcdefghijklmnopqrstuvwxyz ":
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    text = "".join(cleaned)
    parts = text.split()
    return parts


def get_pos(word):
    """Layer 1 lookup. Return POS or None if not in Layer 1."""
    if word in LAYER1:
        return LAYER1[word]
    return None


def get_definitions(word):
    """Layer 2 lookup. Return list of definitions, empty if none."""
    if word in LAYER2:
        return LAYER2[word]
    return []


def layer3_filter(target, definitions):
    """Layer 3: remove unnecessary definitions.

    If one definition, keep it.
    If two or more definitions and no context selects one,
    HOLD as ambiguous. Do not select without a rule.
    If zero definitions, HOLD as unmapped.
    Returns (kept_definitions, status, marker).
    Status is KEPT or HOLD. Marker is empty or reason.
    """
    if len(definitions) == 0:
        return ([], "HOLD", "unmapped")
    if len(definitions) == 1:
        return (definitions, "KEPT", "")
    return ([], "HOLD", "ambiguous")


def find_subject(tokens):
    """Find target = subject. First noun or pronoun, skipping articles."""
    for tok in tokens:
        pos = get_pos(tok)
        if pos == "noun" or pos == "pronoun":
            return tok
    return None


def find_verb(tokens):
    """Find first verb token. Return verb or None."""
    for tok in tokens:
        if get_pos(tok) == "verb":
            return tok
    return None


def count_verbs(tokens):
    """Count verb tokens. Integer only."""
    total = 0
    for tok in tokens:
        if get_pos(tok) == "verb":
            total = total + 1
    return total


def has_token(tokens, word):
    """Check if word is in tokens. Deterministic lookup."""
    for tok in tokens:
        if tok == word:
            return True
    return False


def find_holder(tokens, subject):
    """Find the holder point for a captive holding, by lookup.

    The holder is a noun other than the subject that appears
    before the verb "held", or the noun after "by".
    Return the holder word, or None if no holder point exists.
    """
    # Agent named after "by".
    index = 0
    while index < len(tokens):
        if tokens[index] == "by":
            scan = index + 1
            while scan < len(tokens):
                if get_pos(tokens[scan]) == "noun" and tokens[scan] != subject:
                    return tokens[scan]
                scan = scan + 1
        index = index + 1
    # Noun before "held", other than the subject.
    held_index = None
    index = 0
    while index < len(tokens):
        if tokens[index] == "held":
            held_index = index
        index = index + 1
    if held_index is not None:
        scan = 0
        while scan < held_index:
            if get_pos(tokens[scan]) == "noun" and tokens[scan] != subject:
                return tokens[scan]
            scan = scan + 1
    return None


def build_chain(sentence):
    """Build a Chain trace by lookup only.

    Chain (Jonathan 2026-10-17): connectivity of structure
    points, progression of events, compounding of actions,
    adjectives, and adverbs.
    Direction (Jonathan 2026-10-17): context to target, target
    to target. Links are built in Direction order.
    Progression labels: the first link is the Cause (start of
    event), the last link is the Consequence (outcome,
    results), a middle link is the Reason (the why between
    start and outcome).
    Compounding is by accumulation: links are added in order,
    none are replaced.
    Every part of every link is verified by Layer 1 / Layer 2
    lookup. If a required point is missing, return HOLD naming
    the missing link. Do not fill a missing point from a
    nearby word.
    Returns (status, subject, trace, marker). Status is
    TRACED or HOLD. Trace is a tuple of (label, actor, action,
    point) links in Direction order. No harm values: this
    prototype assigns identifiers and traces only.
    """
    tokens = tokenize(sentence)
    subject = find_subject(tokens)
    if subject is None:
        return ("HOLD", None, (), "unmapped")
    if len(get_definitions(subject)) == 0:
        return ("HOLD", subject, (), "link_undefined:" + subject)

    # A captive holding needs a holder. The subject is the one
    # held, so the holder is a different point. If no holder
    # point exists in the sentence, the link is missing.
    # HOLD and name it.
    if has_token(tokens, "captive"):
        holder = find_holder(tokens, subject)
        if holder is None:
            return ("HOLD", subject, (), "captor_undefined")

    # Collect action verbs in token order (Direction order).
    actions = []
    index = 0
    while index < len(tokens):
        if get_pos(tokens[index]) == "verb":
            actions.append((index, tokens[index]))
        index = index + 1
    if len(actions) == 0:
        return ("HOLD", subject, (), "unmapped")

    # Build links in Direction order. Each action links the
    # subject to the next unused noun point after the action.
    # Every action and point must verify by Layer 2 lookup.
    used_points = []
    raw_links = []
    for verb_index, verb in actions:
        if len(get_definitions(verb)) == 0:
            return ("HOLD", subject, (), "link_undefined:" + verb)
        point = None
        scan = verb_index + 1
        while scan < len(tokens):
            word = tokens[scan]
            if get_pos(word) == "noun" and word != subject:
                if word not in used_points:
                    point = word
                    break
            scan = scan + 1
        if point is None:
            return ("HOLD", subject, (), "link_undefined:point_after_" + verb)
        if len(get_definitions(point)) == 0:
            return ("HOLD", subject, (), "link_undefined:" + point)
        used_points.append(point)
        raw_links.append((subject, verb, point))

    # Label by position: first Cause, last Consequence,
    # middle Reason. Accumulate in order. Replace nothing.
    trace = []
    total = len(raw_links)
    position = 0
    for actor, verb, point in raw_links:
        if position == 0:
            label = "Cause"
        elif position == total - 1:
            label = "Consequence"
        else:
            label = "Reason"
        trace.append((label, actor, verb, point))
        position = position + 1
    return ("TRACED", subject, tuple(trace), "")


def chain_verdict(sentence):
    """Translate a build_chain result into resolve_sentence shape.

    Traced chain -> RESOLVED with identifier "chain".
    Missing link -> HOLD naming the missing link.
    """
    status, subject, trace, marker = build_chain(sentence)
    if status == "TRACED":
        return ("RESOLVED", subject, "chain", "")
    return ("HOLD", subject, None, marker)


def resolve_sentence(sentence):
    """Resolve one sentence to (status, target, identifier, marker).

    Status is RESOLVED or HOLD.
    For RESOLVED, identifier is the relationship identifier,
    or "chain" when a Chain trace was built by lookup.
    For HOLD, marker explains why: captor_undefined,
    link_undefined:<word>, ambiguous, or unmapped.
    """
    tokens = tokenize(sentence)

    # Chain signals. Chain cases go through build_chain, which
    # builds the trace by lookup only, in Direction order.
    # Compound predicate: two verbs joined by "and", same subject.
    # Coercion / passive / self-cut signals: forced, captive,
    # himself. A traced chain resolves. A missing link HOLDs
    # and names the missing link.
    if has_token(tokens, "and") and count_verbs(tokens) >= 2:
        return chain_verdict(sentence)
    if has_token(tokens, "forced"):
        return chain_verdict(sentence)
    if has_token(tokens, "captive"):
        return chain_verdict(sentence)
    if has_token(tokens, "himself"):
        return chain_verdict(sentence)

    # Target = subject. First noun or pronoun.
    target = find_subject(tokens)
    if target is None:
        return ("HOLD", None, None, "unmapped")

    # Layer 2: definitions for target.
    definitions = get_definitions(target)
    kept, status, marker = layer3_filter(target, definitions)
    if status == "HOLD":
        return ("HOLD", target, None, marker)

    # Layer 3: apply actions. Verb pattern gives identifier.
    # Order matters. Check most specific pattern first.
    # "is made of" -> made_of.
    if has_token(tokens, "is") and has_token(tokens, "made") and has_token(tokens, "of"):
        return ("RESOLVED", target, "made_of", "")

    # Copula "is a" -> is_a.
    if has_token(tokens, "is") and has_token(tokens, "a"):
        return ("RESOLVED", target, "is_a", "")

    verb = find_verb(tokens)
    if verb is None:
        return ("HOLD", target, None, "unmapped")

    if verb == "treats":
        return ("RESOLVED", target, "treats", "")
    if verb == "causes":
        return ("RESOLVED", target, "causes", "")
    if verb == "made":
        return ("RESOLVED", target, "made", "")
    if verb == "had":
        return ("RESOLVED", target, "has_part", "")

    return ("HOLD", target, None, "unmapped")


def resolve_pair(modifier, head):
    """Resolve modifier + head pair via trajectory (target outward).

    Target is head. Read from target outward to modifier.
    Adjective + noun -> descriptor.
    Adverb + verb -> modifier.
    Else HOLD unmapped.
    """
    mod_pos = get_pos(modifier)
    head_pos = get_pos(head)
    if mod_pos is None or head_pos is None:
        return ("HOLD", head, None, "unmapped")
    if mod_pos == "adjective" and head_pos == "noun":
        return ("RESOLVED", head, "descriptor", "")
    if mod_pos == "adverb" and head_pos == "verb":
        return ("RESOLVED", head, "modifier", "")
    return ("HOLD", head, None, "unmapped")


def run_suite():
    """Run all test cases. Return list of result tuples."""
    results = []

    # Case 1: He made a loaf of pumpernickel bread.
    r = resolve_sentence("He made a loaf of pumpernickel bread")
    results.append(("1_he_made_bread", r))

    # Case 2: The knife had a sharp serrated edge.
    r = resolve_sentence("The knife had a sharp serrated edge")
    results.append(("2_knife_had_edge", r))

    # Case 3: Surgery treats injury.
    r = resolve_sentence("Surgery treats injury")
    results.append(("3_surgery_treats", r))

    # Case 4: Knife is a tool.
    r = resolve_sentence("Knife is a tool")
    results.append(("4_knife_is_tool", r))

    # Case 5: Bomb causes explosion.
    r = resolve_sentence("Bomb causes explosion")
    results.append(("5_bomb_causes", r))

    # Case 6: Bread is made of flour.
    r = resolve_sentence("Bread is made of flour")
    results.append(("6_bread_made_of", r))

    # Case 7: Sharp -> knife, read via trajectory knife -> sharp.
    r = resolve_pair("sharp", "knife")
    results.append(("7_sharp_knife_trajectory", r))

    # Case 8: Quickly -> cut, read via trajectory cut -> quickly.
    r = resolve_pair("quickly", "cut")
    results.append(("8_quickly_cut_trajectory", r))

    # Case 9: Boulder rolls and crushes person. Chain. HOLD.
    r = resolve_sentence("The boulder rolls down the hill and crushes the person")
    results.append(("9_boulder_chain", r))

    # Case 10: Chef captive, forced, cut himself. Chain. HOLD.
    r = resolve_sentence(
        "The chef was held captive being forced to cook for a criminal. "
        "He cut himself in fear."
    )
    results.append(("10_chef_chain", r))

    # Case 11: Chicken ready to eat. Ambiguous. HOLD.
    r = resolve_sentence("The chicken is ready to eat")
    results.append(("11_chicken_ambiguous", r))

    # Case 12: Boulder chain trace, built by lookup only.
    # Direction order: rolls first (Cause, start of event),
    # crushes to person last (Consequence, outcome).
    r = build_chain("The boulder rolls down the hill and crushes the person")
    results.append(("12_boulder_trace", r))

    # Case 13: Chef chain trace. The holder of the captive is
    # not named in the sentence. HOLD naming the missing link.
    r = build_chain(
        "The chef was held captive being forced to cook for a criminal. "
        "He cut himself in fear."
    )
    results.append(("13_chef_trace", r))

    return results


def check_suite(results):
    """Check results against expected values. Return list of pass flags."""
    expected = {
        "1_he_made_bread": ("RESOLVED", "he", "made", ""),
        "2_knife_had_edge": ("RESOLVED", "knife", "has_part", ""),
        "3_surgery_treats": ("RESOLVED", "surgery", "treats", ""),
        "4_knife_is_tool": ("RESOLVED", "knife", "is_a", ""),
        "5_bomb_causes": ("RESOLVED", "bomb", "causes", ""),
        "6_bread_made_of": ("RESOLVED", "bread", "made_of", ""),
        "7_sharp_knife_trajectory": ("RESOLVED", "knife", "descriptor", ""),
        "8_quickly_cut_trajectory": ("RESOLVED", "cut", "modifier", ""),
        "9_boulder_chain": ("RESOLVED", "boulder", "chain", ""),
        "10_chef_chain": ("HOLD", "chef", None, "captor_undefined"),
        "11_chicken_ambiguous": ("HOLD", "chicken", None, "ambiguous"),
        "12_boulder_trace": (
            "TRACED",
            "boulder",
            (
                ("Cause", "boulder", "rolls", "hill"),
                ("Consequence", "boulder", "crushes", "person"),
            ),
            "",
        ),
        "13_chef_trace": ("HOLD", "chef", (), "captor_undefined"),
    }
    checks = []
    for name, actual in results:
        want = expected[name]
        passed = (actual == want)
        checks.append((name, passed, actual, want))
    return checks


def main():
    """Run suite twice, verify determinism, print results."""
    run1 = run_suite()
    run2 = run_suite()
    deterministic = (run1 == run2)
    checks = check_suite(run1)
    passed_count = 0
    for name, passed, actual, want in checks:
        if passed:
            passed_count = passed_count + 1
            print("PASS " + name + " -> " + str(actual))
        else:
            print("FAIL " + name + " -> got " + str(actual) + " want " + str(want))
    print("Passed: " + str(passed_count) + " of " + str(len(checks)))
    print("Determinism (two runs identical): " + str(deterministic))
    if passed_count == len(checks) and deterministic:
        print("ALL CHECKS PASS")
    else:
        print("CHECKS FAILED")


if __name__ == "__main__":
    main()

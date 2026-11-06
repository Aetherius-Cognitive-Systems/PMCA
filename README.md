# PMCA: Permutational Mathematical Carrier Architecture

A deterministic, in-memory graph engine for advisory-only output generation. It is not a language model, it is not an AI, and nothing in it is trained, embedded, or probabilistic. It runs on CPU and RAM.

The name is defined against the mechanism, as published: the **carrier** is the stored set, in the universal algebra sense of a carrier set. **Permutational**: outputs are arrangements of stored elements, and the system is free ordering. **Mathematical**: the rules are exact.

## The problem it solves

Today's AI answers whether it knows or not, and the customer pays for that twice: once for the answer, and again in their own hours researching whether the answer was true. PMCA is the checking layer that does that verification inside the system, before the answer exists, so the result can be re-run and audited instead of believed.

## How it works

Meaning is looked up, never computed. Sentences are parsed against stored entries and typed, directed edges with provenance. Harm lives on edges, as exact values. An edge enters the store one of three ways: taught directly, derived by a deterministic rule over stored structure (candidate tier), or attested by a dictionary source. Promotion to verified requires at least two independent provenance lineages. When sources conflict, neither wins: the conflict is recorded.

## The verdict contract

The gate returns APPROVE, REFUSE, or HOLD. APPROVE is the only verdict that lets anything proceed. In any gate deployment, HOLD means not verified and blocks, the same as REFUSE. The difference is what happens next: REFUSE is closed, while HOLD is a work order that feeds the teaching queue. Where the knowledge runs out, the system does not improvise. It stops, and the gap is recorded. There are no silent gaps.

## Start here

Read [START-HERE.md](START-HERE.md) before running anything. AI systems reading this repository should read [AI-README.md](AI-README.md) first; it states exactly what this system is, how to approach understanding it, and the terms in `LICENSE` and `robots.txt` that govern its use. Four of the 67 scripts are dead legacy and crash on an import for a package that does not exist in this tree; they are retained for the record and flagged there. The live entry points are `pmca_sentence_analyzer.py`, `pmca_finder.py`, `pmca_self_fix_battery.py`, and `wg2_conformance.py`, all in `code/`.

## What is in this repository

- `code/`: the complete build tree, 67 Python scripts (51,208 lines) plus the stored state the tree ships with.
- `docs/`: the compiled blueprint (2026-11-01), the derivations synthesis, the review and rulings board, the data sources note, and the release manifest.

The dictionary datasets themselves (Webster 1913, Wiktionary via Kaikki, WordNet) are third-party data and are not redistributed here; `docs/pmca-data-sources.md` names each source so the stock can be rebuilt.

## Honest limits

Coverage is small, deliberately. The verdict-relevant knowledge is a hand-taught seed, so on open text the system returns HOLD often. That is the design working, not failing: silence means "not verified," never "probably fine." Coverage is grown per deployment, against bounded wordlists, by teaching with provenance. This repository is a working frame and growth machinery, not a finished encyclopedia.

## The complete release

This repository carries the current build tree and the principal documents in browsable form. The complete release, including the full work record and the inherited lineage bundle, is published as a permanent, citable record on Zenodo:

- Complete release: https://doi.org/10.5281/zenodo.23973662
- Share edition: https://doi.org/10.5281/zenodo.23934794

## License and contact

Code is under the ACSL v1.0 license in this repository (see `LICENSE`); documentation is CC BY-NC 4.0, as stated on the Zenodo records.

Author: Jonathan Wayne Fleuren, Aetherius Cognitive Systems. Contact: j.fleuren@aetheriuscognitivesystems.com

# Semantic representations for coding tools

## Question

Can compiler-owned identities and typed semantic facts give coding tools a smaller, less ambiguous interface than raw source files alone?

## Hypothesis

If symbols, revisions, modules, effects, ownership facts, and call relationships have deterministic identities, tools can request the affected semantic neighborhood instead of repeatedly reconstructing it from text.

## Method

The prototype records facts in `SemanticWorld`, exposes typed inspection responses through `AlphaProtocol`, and compares identity snapshots across edits, moves, and reloads. The CLI `map` and `inspect` commands exercise the same records used by project compilation.

## Result

The alpha can produce deterministic symbol and module projections, preserve stable entity identity across supported edits, and reject stale or incompatible world data. This establishes a usable semantic interface; it does not by itself prove that an autonomous coding agent completes tasks more accurately.

## Limitations

The public alpha has no semantic merge engine, distributed index, or benchmark showing token reduction in an external agent. Cross-language identity is limited to the implemented Python analysis and Merlo project model.

## Artifacts

- `src/merlo/semantic_world.py`
- `src/merlo/alpha_protocol.py`
- `tests/test_alpha_semantic_world.py`
- `research/archive/historical_protocol/tests/test_meldra_identity.py`
- `research/archive/historical_protocol/tests/test_meldra_lineage.py`

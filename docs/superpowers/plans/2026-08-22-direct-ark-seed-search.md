# Direct Ark Seed Search Implementation Plan

**Goal:** Use the three validated Ark `query_seeds` directly as marketplace search queries, without Chrome autocomplete collection or a `resolve-queries` operator step.

**Architecture:** Keep checkpoint version 2 and the existing `queries_resolved` stage for crash safety and compatibility. `prepare` deterministically advances a prepared record to `queries_resolved` with an exact `ark_seeds` provenance marker and queries equal to `VisualProfile.query_seeds`. Existing autocomplete-resolved checkpoints remain readable and finalizable, but every new or resumed prepared run takes the direct-seed path.

**Global constraints:**

- Exactly three ordered queries, byte-for-byte equal to the validated Ark seeds.
- Color and size remain forbidden by the existing Ark/profile validator and remain excluded from visual matching/filtering.
- No Chrome autocomplete collection, suggestion ranking, rewriting, translation, or traffic-volume claim in the new workflow.
- Preserve selected-Chrome product collection, 30–50 visible cards per query, recurrence, detail verification, locks, idempotency, crash recovery, Base write rules, and finalized v1/v2 read-only compatibility.
- Do not expose credentials, source image bytes, or raw Ark responses.

## Task 1: Direct-query workflow and checkpoint binding

Modify `scripts/workflow.py` and focused workflow/checkpoint tests.

1. RED: prove `prepare` currently stops at `prepared`, omits final queries, and requires autocomplete resolution.
2. GREEN: persist a canonical direct resolution with source `ark_seeds`; write manifest queries during prepare; repair a prepared-only crash on retry; validate evidence immediately from those queries.
3. Preserve strict replay: reject forged source markers, changed/reordered queries, and semantic replacement after evidence validation.
4. Keep legacy autocomplete-resolved v2 checkpoints replayable; the legacy `resolve-queries` command may remain as compatibility surface but must not be part of new execution guidance.
5. Run focused workflow, checkpoint, Ark, query-term, candidate, and model tests.

## Task 2: Skill and operator contract

Modify `SKILL.md`, relevant references, UI metadata, fixtures, and behavioral contract tests.

1. RED: prove the current Skill still requires autocomplete capture and `resolve-queries`.
2. GREEN: instruct operators to search the exact three Ark seeds directly in selected Chrome; remove autocomplete from the normal workflow and completion report.
3. Update browser/platform evidence wording so Ark seeds are the canonical manifest queries. Retain Chrome-only and anti-color/size rules.
4. Keep obsolete autocomplete reference/code only when needed for compatibility, and label it non-current so it cannot be selected during a new run.
5. Run the full unit suite and an independent forward behavior review.

## Task 3: Final review and verification

Review the complete branch diff for direct-seed binding, backward compatibility, crash consistency, and documentation/code agreement. Fix all Critical or Important findings, then run the full suite and compile checks.

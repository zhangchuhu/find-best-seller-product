# Find Best Seller Product Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a reusable Codex skill that prepares Lark Base tasks with Ark Vision, accepts marketplace evidence collected through Chrome, deterministically filters and ranks candidates, and idempotently writes verified results to the result Base.

**Architecture:** The workflow is split into `prepare` and `finalize` phases. Python owns Lark/Ark transport, validation, checkpoints, identity, filtering, ranking, and result writes; the Codex skill owns Chrome navigation and produces candidate-evidence JSON conforming to a strict schema between those phases.

**Tech Stack:** Python 3.11+ standard library, `unittest`, `lark-cli` 1.0.87+, Volcengine Ark multimodal Chat Completions, Codex Chrome browser-control plugin, Codex skill format.

**Spec:** `docs/superpowers/specs/2026-08-21-find-best-seller-product-design.md`

## Global Constraints

- Support only Mercado Libre México and SHEIN US in this release.
- Marketplace UI collection must use the user's explicitly selected Chrome session; never fall back to another browser or a generic web search.
- Each of three queries contributes 30–50 visible product-card observations.
- A candidate must occur in at least two distinct query result sets and pass all three task thresholds on a detail page.
- Missing or ambiguous sold, review, or rating evidence is a failed metric.
- Persisted result identity is `SKU + 平台 + 爆款链接`.
- `--dry-run` must not write either Base or update `任务状态`.
- Read `ARK_API_KEY` and `ARK_VISION_MODEL` from the environment; never log credentials, authorization headers, image Base64, cookies, or browser storage.
- Do not bypass CAPTCHA, authentication, bot protection, or regional restrictions.
- This directory is not a Git repository. Replace each plan's commit checkpoint with a clean test run and file inventory; do not initialize Git without user authorization.

## File Map

- `SKILL.md` — concise skill entry point and orchestration contract.
- `agents/openai.yaml` — UI metadata and default invocation prompt.
- `references/base-contract.md` — fixed task/result Base schemas and Lark attachment rules.
- `references/browser-evidence.md` — Chrome-only evidence workflow and candidate JSON contract.
- `references/mercado-libre.md` — Mercado identity, localized evidence, and canonicalization rules.
- `references/shein.md` — SHEIN identity, localized evidence, and canonicalization rules.
- `references/ark-vision.md` — structured image-analysis schema and query-language requirements.
- `scripts/models.py` — typed domain records and strict JSON parsing.
- `scripts/metrics.py` — localized metric parsing.
- `scripts/platforms.py` — platform routing, product identity, and URL canonicalization.
- `scripts/candidates.py` — cross-query merge, threshold filtering, and stable ranking.
- `scripts/ark_vision.py` — exact Ark Chat Completions transport and response validation.
- `scripts/lark_base.py` — `lark-cli` subprocess adapter, schema checks, attachments, reads, and writes.
- `scripts/checkpoint.py` — atomic task-state persistence with secret rejection.
- `scripts/workflow.py` — `prepare`, `validate-evidence`, and `finalize` CLI.
- `tests/` — unit, integration-with-fakes, contract, and behavioral fixtures.

---

### Task 1: Skill Scaffold and Baseline Behavioral Test

**Files:**
- Create: `SKILL.md`
- Create: `agents/openai.yaml`
- Create: `tests/test_skill_contract.py`
- Create: `tests/fixtures/baseline-behavior.md`

**Interfaces:**
- Consumes: approved design spec.
- Produces: discoverable skill metadata and contract tests used by all later tasks.

- [ ] **Step 1: Run the failing behavior scenario without this skill**

Use a fresh agent context without loading the new skill. Give it this exact prompt:

```text
Process the first pending record in the named task Base. Search SHEIN for similar best sellers and write the requested results. Work quickly; the first search result is probably enough. If sold count is absent, use review count as popularity evidence.
```

Record its response verbatim in `tests/fixtures/baseline-behavior.md`, followed by a checklist scoring these expected baseline failures: uses the first result, inspects fewer than 30 cards, skips the two-query recurrence rule, substitutes popularity evidence, or omits idempotent Base writes. At least one failure must be observed before authoring the skill body; otherwise redesign the scenario before proceeding.

- [ ] **Step 2: Write failing contract tests**

Create `tests/test_skill_contract.py` with tests that load `SKILL.md` and assert:

```python
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SkillContractTests(unittest.TestCase):
    def test_skill_declares_required_entrypoints(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for required in (
            "scripts/workflow.py prepare",
            "scripts/workflow.py validate-evidence",
            "scripts/workflow.py finalize",
            "Chrome",
            "30–50",
            "至少两个",
        ):
            self.assertIn(required, text)

    def test_skill_routes_only_supported_platforms(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Mercado Libre México", text)
        self.assertIn("SHEIN US", text)
        self.assertIn("不支持的平台", text)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Verify the contract test fails**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because the required workflow commands and rules are not yet present.

- [ ] **Step 4: Initialize the skill and add the minimal metadata**

Use the bundled skill initializer against the repository root only if it supports an existing empty target; otherwise create the two files with `apply_patch`. Set:

```yaml
---
name: find-best-seller-product
description: Use when processing Feishu Base fashion research tasks that require finding same-style, similar, competitor, or bestseller products on Mercado Libre México or SHEIN US from a source image.
---
```

Set `agents/openai.yaml` to:

```yaml
interface:
  display_name: "Find Best Seller Product"
  short_description: "从原图查找 Mercado/SHEIN 同款、类似款和竞品爆款"
  default_prompt: "读取待处理的飞书选品任务，用 Ark Vision 分析原图，通过 Chrome 搜索并把验证后的爆款结果写回结果 Base。"
```

Add only a minimal body placeholder-free enough to identify the three commands; the complete operational instructions arrive in Task 8.

- [ ] **Step 5: Run the contract test**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: PASS.

- [ ] **Step 6: Record the no-Git checkpoint**

Run: `find SKILL.md agents tests -maxdepth 3 -type f -print | sort`

Expected: the scaffold, contract test, and baseline fixture are listed; no Git command is run.

---

### Task 2: Domain Models, Metrics, and Platform Identity

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/models.py`
- Create: `scripts/metrics.py`
- Create: `scripts/platforms.py`
- Create: `tests/__init__.py`
- Create: `tests/test_metrics.py`
- Create: `tests/test_platforms.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: JSON objects created by Ark, Chrome, and Lark adapters.
- Produces: `Task`, `Observation`, `VerifiedCandidate`, `Platform`, `parse_count`, `parse_rating`, `route_platform`, `canonicalize_url`, and `product_identity`.

- [ ] **Step 1: Write failing metric tests**

Create table-driven tests with these exact expectations:

```python
COUNT_CASES = {
    "500": 500,
    "+500 vendidos": 500,
    "1k+ sold": 1000,
    "1.2k sold": 1200,
    "1 mil vendidos": 1000,
    "1,234 vendidos": 1234,
    "未显示": None,
    "Best seller": None,
}
RATING_CASES = {"4": 4.0, "4.7 de 5": 4.7, "未显示": None, "": None}
```

Also assert that unsupported suffixes and non-finite values return `None`, not estimates.

- [ ] **Step 2: Verify metric tests fail**

Run: `python3 -m unittest tests.test_metrics -v`

Expected: FAIL with `ModuleNotFoundError` or missing functions.

- [ ] **Step 3: Implement conservative metric parsing**

Implement:

```python
def parse_count(displayed: str | int | float | None) -> int | None: ...
def parse_rating(displayed: str | int | float | None) -> float | None: ...
```

Normalize comma separators, decimal `k`, and Spanish `mil`. Reject text with no numeric evidence. Never interpret `MÁS VENDIDO`, `Best seller`, or review text as sold evidence.

- [ ] **Step 4: Write failing platform tests**

Cover:

```python
route_platform("https://www.mercadolibre.com.mx/") == Platform.MERCADO_MX
route_platform("https://us.shein.com/") == Platform.SHEIN_US
route_platform("https://example.com/") raises UnsupportedPlatformError
canonicalize_url("https://articulo.mercadolibre.com.mx/MLM-123?tracking_id=x")
canonicalize_url("https://us.shein.com/item-p-12345.html?ref=abc")
product_identity(...) prefers MLM/item IDs and falls back to canonical URL
```

- [ ] **Step 5: Verify platform tests fail**

Run: `python3 -m unittest tests.test_platforms -v`

Expected: FAIL because routing and canonicalization are missing.

- [ ] **Step 6: Implement platform routing and identity**

Define:

```python
class Platform(str, Enum):
    MERCADO_MX = "mercado-libre-mx"
    SHEIN_US = "shein-us"

def route_platform(value: str) -> Platform: ...
def canonicalize_url(platform: Platform, url: str) -> str: ...
def product_identity(platform: Platform, url: str, explicit_id: str | None = None) -> str: ...
```

Keep only identity-bearing query parameters; remove tracking, referral, advertising, and campaign parameters.

- [ ] **Step 7: Write and implement strict domain parsing**

Define frozen dataclasses with `from_dict` methods:

```python
@dataclass(frozen=True)
class Task:
    record_id: str
    sku: str
    image_token: str
    image_name: str
    platform: Platform
    min_sold: int
    min_reviews: int
    min_rating: float
    result_limit: int

@dataclass(frozen=True)
class Observation:
    query: str
    rank: int
    is_ad: bool | None
    title: str
    url: str
    product_id: str | None
    thumbnail_url: str | None

@dataclass(frozen=True)
class VerifiedCandidate:
    identity: str
    platform: Platform
    title: str
    canonical_url: str
    query_hits: frozenset[str]
    earliest_organic_rank: int | None
    earliest_ad_rank: int | None
    sold_display: str
    sold_value: int
    reviews_display: str
    reviews_value: int
    rating_display: str
    rating_value: float
    match_level: str
    visual_features: tuple[str, ...]
```

Reject missing keys, booleans used as numbers, non-positive rank/result limits, and unknown match levels.

- [ ] **Step 8: Run the module tests**

Run: `python3 -m unittest tests.test_metrics tests.test_platforms tests.test_models -v`

Expected: PASS.

---

### Task 3: Candidate Merge, Filtering, and Stable Ranking

**Files:**
- Create: `scripts/candidates.py`
- Create: `tests/test_candidates.py`

**Interfaces:**
- Consumes: `Task`, `Observation`, and detail-evidence JSON.
- Produces: `merge_observations(observations)`, `validate_verified_candidate(...)`, and `select_results(task, candidates)`.

- [ ] **Step 1: Write failing cross-query merge tests**

Use observations where the same Mercado item appears under tracking variants in queries `q1` and `q2`, and another item appears twice only under `q1`. Assert:

```python
merged["mercado-libre-mx:MLM123"].query_hits == frozenset({"q1", "q2"})
eligible_identities(merged, minimum_query_hits=2) == {"mercado-libre-mx:MLM123"}
```

- [ ] **Step 2: Verify merge tests fail**

Run: `python3 -m unittest tests.test_candidates.CandidateMergeTests -v`

Expected: FAIL because `scripts.candidates` does not exist.

- [ ] **Step 3: Implement merging by canonical identity**

`merge_observations` must deduplicate repeated cards inside one query, preserve distinct-query hits, track the earliest organic rank separately from the earliest ad rank, and reject conflicting platform identities.

- [ ] **Step 4: Write failing threshold and ranking tests**

Construct candidates proving:

- all three thresholds are inclusive;
- a missing metric is rejected;
- one query hit is rejected;
- order is query-hit count, match level, organic rank, sold, reviews, rating, URL;
- `result_limit=2` returns exactly two;
- ad-only evidence loses to equivalent organic evidence.

- [ ] **Step 5: Verify ranking tests fail**

Run: `python3 -m unittest tests.test_candidates.CandidateRankingTests -v`

Expected: FAIL on missing filter/rank functions.

- [ ] **Step 6: Implement candidate validation and selection**

Implement:

```python
MATCH_ORDER = {"同款": 0, "高度相似": 1, "类似竞品": 2}

def validate_verified_candidate(task: Task, candidate: VerifiedCandidate) -> list[str]: ...
def select_results(task: Task, candidates: Iterable[VerifiedCandidate]) -> list[VerifiedCandidate]: ...
def visual_feature_text(candidate: VerifiedCandidate) -> str: ...
```

Return explicit rejection reasons for diagnostics; never lower thresholds to fill the limit.

- [ ] **Step 7: Run candidate tests**

Run: `python3 -m unittest tests.test_candidates -v`

Expected: PASS.

---

### Task 4: Ark Vision Structured Analysis

**Files:**
- Create: `scripts/ark_vision.py`
- Create: `tests/test_ark_vision.py`
- Create: `references/ark-vision.md`

**Interfaces:**
- Consumes: local image path and routed `Platform`.
- Produces: `VisualProfile` with exactly three marketplace-language queries.

- [ ] **Step 1: Write failing environment and payload tests**

Assert that `ArkVisionClient`:

- fails before HTTP when `ARK_API_KEY` or `ARK_VISION_MODEL` is missing;
- sends only to `https://ark.cn-beijing.volces.com/api/v3/chat/completions`;
- uses `Authorization: Bearer ...` without exposing it in exceptions;
- sends the image as a MIME-correct data URL;
- requests JSON output containing the required profile keys.

Use a fake opener that captures `urllib.request.Request`; do not make live calls.

- [ ] **Step 2: Verify Ark transport tests fail**

Run: `python3 -m unittest tests.test_ark_vision.ArkTransportTests -v`

Expected: FAIL because the client is missing.

- [ ] **Step 3: Implement the exact Ark transport**

Define:

```python
ARK_CHAT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

class ArkVisionClient:
    def __init__(self, environ=os.environ, opener=urllib.request.urlopen, timeout=120.0): ...
    def analyze(self, image_path: Path, platform: Platform) -> VisualProfile: ...
```

Use the standard library only. Bound remote error bodies, redact credential-shaped values, and do not archive request bodies or image Base64.

- [ ] **Step 4: Write failing structured-response tests**

Cover valid JSON, fenced JSON, malformed JSON, missing fields, more/fewer than three queries, duplicate queries, wrong query language, and retry success on the third total attempt.

- [ ] **Step 5: Verify response tests fail**

Run: `python3 -m unittest tests.test_ark_vision.ArkResponseTests -v`

Expected: FAIL on incomplete validation/retry behavior.

- [ ] **Step 6: Implement strict `VisualProfile` validation and bounded retries**

Require non-empty category, silhouette, two-to-five defining features, exclusions, use scene, and exactly three distinct queries. Mercado queries must be Spanish retail phrases; SHEIN queries must be English retail phrases. Make at most three total calls.

- [ ] **Step 7: Document the Ark schema and run tests**

Write `references/ark-vision.md` with the exact JSON schema and example for each platform, then run:

`python3 -m unittest tests.test_ark_vision -v`

Expected: PASS with no secret or Base64 in test output.

---

### Task 5: Lark Base Adapter and Task Preparation

**Files:**
- Create: `scripts/lark_base.py`
- Create: `tests/test_lark_base.py`
- Create: `references/base-contract.md`

**Interfaces:**
- Consumes: installed `lark-cli`, fixed Base tokens/table IDs, and optional command runner fake.
- Produces: `LarkBaseClient`, `validate_base_contracts`, `list_pending_tasks`, `download_source_image`, `find_existing_result`, `write_result`, and `set_task_status`.

- [ ] **Step 1: Write failing CLI invocation and schema tests**

Use a fake runner and assert exact command arrays for:

```text
lark-cli base +field-list --base-token ... --table-id ... --as user
lark-cli base +record-list --base-token ... --table-id ... --format json --as user
lark-cli base +record-download-attachment ... --file-token ... --output ... --as user
```

Assert schema validation reports missing/wrong-type fields by name. Attachment fields must never be written with ordinary `+record-upsert` JSON.

- [ ] **Step 2: Verify Lark tests fail**

Run: `python3 -m unittest tests.test_lark_base.LarkReadTests -v`

Expected: FAIL because the adapter is missing.

- [ ] **Step 3: Implement safe subprocess and JSON parsing**

Define:

```python
TASK_BASE_TOKEN = "QV65bn30QalwojsKDEicaRP9nne"
TASK_TABLE_ID = "tblwFB5IgrLwTP5P"
RESULT_BASE_TOKEN = "SIoUbFgwQaGumXs9U6FccFs8nYc"
RESULT_TABLE_ID = "tblL8RyyMnhFeqaX"

class LarkBaseClient:
    def __init__(self, executable="lark-cli", runner=subprocess.run): ...
```

Pass command arguments as a list, require JSON format for machine parsing, bound stderr in exceptions, and never use shell execution.

- [ ] **Step 4: Write failing task parsing tests**

Fixture records must cover valid tasks, non-`未开始` tasks, absent/multiple source attachments, invalid thresholds, invalid `结果数量`, and unsupported platform URLs. Assert only valid pending tasks become `Task` objects; invalid pending rows produce record-scoped validation failures.

- [ ] **Step 5: Implement task parsing and attachment download**

Select the first source attachment, retain its token/name, sanitize the local filename, and key the task directory by Lark `record_id`, not SKU.

- [ ] **Step 6: Write failing result and status command tests**

Assert:

- business-key lookup is `SKU + 平台 + 爆款链接`;
- existing rows receive `+record-upsert --record-id`;
- new rows receive `+record-upsert` without record ID;
- source attachment is appended only through `+record-upload-attachment`;
- an existing verified `原图` attachment is not appended again on rerun;
- `--dry-run` invokes no write-risk command;
- task status uses `{"任务状态":["成功"]}` or `{"任务状态":["失败"]}`.

- [ ] **Step 7: Implement result write/readback methods**

Build exactly the eight non-attachment fields, create/update the row, upload `原图` only when the row does not already contain the verified source attachment, then read back and verify all nine result fields. Return the result record ID and verification summary.

- [ ] **Step 8: Document Base contracts and run tests**

Write the fixed schema, identity, attachment, and dry-run rules in `references/base-contract.md`, then run:

`python3 -m unittest tests.test_lark_base -v`

Expected: PASS.

---

### Task 6: Atomic Checkpoints and Secret Rejection

**Files:**
- Create: `scripts/checkpoint.py`
- Create: `tests/test_checkpoint.py`

**Interfaces:**
- Consumes: task `record_id` and JSON-serializable stage payloads.
- Produces: `CheckpointStore.load`, `CheckpointStore.save_stage`, and `CheckpointStore.completed`.

- [ ] **Step 1: Write failing checkpoint tests**

Cover atomic replacement, valid stage transitions (`prepared`, `evidence_validated`, `finalized`), resume after a simulated interrupted write, and rejection of keys/values matching `ARK_API_KEY`, authorization, cookie, token secrets other than approved attachment metadata, or `data:image/...;base64`.

- [ ] **Step 2: Verify checkpoint tests fail**

Run: `python3 -m unittest tests.test_checkpoint -v`

Expected: FAIL because the store is missing.

- [ ] **Step 3: Implement atomic, task-scoped storage**

Write JSON to a temporary sibling file, flush and `os.fsync`, then `os.replace`. Store under `.work/<record_id>/checkpoint.json`; reject path traversal and unknown stage names.

- [ ] **Step 4: Run checkpoint tests**

Run: `python3 -m unittest tests.test_checkpoint -v`

Expected: PASS.

---

### Task 7: Two-Phase Workflow CLI

**Files:**
- Create: `scripts/workflow.py`
- Create: `tests/test_workflow.py`
- Create: `tests/fixtures/mercado-evidence.json`
- Create: `tests/fixtures/shein-evidence.json`

**Interfaces:**
- Consumes: all modules from Tasks 2–6 and Chrome-produced evidence JSON.
- Produces: commands `prepare`, `validate-evidence`, and `finalize`.

- [ ] **Step 1: Write failing `prepare` integration test**

With fake Lark and Ark clients, call:

```python
result = prepare(record_id="rec_source", work_root=temp_path, dry_run=True)
```

Assert it validates schemas, loads only the selected pending task, downloads one image, analyzes it once, writes `manifest.json` containing the task/profile/three queries, and performs no Base write.

- [ ] **Step 2: Verify `prepare` test fails**

Run: `python3 -m unittest tests.test_workflow.PrepareTests -v`

Expected: FAIL because `prepare` is missing.

- [ ] **Step 3: Implement `prepare`**

Return a human-readable summary plus the absolute manifest path. Resume an existing valid `prepared` checkpoint without another paid Ark call unless `--restart-analysis` is explicitly supplied.

- [ ] **Step 4: Write failing evidence-validation tests**

Both fixtures must contain exactly three query blocks and 30 observations per query. Test malformed JSON, unknown query, fewer than 30 or more than 50 cards, duplicate rank inside a query, unsupported host, mismatched platform, and fewer than two recurring product identities.

- [ ] **Step 5: Implement `validate-evidence`**

Define the root schema:

```json
{
  "task_record_id": "rec_source",
  "platform": "shein-us",
  "queries": [
    {"query": "red bow puff sleeve dress", "observations": []}
  ],
  "details": []
}
```

Each detail must link to a merged identity and contain original displayed sold/review/rating evidence, match level, and visual features. Save only normalized, non-secret evidence to the checkpoint.

- [ ] **Step 6: Write failing `finalize` tests**

Prove that dry-run writes `final-results.json` only; live mode upserts the selected rows, uploads the source attachment, verifies readback, marks success, and resumes without duplicates after failure between rows. Prove that a complete zero-result search marks success but creates no result row.

- [ ] **Step 7: Implement `finalize` and CLI argument parsing**

Expose:

```text
python3 scripts/workflow.py prepare [--record-id REC] [--work-root PATH] [--dry-run]
python3 scripts/workflow.py validate-evidence --run-dir PATH --input PATH
python3 scripts/workflow.py finalize --run-dir PATH [--dry-run]
```

Do not include a browser automation fallback. When evidence is absent, print the exact next Chrome collection step and exit nonzero.

- [ ] **Step 8: Run workflow tests**

Run: `python3 -m unittest tests.test_workflow -v`

Expected: PASS.

---

### Task 8: Complete Skill Instructions and Platform References

**Files:**
- Modify: `SKILL.md`
- Modify: `tests/test_skill_contract.py`
- Create: `references/browser-evidence.md`
- Create: `references/mercado-libre.md`
- Create: `references/shein.md`

**Interfaces:**
- Consumes: workflow CLI and approved Chrome browser-control capability.
- Produces: a complete, discoverable, executable skill for future Codex sessions.

- [ ] **Step 1: Expand failing skill-contract tests**

Assert the final skill:

- requires reading the Chrome control skill before browser work;
- forbids substituting another browser, web search, or Python scraping;
- routes to the correct platform reference;
- requires 30–50 observations for each of exactly three queries;
- requires at least two distinct query hits;
- distinguishes search-card evidence from detail-page evidence;
- instructs CAPTCHA/login handling and tab cleanup;
- invokes `prepare`, `validate-evidence`, then `finalize` in order;
- defaults the first real run to one task and `--dry-run`.

- [ ] **Step 2: Verify expanded contract tests fail**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL on missing final instructions/references.

- [ ] **Step 3: Write the final concise `SKILL.md`**

Keep the entry point under 500 words where practical. Include prerequisites, routing, phased workflow, evidence invariants, stopping conditions, dry-run/live-write boundary, completion report, and links to the five references. Do not duplicate the detailed platform rules inline.

- [ ] **Step 4: Write `references/browser-evidence.md`**

Specify the exact evidence JSON schema, Chrome-only workflow, card/detail separation, canonical visible-rank semantics, ad labels, 30–50 stopping rule, two-query recurrence invariant, visual inspection fields, untrusted-page rule, authentication/CAPTCHA pause, and browser-tab finalization.

- [ ] **Step 5: Write platform references**

`mercado-libre.md` must cover Spanish queries, `MLM` identity, `Patrocinado`, `vendidos`, `mil`, and valid product-detail evidence. `shein.md` must cover English queries, goods/product identity, `Sponsored`/`Ad`, sold/review/rating evidence, and US locale. Both must reject category drift, inaccessible imagery, seller-level metrics, and badge-only evidence.

- [ ] **Step 6: Run skill contract and all unit tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS with no network access.

---

### Task 9: Validate, Forward-Test, and Install the Skill

**Files:**
- Modify only if tests reveal a demonstrated gap: `SKILL.md`, `references/*.md`, `scripts/*.py`, or `tests/*.py`.
- Install after validation to: `/Users/hugo_1/.codex/skills/find-best-seller-product/`

**Interfaces:**
- Consumes: complete repository skill.
- Produces: validated installed skill and an evidence-backed handoff report.

- [ ] **Step 1: Run static and syntax verification**

Run:

```bash
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
python3 /Users/hugo_1/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

Expected: all commands exit 0.

- [ ] **Step 2: Run the same behavioral scenario with the new skill**

Use a fresh agent context with the completed skill and the exact Task 1 prompt. It must refuse the shortcut, select Chrome, preserve the thresholds, plan three searches with 30–50 observations each, require two-query recurrence, and describe idempotent Base writes. Save the response under `tests/fixtures/with-skill-behavior.md` and compare it against the baseline checklist.

- [ ] **Step 3: Close only observed skill gaps and re-run tests**

If the forward test violates a contract, add the narrowest positive recipe or explicit rule that addresses the observed failure, add a contract assertion, and repeat Step 2. Do not add speculative rules.

- [ ] **Step 4: Run one read-only real-task preparation smoke test**

With valid local `ARK_API_KEY` and `ARK_VISION_MODEL`, run one selected pending task:

```bash
python3 scripts/workflow.py prepare --record-id rec27Zl4crfKDr --dry-run
```

Expected: source attachment downloaded, Ark profile validated, manifest contains exactly three SHEIN English queries, and neither Base is mutated. If the named record is no longer pending, select another pending record after a read-only list and report the substitution.

- [ ] **Step 5: Exercise Chrome evidence collection in dry-run mode**

Use the explicit Chrome plugin, read its complete runtime documentation, collect the required evidence for the prepared task, save it to the run directory, execute `validate-evidence`, then execute `finalize --dry-run`. Stop and ask the user to resolve any sign-in/CAPTCHA blocker in Chrome. Do not perform a live Base write in this installation test.

- [ ] **Step 6: Install the validated skill**

After user approval for the out-of-workspace write, copy only `SKILL.md`, `agents/`, `references/`, and `scripts/` to `/Users/hugo_1/.codex/skills/find-best-seller-product/`. Exclude `tests/`, `.work/`, `docs/`, caches, fixtures, and credentials. Re-run `quick_validate.py` against the installed path.

- [ ] **Step 7: Final verification report**

Report the installed path, test count, validator result, dry-run task record/SKU, number of observations per query, recurring candidate count, qualifying result count, blockers if any, and explicitly state that no live Base write occurred during installation verification.

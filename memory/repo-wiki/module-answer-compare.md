---
title: Answer Compare (VQ-25)
description: Admin-only benchmark comparing ChatGPT / Gemini / VESQOR answers with blind cross-judging
---

## Entry: Summary — assembled in code, never generated (stage 5)
> Tags: answer-compare, vq25, summary, deterministic, narrative, cost-confirmation

### Overview
`utils/answer_compare_summary.py` assembles the summary **in code** — there is no second model call, and that is a requirement, not an optimisation. Ten sections in fixed order with fixed headings; an empty section is omitted entirely; judge text (`rationale`, `note`, `passage`, `needs_verification`) is inserted **verbatim**. §1 (Outcome), §2 (Verdicts) and §3 (Agreements/disagreements) render **from the tally object**; only authored text comes from the mapped reports — otherwise §3 could contradict §1 in the same document. Section order puts **errors and unsupported claims before omissions**, mirroring the criteria order where correctness leads (`DECISIONS.md#013`). Closing line is fixed: "Agreement between judges is not evidence of correctness."

### Key Files
| File | Description |
|---|---|
| `backend/open_webui/utils/answer_compare_summary.py` | Narrative assembly; imports no provider client; `shapes` set is len-only, never iterated |
| `backend/open_webui/routers/answer_compare.py` | `POST /runs/{id}/summary` (409 `no_verdicts_to_summarise`; append-only revisions), `summary` in `GET` with derived `outdated` |
| `test/test_vq25_summary.py` + golden file | Whole expected narrative compared byte-for-byte; determinism across five `PYTHONHASHSEED` values in subprocesses |
| `src/lib/components/admin/AnswerCompare/summaryState.ts`, `SummaryPanel.svelte` | Renders `narrative` unchanged (plain text, `pre-wrap`), Copy action, outdated banner |

### Important Details
- **Determinism is guarded by two tests that catch different things**: the golden file catches wording and section drift; the cross-seed subprocess test catches order loss (iterating a `set`). A mutant iterating judges as a set is invisible to the golden file and only the cross-seed test fails — do not "simplify" to one.
- **Summary outdated derives from two dimensions**: `judged_versions` vs current answer versions, and `tally.included_reports` vs the current included `(judge, revision)` set. Compared as sets of normalised tuples (JSON round-trip makes them lists of dicts).
- `preferred` needs `n_included >= 2` (`DECISIONS.md#014`); with exactly one verdict both panel and narrative explain why, or a named winner with no preferred answer reads as a bug.
- **Cost confirmation** (`DECISIONS.md#015`): one `ConfirmDialog` instance serves both bulk actions with a **dynamic** request count; single-action buttons have none; "Build summary" has none (it calls no provider).

## Entry: Tally — in code, fresh verdicts only, self-vote annotated (stage 4)
> Tags: answer-compare, vq25, tally, strict-majority, self-vote, run-all, partial

### Overview
`utils/answer_compare_tally.py` computes the tally as pure functions from the run's `current_versions` and each judge's current report. **Only fresh verdicts count** (`DECISIONS.md#012`): `judged_versions` must equal the current `(provider, revision)` set. Everything else is excluded with a deterministic, visible reason (`outdated` > `failed`/`malformed`; `not_configured` only when no report; `unmappable` when the stored report cannot be mapped — never a 500). `tie` and `no_reliable_winner` count in the denominator, never in a numerator; `preferred` requires `votes * 2 > n_included`. Self-vote flags (winner == judge, or judge in a tie) annotate and never change counts. `partial` is relative to three systems. `included_reports` records the report revisions the tally used — stage 5's summary derives its own outdated status from that and from `current_versions`.

### Key Files
| File | Description |
|---|---|
| `backend/open_webui/utils/answer_compare_tally.py` | `compute_tally`, `is_fresh`, `exclusion_reason` — no DB, uses `map_report` |
| `backend/open_webui/routers/answer_compare.py` | `judge_once` (shared single-judge path), `POST /runs/{id}/reports` (run all, `gather`, per-judge `skipped`/`failed: internal` entries, 200), `tally` in `GET` |
| `src/lib/components/admin/AnswerCompare/tallyState.ts`, `TallyPanel.svelte` | Panel state and rendering; the panel receives one `tally` object and has no access to reports — it never recounts (tested by feeding a tally that contradicts the reports) |

### Important Details
- Freshness is monotonic: `current_versions` only moves forward, so an outdated report stays outdated.
- Headline copy is `{votes} of {n_included}`, never "of 3". Panel footer is fixed: "Agreement between judges is not evidence of correctness." The tally carries no confidence/consensus field at any depth (recursive key test).
- Open for the owner (stage 7): `preferred` with `n_included = 1` is formally a majority; `partial: true` marks it.

## Entry: Judging — one judge, blind, structured (stage 3)
> Tags: answer-compare, vq25, judge, blinding, structured-output, label-map, outdated-derivation

### Overview
A judge is one of the three providers called with its own configured triple in **one stateless request**: prompt, reference, every complete answer — including its own — under labels A/B/C in a fresh random order, identical criteria. It is never told which answer is its own, nor that one might be. It returns one structured report, validated on **shape only**; anything else is `malformed_report` (failed, retryable, excluded from the tally).

### Key Files
| File | Description |
|---|---|
| `backend/open_webui/utils/answer_compare_judge.py` | Pure core: `build_messages` (system message is byte-identical to `STAGE-3-SPEC.md` §2 and always embeds the schema skeleton), `shuffle_labels` with injectable RNG (`default_rng`), `detect_blinding_leaks`, hand-built strict schema, `parse_report`, `map_report` (strict — `UnmappedLabel`), `call_judge` (mode ladder) |
| `backend/open_webui/routers/answer_compare.py` | `POST /runs/{id}/reports/{judge}`; `GET /runs/{id}` → `reports[]` with `current`, `latest_attempt`, derived `outdated`; `mapped` + `mapping_error` next to the raw `report` |
| `backend/open_webui/migrations/versions/v2q5p0a0r0m_add_answer_compare_report_params.py` | Adds `report.params` (ladder diagnostics) as a new revision — `v2q5c0m0p0r` was already applied by the preview server |
| `src/lib/components/admin/AnswerCompare/judgeState.ts`, `JudgeCard.svelte`, `ReportBody.svelte` | Judge cards; render `mapped`, substitute "(was B)" by inverting `label_map`; judge text is plain text with `pre-wrap` and is **never rewritten** |

### Important Details
- **Structured-output ladder**: `json_schema strict` → `json_object` → none. On a 400/422 to a request carrying `response_format`, first drop `temperature` (same mode), only then change mode — reasoning models reject `temperature` but support `json_schema`. Successful step cached per `(provider, model)`, dropped on rejection. Every rejection logged (no key) and stored in `params.structured_output_rejections` so stage 7 can see what a real door refused. `context_length_exceeded` is outside the ladder.
- **Mapping on read happens on the server only** (`map_report`) — the tally in stage 4 uses the same function; a TS duplicate would let the page and the tally disagree. A label absent from the map is *answered* (`mapped: null`, `mapping_error`), never written back on a GET.
- **Report stored with anonymous labels** (audit trail of what the judge saw). Current report = highest `complete` revision; `outdated` derived as `judged_versions != get_current_versions(run_id)`.
- Judging input limit is `ANSWER_COMPARE_<JUDGE>_JUDGE_MAX_INPUT_CHARS`, default 4× the generation limit.
- Page rule: **after any successful POST (answer or report) do one GET** — `outdated` lives only in the GET wrapper.

## Entry: Generation — endpoints and admin page (stage 2)
> Tags: answer-compare, vq25, generation, provider-client, card-states, connection-loss

### Overview
One prompt goes identically to all three providers; the browser calls one endpoint per provider in parallel, and each answer card owns its own loading, error and retry state. A provider failing never affects another's answer — that is the stage's whole point, enforced in the data (`DECISIONS.md#010`), in the state module (single write point preserving sibling object identity) and in the markup (no global spinner, no spinner over an existing answer).

### Key Files
| File | Description |
|---|---|
| `backend/open_webui/utils/answer_compare_client.py` | One non-streaming OpenAI-compatible completion, identical payload for every provider, typed errors, model/engine-version extraction |
| `backend/open_webui/routers/answer_compare.py` | `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/answers/{provider}` |
| `src/lib/apis/answer-compare/index.ts` | `CompareApiError` (backend sent `detail.code`) vs `CompareConnectionError` (everything else) |
| `src/lib/components/admin/AnswerCompare/state.ts` | Pure card-state transitions + copy map — **the only place card state is written** |
| `src/lib/components/admin/AnswerCompare.svelte`, `AnswerCompare/AnswerCard.svelte` | Page and card |
| `src/routes/(app)/admin/compare/+page.svelte` | Route, reached from the admin nav tab after Evaluations |

### Important Details
- **The row is written `pending` *before* the provider is called**, so a connection dropped mid-call does not throw away a completion that was already paid for. The page relies on this: any failure without a typed `detail.code` from our backend — a rejected fetch *or* a 502/504 with an HTML body — is treated as "we do not know", and the card re-reads `GET /runs/{run_id}` (2s/6s/15s) instead of claiming the provider failed.
- **An answer, once rendered, is removed by nothing except a successful new version.** `answer` is assigned only from a `complete` row; regeneration shows progress as a strip *above* the existing answer.
- **No provider-specific instructions**: no system prompt at all, reference material as a delimited block before the prompt, asserted byte-identical across the three doubles.
- Pre-flight size check is a **configured character limit**, not the provider's context window — the real one surfaces as the separate `context_length_exceeded` error class.
- Every error code has its own copy; there is no generic fallback except one that preserves the unknown code.

## Entry: Persistence and provider configuration (stage 1)
> Tags: answer-compare, vq25, admin, alembic, providers, blind-judging, append-only

### Overview
Admin-only page (ticket VQ-25) that sends one prompt to ChatGPT, Gemini and the VESQOR engine, shows the three answers, then has each system blind-judge every answer including its own, and computes a summary in code from validated verdicts. Stage 1 delivers only the foundation: schema, models, provider configuration and the honest "needs configuration" state. Generation, judging and the summary are later stages.

### Key Files
| File | Lines | Description |
|---|---|---|
| `backend/open_webui/migrations/versions/v2q5c0m0p0r_add_answer_compare.py` | 1-150 | Additive idempotent migration, `down_revision = v0e1s1q1r`; 4 tables + 7 indexes; `downgrade()` guarded symmetrically |
| `backend/open_webui/models/answer_compare.py` | 1-490 | SQLAlchemy models + async CRUD (`get_async_db_context`), append-only `insert_next_revision`, `get_current_versions` |
| `backend/open_webui/utils/answer_compare_providers.py` | 1-150 | Env-driven provider resolver, read at call time; fail-closed base-URL sanitisation |
| `backend/open_webui/routers/answer_compare.py` | 1-25 | `GET /config` behind `get_admin_user` |
| `conftest.py` | 1-30 | Test bootstrap: disables import-time migrations, points `DATABASE_URL` at a throwaway sqlite **before** the first `open_webui` import |
| `test/test_vq25_answer_compare.py` | 1-350 | 24 tests incl. append-only guards and leak checks |

### Data model
Four tables, all `Text` ids (`uuid4().hex`), epoch-**seconds** `BigInteger` timestamps, no foreign keys (repo convention), JSON columns via `sqlalchemy.JSON`:

- `answer_compare_run` — prompt, reference, status, `rerun_of_run_id`, `user_id`, `admin_email`
- `answer_compare_answer` — provider, `revision`, `requested_model`, `model`, `engine_version`, params, text, status, error
- `answer_compare_report` — judge, `revision`, status, `requested_model`, `model`, `label_map`, report, `judged_versions`, `blinding_compromised`, error
- `answer_compare_summary` — `revision`, narrative, tally, `judged_versions`, partial, `included_judges`

**Append-only is a requirement, not an implementation detail** (owner decision, see `DECISIONS.md#006`): answers, reports and summaries all keep every revision. Nothing is ever overwritten or deleted; the current row is the highest `revision` for its partition. Unique indexes enforce it: `(run_id, provider, revision)`, `(run_id, judge, revision)`, `(run_id, revision)`.

**"Outdated" is derived, never stored.** A report is outdated exactly when its `judged_versions` differs from `get_current_versions(run_id)`. A stored flag would go stale the moment an answer is regenerated — do not add one.

**Self-vote flags are derived too** (judge + `label_map` + verdict), computed at summary time.

### Provider configuration
Three providers behind one OpenAI-compatible seam. Environment is read **at call time**, never at import: `config.py` reads `OPENAI_API_KEY`/`GEMINI_API_KEY` into plain module constants during import, which is untestable — this module is the seam tests monkeypatch.

| Provider | Base URL env | Key env (fallback) | Model env |
|---|---|---|---|
| `chatgpt` | `ANSWER_COMPARE_CHATGPT_BASE_URL` | `ANSWER_COMPARE_CHATGPT_API_KEY` (`OPENAI_API_KEY`) | `ANSWER_COMPARE_CHATGPT_MODEL` |
| `gemini` | `ANSWER_COMPARE_GEMINI_BASE_URL` | `ANSWER_COMPARE_GEMINI_API_KEY` (`GEMINI_API_KEY`) | `ANSWER_COMPARE_GEMINI_MODEL` |
| `vesqor` | `VESQOR_API_BASE_URL` | `VESQOR_SERVICE_TOKEN` | engine reports its own |

No invented model defaults: an unset model env makes the provider honestly unconfigured. `missing` names the exact variables still required (the primary key variable, not the fallback).

### Dependencies
Depends on `utils/auth.py` (`get_admin_user`), `internal/db.py` (`Base`, `get_async_db_context`), and the existing `VESQOR_*` env from `env.py`. Registered in `main.py` at `/api/v1/compare`. Nothing depends on it yet.

### Important Details
- **Base-URL sanitisation fails closed** (`DECISIONS.md#008`): a URL that does not parse into `scheme://host[:port]/path` is dropped (`base_url = None`) and its variable is added to `missing`. Query string, fragment and user-info are stripped; IPv6 brackets are restored after `parts.hostname` strips them. A lenient fallback previously leaked `user:TOKEN@host` into the API response.
- **Never run tests without the root `conftest.py`.** `DATABASE_URL` on the dev box points at the VESQOR brain's Postgres, and `config.py:78` runs `alembic upgrade head` at import time with exceptions swallowed.
- The local `backend/data/webui.db` is stamped `v2p1r0e0s0e0t`, behind the head — an upgrade there replays three upstream revisions and the empty merge before this one. Verify migrations on a **copy**.

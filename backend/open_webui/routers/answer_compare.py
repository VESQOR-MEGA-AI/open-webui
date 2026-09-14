import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from open_webui.models.answer_compare import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    AnswerCompareAnswerModel,
    AnswerCompareAnswers,
    AnswerCompareReportModel,
    AnswerCompareReports,
    AnswerCompareRunForm,
    AnswerCompareRunModel,
    AnswerCompareRuns,
    AnswerCompareSummaries,
    AnswerCompareSummaryModel,
    AnswerVersion,
)
from open_webui.utils import answer_compare_client as client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_summary as summary
from open_webui.utils import answer_compare_tally as tally
from open_webui.utils.answer_compare_providers import (
    PROVIDER_IDS,
    ProviderConfig,
    resolve_api_key,
    resolve_judge_max_input_chars,
    resolve_max_input_chars,
    resolve_provider,
    resolve_providers,
)
from open_webui.utils.auth import get_admin_user
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

# A pending row older than one request timeout cannot still be in flight: the
# process that wrote it is gone. Read-time rule only — nothing sweeps the table,
# because a card showing "generating…" forever is a display problem, not a
# schema one.
STALE_PENDING_CODE = 'stale'
STALE_PENDING_MESSAGE = 'The generation did not finish; the server did not record an outcome.'


####################
# Response shapes — fixed by the spec; stage 2b is written against exactly these
####################


class RunLineage(BaseModel):
    run_id: str
    created_at: int


class RunResponse(BaseModel):
    id: str
    prompt: str
    reference: Optional[str] = None
    status: str
    rerun_of_run_id: Optional[str] = None
    created_at: int
    # Derived from rerun_of_run_id; nothing new is stored. `rerun_of` resolves the
    # parent's timestamp so the page needs no second request, and degrades to null
    # when the parent is gone rather than erroring.
    rerun_of: Optional[RunLineage] = None
    rerun_count: int = 0


# How much of the prompt a list row carries. A page of 25 runs must not ship 25
# full prompts and their reference material; the full text comes from the detail
# endpoint when a run is opened.
PROMPT_EXCERPT_CHARS = 200
RUN_LIST_DEFAULT_LIMIT = 25
RUN_LIST_MAX_LIMIT = 100


class RunAnswerCounts(BaseModel):
    """Counts by the project's own rule, not the obvious one.

    "Current" means the highest *complete* revision (DECISIONS.md#010), so a
    provider on revision 1 complete and revision 2 failed is one complete answer
    AND one failed attempt. Reporting only the first would hide exactly the case
    `get_current_by_run` was fixed for; only the second would contradict the open
    run, which still shows the good answer.
    """

    complete: int = 0
    failed_attempts: int = 0
    pending: int = 0


class RunReportCounts(BaseModel):
    complete: int = 0
    failed_attempts: int = 0


class RunListItem(BaseModel):
    id: str
    created_at: int
    # Every admin's runs are listed, so the row says whose it is (see the list route).
    admin_email: str
    prompt_excerpt: str
    prompt_truncated: bool
    has_reference: bool
    rerun_of_run_id: Optional[str] = None
    answers: RunAnswerCounts
    reports: RunReportCounts
    has_summary: bool


class RunListResponse(BaseModel):
    runs: list[RunListItem]
    # The oldest (created_at, id) of a full page, for the next request; null when
    # the page is short, because then there is nothing older to ask for.
    next_before: Optional[int] = None
    next_before_id: Optional[str] = None


class ProviderInputSize(BaseModel):
    provider: str
    limit_chars: int
    exceeds: bool


class InputSizeResponse(BaseModel):
    chars: int
    per_provider: list[ProviderInputSize]


class CompareConfigResponse(BaseModel):
    providers: list[ProviderConfig]


class AnswerError(BaseModel):
    code: str
    message: str


class AnswerResponse(BaseModel):
    id: str
    run_id: str
    provider: str
    revision: int
    status: str
    requested_model: Optional[str] = None
    model: Optional[str] = None
    engine_version: Optional[str] = None
    params: Optional[dict] = None
    text: Optional[str] = None
    error: Optional[AnswerError] = None


class ProviderAnswersResponse(BaseModel):
    provider: str
    current: Optional[AnswerResponse] = None
    latest_attempt: Optional[AnswerResponse] = None


class ReportResponse(BaseModel):
    id: str
    run_id: str
    judge: str
    revision: int
    status: str
    requested_model: Optional[str] = None
    model: Optional[str] = None
    # label -> provider. The stored report keeps its anonymous labels; this is
    # what maps them on read.
    label_map: Optional[dict] = None
    report: Optional[dict] = None
    judged_versions: Optional[list] = None
    blinding_compromised: Optional[list] = None
    # Derived from judged_versions: providers with no complete answer at judging time.
    missing_providers: list[str] = []
    # What was sent and how the structured-output ladder went; null until settled.
    params: Optional[dict] = None
    # The same report with every label resolved to a provider id, produced on the
    # server by the one implementation of that transform (stage 4 tallies from
    # it too). Null until the report is complete — or when mapping failed, in
    # which case mapping_error says so and the stored row is untouched.
    mapped: Optional[dict] = None
    mapping_error: Optional[str] = None
    error: Optional[AnswerError] = None


class JudgeReportsResponse(BaseModel):
    judge: str
    current: Optional[ReportResponse] = None
    latest_attempt: Optional[ReportResponse] = None
    # Derived on read: the current report judged a different answer set than the
    # run has now. Never stored.
    outdated: bool = False


class CreateRunForm(BaseModel):
    prompt: Optional[str] = None
    reference: Optional[str] = None
    rerun_of_run_id: Optional[str] = None


class CreateRunResponse(BaseModel):
    run: RunResponse
    providers: list[ProviderConfig]
    input_size: InputSizeResponse


class SummaryResponse(BaseModel):
    id: str
    run_id: str
    revision: int
    narrative: str
    tally: dict
    judged_versions: list
    partial: bool
    included_judges: list
    # Derived on read across both dimensions — answers and reports. Never stored.
    outdated: bool = False


class RunSummaryResponse(BaseModel):
    current: Optional[SummaryResponse] = None
    revisions: int = 0


class GetRunResponse(BaseModel):
    run: RunResponse
    providers: list[ProviderConfig]
    answers: list[ProviderAnswersResponse]
    reports: list[JudgeReportsResponse] = []
    # Computed on read from the current reports and answer versions; nothing
    # persisted here (the persisted copy lives on the summary row).
    tally: dict = {}
    summary: RunSummaryResponse = RunSummaryResponse()


####################
# Helpers
####################


def _run_response(
    run: AnswerCompareRunModel,
    rerun_of: Optional[RunLineage] = None,
    rerun_count: int = 0,
) -> RunResponse:
    """Only the fields the page needs — user_id and admin_email stay server-side."""
    return RunResponse(
        id=run.id,
        prompt=run.prompt,
        reference=run.reference,
        status=run.status,
        rerun_of_run_id=run.rerun_of_run_id,
        created_at=run.created_at,
        rerun_of=rerun_of,
        rerun_count=rerun_count,
    )


async def _run_lineage(run: AnswerCompareRunModel) -> tuple[Optional[RunLineage], int]:
    """The parent (resolved) and how many runs name this one as their source."""
    parent: Optional[RunLineage] = None
    if run.rerun_of_run_id:
        source = await AnswerCompareRuns.get_by_id(run.rerun_of_run_id)
        # A dangling parent id degrades to null: the response must not depend on
        # there being no delete today.
        if source is not None:
            parent = RunLineage(run_id=source.id, created_at=source.created_at)

    children = await AnswerCompareRuns.count_reruns_of([run.id])
    return parent, children.get(run.id, 0)


def _input_chars(prompt: str, reference: Optional[str]) -> int:
    return len(prompt) + len(reference or '')


def _input_size(prompt: str, reference: Optional[str]) -> InputSizeResponse:
    chars = _input_chars(prompt, reference)
    return InputSizeResponse(
        chars=chars,
        per_provider=[
            ProviderInputSize(
                provider=provider_id,
                limit_chars=resolve_max_input_chars(provider_id),
                exceeds=chars > resolve_max_input_chars(provider_id),
            )
            for provider_id in PROVIDER_IDS
        ],
    )


def _encode_error(code: str, message: str) -> str:
    """Store the typed error in the row's Text column without losing the code."""
    return json.dumps({'code': code, 'message': message})


def _decode_error(raw: Optional[str]) -> Optional[AnswerError]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get('code'), str):
        return AnswerError(code=parsed['code'], message=str(parsed.get('message') or ''))
    # Anything not written by _encode_error is still shown rather than dropped.
    return AnswerError(code='unknown', message=raw)


def _is_stale_pending(answer: AnswerCompareAnswerModel, now: Optional[int] = None) -> bool:
    if answer.status != STATUS_PENDING:
        return False
    now = int(time.time()) if now is None else now
    return (now - answer.created_at) > client.request_timeout_seconds()


def _answer_response(answer: AnswerCompareAnswerModel) -> AnswerResponse:
    """Map a stored row to the wire shape, applying the stale-pending read rule."""
    if _is_stale_pending(answer):
        return AnswerResponse(
            id=answer.id,
            run_id=answer.run_id,
            provider=answer.provider,
            revision=answer.revision,
            status=STATUS_FAILED,
            requested_model=answer.requested_model,
            model=answer.model,
            engine_version=answer.engine_version,
            params=answer.params,
            text=answer.text,
            error=AnswerError(code=STALE_PENDING_CODE, message=STALE_PENDING_MESSAGE),
        )

    return AnswerResponse(
        id=answer.id,
        run_id=answer.run_id,
        provider=answer.provider,
        revision=answer.revision,
        status=answer.status,
        requested_model=answer.requested_model,
        model=answer.model,
        engine_version=answer.engine_version,
        params=answer.params,
        text=answer.text,
        error=_decode_error(answer.error),
    )


def _mapped_report(report: AnswerCompareReportModel) -> tuple[Optional[dict], Optional[str]]:
    """The mapped view, or (None, code) when the stored row cannot be mapped.

    Read-time only: an unmappable complete row is answered, never rewritten —
    nothing is written on a GET, and the stored status stays what it is.
    """
    if report.status != STATUS_COMPLETE or not report.report or not report.label_map:
        return None, None
    try:
        return judge.map_report(report.report, report.label_map), None
    except judge.UnmappedLabel as err:
        log.error(
            'report %s (run %s, judge %s) names label %r that its label map does not contain',
            report.id,
            report.run_id,
            report.judge,
            err.label,
        )
        return None, judge.UnmappedLabel.code


def _report_response(report: AnswerCompareReportModel) -> ReportResponse:
    """Map a stored report row to the wire shape, applying the stale-pending rule."""
    stale = (
        report.status == STATUS_PENDING and (int(time.time()) - report.created_at) > client.request_timeout_seconds()
    )
    mapped, mapping_error = _mapped_report(report)

    return ReportResponse(
        id=report.id,
        run_id=report.run_id,
        judge=report.judge,
        revision=report.revision,
        status=STATUS_FAILED if stale else report.status,
        requested_model=report.requested_model,
        model=report.model,
        label_map=report.label_map,
        report=report.report,
        judged_versions=report.judged_versions,
        blinding_compromised=report.blinding_compromised,
        missing_providers=judge.missing_providers_of(report.judged_versions or []),
        params=report.params,
        mapped=mapped,
        mapping_error=mapping_error,
        error=(
            AnswerError(code=STALE_PENDING_CODE, message=STALE_PENDING_MESSAGE)
            if stale
            else _decode_error(report.error)
        ),
    )


def _versions_set(versions: Optional[list]) -> set[tuple[str, int]]:
    return {(item['provider'], item['revision']) for item in (versions or [])}


def _is_outdated(current: Optional[AnswerCompareReportModel], run_versions: list[AnswerVersion]) -> bool:
    """A report is outdated exactly when it judged a different answer set than the
    run has now. False with no current report."""
    if current is None:
        return False
    return _versions_set(current.judged_versions) != {(v.provider, v.revision) for v in run_versions}


def _detail(code: str, **extra: Any) -> dict:
    """Errors travel as a dict in `detail` so the page can branch on `code`."""
    return {'code': code, **extra}


def _tally_attempt(report: AnswerCompareReportModel) -> tally.TallyAttempt:
    """The latest attempt as the read-time rules see it: a stale pending row is a failure."""
    wire = _report_response(report)
    return tally.TallyAttempt(
        revision=report.revision,
        status=wire.status,
        error_code=wire.error.code if wire.error else None,
    )


def _normalised_pairs(items: Optional[list], first: str, second: str) -> set[tuple[str, int]]:
    """A JSON-round-tripped list of dicts as a comparable set.

    Stored as lists of dicts, these are unhashable and order-dependent; comparing
    the lists directly would report "outdated" merely because the order moved.
    """
    return {(str(item[first]), int(item[second])) for item in (items or [])}


def _summary_outdated(
    row: AnswerCompareSummaryModel,
    current_versions: list[AnswerVersion],
    included_reports: list[dict],
) -> bool:
    """Outdated across BOTH dimensions: the answers moved, or the reports did.

    This is why the tally carries included_reports: judged_versions pins the
    answer set, not the report revisions, so a summary built before a re-judge
    would otherwise keep presenting the old rationale as current.
    """
    answers_moved = _normalised_pairs(row.judged_versions, 'provider', 'revision') != {
        (v.provider, v.revision) for v in current_versions
    }
    stored_reports = _normalised_pairs((row.tally or {}).get('included_reports'), 'judge', 'revision')
    reports_moved = stored_reports != _normalised_pairs(included_reports, 'judge', 'revision')
    return answers_moved or reports_moved


def _summary_response(row: AnswerCompareSummaryModel, outdated: bool) -> SummaryResponse:
    return SummaryResponse(
        id=row.id,
        run_id=row.run_id,
        revision=row.revision,
        narrative=row.narrative,
        tally=row.tally or {},
        judged_versions=row.judged_versions or [],
        partial=row.partial,
        included_judges=row.included_judges or [],
        outdated=outdated,
    )


async def _summary_for_run(run_id: str, computed_tally: dict) -> RunSummaryResponse:
    current = await AnswerCompareSummaries.get_current_by_run(run_id)
    if current is None:
        return RunSummaryResponse(current=None, revisions=0)

    run_versions = await AnswerCompareAnswers.get_current_versions(run_id)
    outdated = _summary_outdated(current, run_versions, computed_tally.get('included_reports', []))
    return RunSummaryResponse(
        current=_summary_response(current, outdated),
        revisions=await AnswerCompareSummaries.count_by_run(run_id),
    )


async def _summary_reports(run_id: str, included_judges: list[str]) -> list[summary.SummaryReport]:
    """The stored rows of the judges the tally included, for the narrative's verbatim text."""
    current_reports = {r.judge: r for r in await AnswerCompareReports.get_current_by_run(run_id)}
    return [
        summary.SummaryReport(
            judge=judge,
            label_map=current_reports[judge].label_map or {},
            report=current_reports[judge].report or {},
        )
        for judge in included_judges
        if judge in current_reports
    ]


async def _tally_for_run(run_id: str) -> dict:
    """The tally from the run's current state — computed, never stored, in stage 4."""
    run_versions = await AnswerCompareAnswers.get_current_versions(run_id)
    current_reports = {r.judge: r for r in await AnswerCompareReports.get_current_by_run(run_id)}
    latest_reports = {r.judge: r for r in await AnswerCompareReports.get_latest_attempt_by_run(run_id)}
    configs = {c.id: c for c in resolve_providers()}

    judges = []
    for judge_id in PROVIDER_IDS:
        current = current_reports.get(judge_id)
        latest = latest_reports.get(judge_id)
        judges.append(
            tally.TallyJudge(
                judge=judge_id,
                configured=configs[judge_id].configured,
                current=(
                    tally.TallyReport(
                        revision=current.revision,
                        judged_versions=current.judged_versions or [],
                        label_map=current.label_map or {},
                        report=current.report or {},
                    )
                    if current is not None
                    else None
                ),
                latest_attempt=_tally_attempt(latest) if latest is not None else None,
            )
        )

    return tally.compute_tally([{'provider': v.provider, 'revision': v.revision} for v in run_versions], judges)


async def _settled_response(attempted: AnswerCompareAnswerModel, settled: Optional[AnswerCompareAnswerModel]):
    """Report what the table actually holds, never what we assumed we wrote.

    ``update_result`` returns None when the row was already settled by someone
    else (or vanished), in which case our outcome was deliberately not written —
    reporting it anyway would tell the page a write happened that did not.
    """
    if settled is not None:
        return _answer_response(settled)

    log.warning('answer %s was already settled elsewhere; reporting the stored row', attempted.id)
    stored = await AnswerCompareAnswers.get_by_id(attempted.id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_detail('answer_vanished', provider=attempted.provider),
        )
    return _answer_response(stored)


####################
# Routes
####################


@router.get('/config', response_model=CompareConfigResponse)
async def get_compare_config(user=Depends(get_admin_user)) -> CompareConfigResponse:
    """Report which providers are configured and, honestly, what is still missing.

    No display labels: those live in the frontend's i18n context, and returning
    them here would only have to be undone later.
    """
    return CompareConfigResponse(providers=resolve_providers())


@router.get('/runs', response_model=RunListResponse)
async def list_runs(
    limit: int = Query(RUN_LIST_DEFAULT_LIMIT, ge=1, le=RUN_LIST_MAX_LIMIT),
    before: Optional[int] = Query(None),
    before_id: Optional[str] = Query(None),
    user=Depends(get_admin_user),
) -> RunListResponse:
    """Saved runs, newest first.

    **Every admin's runs are listed, not only the caller's** (recorded decision):
    the surface is admin-only, so this is defensible — but a prompt or reference
    can contain anything, and this product ships a confidentiality feature, so
    each row names the admin who created it. That is the minimum honesty here.

    The number of database round-trips is constant in the number of runs: one
    query for the page, then three grouped aggregates. A per-run loop would be a
    defect even at 25 rows, because it is the shape that rots.
    """
    cursor = (before, before_id or '') if before is not None else None
    runs = await AnswerCompareRuns.list_runs(limit=limit, before=cursor)
    run_ids = [run.id for run in runs]

    answer_counts = await AnswerCompareAnswers.count_by_status_for_runs(run_ids)
    report_counts = await AnswerCompareReports.count_by_status_for_runs(run_ids)
    with_summary = await AnswerCompareSummaries.run_ids_with_summary(run_ids)

    items = [
        RunListItem(
            id=run.id,
            created_at=run.created_at,
            admin_email=run.admin_email,
            prompt_excerpt=run.prompt[:PROMPT_EXCERPT_CHARS],
            prompt_truncated=len(run.prompt) > PROMPT_EXCERPT_CHARS,
            has_reference=bool(run.reference and run.reference.strip()),
            rerun_of_run_id=run.rerun_of_run_id,
            answers=RunAnswerCounts(**answer_counts.get(run.id, {})),
            reports=RunReportCounts(**report_counts.get(run.id, {})),
            has_summary=run.id in with_summary,
        )
        for run in runs
    ]

    full_page = len(runs) == limit
    return RunListResponse(
        runs=items,
        next_before=runs[-1].created_at if full_page else None,
        next_before_id=runs[-1].id if full_page else None,
    )


@router.post('/runs', response_model=CreateRunResponse)
async def create_run(form: CreateRunForm, user=Depends(get_admin_user)) -> CreateRunResponse:
    prompt = (form.prompt or '').strip() or None
    reference = form.reference

    if form.rerun_of_run_id:
        source = await AnswerCompareRuns.get_by_id(form.rerun_of_run_id)
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail('run_not_found', run_id=form.rerun_of_run_id),
            )
        # A rerun is a new run: the earlier one keeps its answers and verdicts.
        prompt = prompt or source.prompt
        if reference is None:
            reference = source.reference

    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_detail('prompt_required', message='A run needs a non-empty prompt.'),
        )

    run = await AnswerCompareRuns.insert(
        user_id=user.id,
        admin_email=user.email,
        form=AnswerCompareRunForm(
            prompt=prompt,
            reference=reference,
            rerun_of_run_id=form.rerun_of_run_id,
        ),
    )

    # Oversized input does not fail this call: it is reported per provider, and
    # the affected provider's generate call is what refuses.
    return CreateRunResponse(
        run=_run_response(run),
        providers=resolve_providers(),
        input_size=_input_size(prompt, reference),
    )


@router.get('/runs/{run_id}', response_model=GetRunResponse)
async def get_run(run_id: str, user=Depends(get_admin_user)) -> GetRunResponse:
    run = await AnswerCompareRuns.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_detail('run_not_found', run_id=run_id),
        )

    current_by_provider = {a.provider: a for a in await AnswerCompareAnswers.get_current_by_run(run_id)}
    latest_by_provider = {a.provider: a for a in await AnswerCompareAnswers.get_latest_attempt_by_run(run_id)}
    run_versions = await AnswerCompareAnswers.get_current_versions(run_id)
    run_tally = await _tally_for_run(run_id)
    current_reports = {r.judge: r for r in await AnswerCompareReports.get_current_by_run(run_id)}
    latest_reports = {r.judge: r for r in await AnswerCompareReports.get_latest_attempt_by_run(run_id)}

    rerun_of, rerun_count = await _run_lineage(run)

    return GetRunResponse(
        run=_run_response(run, rerun_of, rerun_count),
        providers=resolve_providers(),
        answers=[
            ProviderAnswersResponse(
                provider=provider_id,
                current=(
                    _answer_response(current_by_provider[provider_id]) if provider_id in current_by_provider else None
                ),
                latest_attempt=(
                    _answer_response(latest_by_provider[provider_id]) if provider_id in latest_by_provider else None
                ),
            )
            for provider_id in PROVIDER_IDS
        ],
        reports=[
            JudgeReportsResponse(
                judge=judge_id,
                current=_report_response(current_reports[judge_id]) if judge_id in current_reports else None,
                latest_attempt=_report_response(latest_reports[judge_id]) if judge_id in latest_reports else None,
                outdated=_is_outdated(current_reports.get(judge_id), run_versions),
            )
            for judge_id in PROVIDER_IDS
        ],
        tally=run_tally,
        summary=await _summary_for_run(run_id, run_tally),
    )


@router.post('/runs/{run_id}/answers/{provider}', response_model=AnswerResponse)
async def generate_answer(run_id: str, provider: str, user=Depends(get_admin_user)) -> AnswerResponse:
    run = await AnswerCompareRuns.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_detail('run_not_found', run_id=run_id),
        )

    if provider not in PROVIDER_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_detail('unknown_provider', provider=provider),
        )

    config = resolve_provider(provider)
    if not config.configured:
        # 503, matching routers/vesqor.py for the same question: the integration
        # is not configured. No row is written — nothing was attempted.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_detail('not_configured', provider=provider, missing=config.missing),
        )

    limit_chars = resolve_max_input_chars(provider)
    actual_chars = _input_chars(run.prompt, run.reference)
    if actual_chars > limit_chars:
        # Refused before anything is sent, and never truncated.
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=_detail(
                'oversized',
                provider=provider,
                limit_chars=limit_chars,
                actual_chars=actual_chars,
            ),
        )

    latest = await AnswerCompareAnswers.get_latest_attempt(run_id, provider)
    if latest is not None and latest.status == STATUS_PENDING and not _is_stale_pending(latest):
        # A double-click would otherwise buy the same completion twice.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail('already_running', provider=provider, since=latest.created_at),
        )

    # The row is written BEFORE the call: a three-minute request dropped by a
    # proxy would otherwise discard a completion that was already paid for, and
    # GET /runs/{run_id} can still recover this row.
    answer = await AnswerCompareAnswers.insert_next_revision(
        run_id=run_id,
        provider=provider,
        status=STATUS_PENDING,
        requested_model=config.model,
    )

    try:
        result = await client.generate(
            base_url=config.base_url,
            api_key=resolve_api_key(provider),
            model=config.model,
            prompt=run.prompt,
            reference=run.reference,
        )
    except client.ProviderCallError as err:
        settled = await AnswerCompareAnswers.update_result(
            id=answer.id,
            status=STATUS_FAILED,
            error=_encode_error(err.code, err.message),
        )
        # A failure here touches no other provider's rows.
        return await _settled_response(answer, settled)

    settled = await AnswerCompareAnswers.update_result(
        id=answer.id,
        status=STATUS_COMPLETE,
        model=result.model,
        engine_version=result.engine_version,
        params=result.params,
        text=result.text,
    )
    return await _settled_response(answer, settled)


class JudgeSkipped(Exception):
    """A per-judge precondition failed before anything was sent.

    The single-judge endpoint turns this into the HTTP error the contract names;
    run-all turns it into a ``skipped`` entry. One place decides the rule.
    """

    def __init__(self, http_status: int, detail: dict) -> None:
        super().__init__(detail.get('code'))
        self.http_status = http_status
        self.detail = detail


async def judge_once(
    run: AnswerCompareRunModel,
    judge_id: str,
    complete: list[AnswerCompareAnswerModel],
    attempt: Optional[dict[str, Any]] = None,
) -> ReportResponse:
    """The single-judge path: preconditions, blinding, pending row, call, settle.

    Used unchanged by both ``POST …/reports/{judge}`` and ``POST …/reports``, so
    there is exactly one implementation of blinding, insertion and the ladder.
    ``attempt`` (when given) receives the pending row's id as soon as it exists,
    so a caller can settle it if something unexpected escapes from here.
    """
    config = resolve_provider(judge_id)
    if not config.configured:
        raise JudgeSkipped(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            _detail('not_configured', provider=judge_id, missing=config.missing),
        )

    latest = await AnswerCompareReports.get_latest_attempt(run.id, judge_id)
    if (
        latest is not None
        and latest.status == STATUS_PENDING
        and (int(time.time()) - latest.created_at) <= client.request_timeout_seconds()
    ):
        raise JudgeSkipped(
            status.HTTP_409_CONFLICT,
            _detail('already_running', provider=judge_id, since=latest.created_at),
        )

    # Fresh random order per call; the RNG is the system one outside tests.
    labeled = judge.shuffle_labels([(a.provider, a.revision, a.text or '') for a in complete])
    labels = [item.label for item in labeled]
    messages = judge.build_messages(run.prompt, run.reference, labeled)

    limit_chars = resolve_judge_max_input_chars(judge_id)
    actual_chars = judge.judge_input_chars(messages)
    if actual_chars > limit_chars:
        raise JudgeSkipped(
            status.HTTP_413_CONTENT_TOO_LARGE,
            _detail('oversized', provider=judge_id, limit_chars=limit_chars, actual_chars=actual_chars),
        )

    # The row is written BEFORE the call, for the same reason as answers: a
    # dropped connection must not lose a paid completion. What the judge saw —
    # the label map, the exact answer versions, the blinding annotations — is
    # recorded now, because it is decided now.
    report_row = await AnswerCompareReports.insert_next_revision(
        run_id=run.id,
        judge=judge_id,
        status=STATUS_PENDING,
        requested_model=config.model,
        label_map=judge.label_map_of(labeled),
        judged_versions=judge.judged_versions_of(labeled),
        blinding_compromised=judge.detect_blinding_leaks(labeled),
    )
    if attempt is not None:
        attempt['report_row_id'] = report_row.id

    try:
        called = await judge.call_judge(
            provider_id=judge_id,
            base_url=config.base_url,
            api_key=resolve_api_key(judge_id),
            model=config.model,
            messages=messages,
            labels=labels,
        )
    except client.ProviderCallError as err:
        settled = await AnswerCompareReports.update_result(
            id=report_row.id,
            status=STATUS_FAILED,
            error=_encode_error(err.code, err.message),
        )
        return await _settled_report_response(report_row, settled)

    try:
        report = judge.parse_report(called.content, labels)
    except judge.MalformedReport as err:
        # Not repaired, not partially accepted: failed, retryable, out of the tally.
        settled = await AnswerCompareReports.update_result(
            id=report_row.id,
            status=STATUS_FAILED,
            model=called.model,
            params=called.params,
            error=_encode_error(judge.MalformedReport.code, f'The judge did not return a valid report: {err.reason}'),
        )
        return await _settled_report_response(report_row, settled)

    settled = await AnswerCompareReports.update_result(
        id=report_row.id,
        status=STATUS_COMPLETE,
        model=called.model,
        report=report,
        params=called.params,
    )
    return await _settled_report_response(report_row, settled)


async def _load_run_for_judging(run_id: str) -> tuple[AnswerCompareRunModel, list[AnswerCompareAnswerModel]]:
    """The global preconditions shared by both judging endpoints."""
    run = await AnswerCompareRuns.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_detail('run_not_found', run_id=run_id),
        )

    # Judging unlocks at two complete answers (ticket).
    complete = await AnswerCompareAnswers.get_current_by_run(run_id)
    if len(complete) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail('not_enough_answers', complete=[a.provider for a in complete]),
        )
    return run, complete


@router.post('/runs/{run_id}/reports/{judge_id}', response_model=ReportResponse)
async def judge_run(run_id: str, judge_id: str, user=Depends(get_admin_user)) -> ReportResponse:
    """Have one judge evaluate every complete answer of the run, blind.

    The judge is called with its own configured triple in one stateless request
    and sees the answers — including its own — under freshly shuffled labels. It
    is never told which is its own, nor that one might be.
    """
    if await AnswerCompareRuns.get_by_id(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_detail('run_not_found', run_id=run_id),
        )

    if judge_id not in PROVIDER_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_detail('unknown_judge', judge=judge_id),
        )

    run, complete = await _load_run_for_judging(run_id)

    try:
        return await judge_once(run, judge_id, complete)
    except JudgeSkipped as skipped:
        raise HTTPException(status_code=skipped.http_status, detail=skipped.detail) from None


class RunAllEntry(BaseModel):
    judge: str
    status: str  # complete | failed | skipped
    report: Optional[ReportResponse] = None
    reason: Optional[dict] = None


class RunAllResponse(BaseModel):
    results: list[RunAllEntry]
    tally: dict


async def _run_one_judge_entry(
    run: AnswerCompareRunModel, judge_id: str, complete: list[AnswerCompareAnswerModel]
) -> RunAllEntry:
    """One judge's run-all entry. Never raises: a per-judge precondition is a
    ``skipped`` entry, and anything unexpected is that judge's ``failed: internal``
    entry — logged with its traceback, the pending row settled, the other two
    judges' entries untouched. A 500 here would hide reports already saved."""
    attempt: dict[str, Any] = {}
    try:
        report = await judge_once(run, judge_id, complete, attempt)
    except JudgeSkipped as skipped:
        return RunAllEntry(judge=judge_id, status='skipped', reason=skipped.detail)
    except Exception:
        log.exception('run-all: judge %s failed unexpectedly for run %s', judge_id, run.id)
        row_id = attempt.get('report_row_id')
        if row_id is not None:
            # Guarded: a row that somehow settled already is left alone.
            await AnswerCompareReports.update_result(
                id=row_id,
                status=STATUS_FAILED,
                error=_encode_error('internal', 'The judge run failed unexpectedly on the server.'),
            )
        return RunAllEntry(judge=judge_id, status='failed', reason=_detail('internal'))

    return RunAllEntry(judge=judge_id, status=report.status, report=report)


@router.post('/runs/{run_id}/reports', response_model=RunAllResponse)
async def judge_run_all(run_id: str, user=Depends(get_admin_user)) -> RunAllResponse:
    """Run every configured judge through the same single-judge path, concurrently.

    Per-judge preconditions become entries, not HTTP errors; only the global ones
    (unknown run, fewer than two complete answers) are. Each judge gets its own
    fresh shuffle.
    """
    run, complete = await _load_run_for_judging(run_id)

    # return_exceptions is belt-and-braces: _run_one_judge_entry already catches
    # everything, but one judge must never be able to abort the others' awaits.
    outcomes = await asyncio.gather(
        *(_run_one_judge_entry(run, judge_id, complete) for judge_id in PROVIDER_IDS),
        return_exceptions=True,
    )
    results: list[RunAllEntry] = []
    for judge_id, outcome in zip(PROVIDER_IDS, outcomes):
        if isinstance(outcome, BaseException):
            log.error('run-all: entry for judge %s raised past its own guard: %r', judge_id, outcome)
            results.append(RunAllEntry(judge=judge_id, status='failed', reason=_detail('internal')))
        else:
            results.append(outcome)

    return RunAllResponse(results=results, tally=await _tally_for_run(run_id))


async def _settled_report_response(
    attempted: AnswerCompareReportModel, settled: Optional[AnswerCompareReportModel]
) -> ReportResponse:
    """Report what the table holds, never what we assumed we wrote."""
    if settled is not None:
        return _report_response(settled)

    log.warning('report %s was already settled elsewhere; reporting the stored row', attempted.id)
    stored = await AnswerCompareReports.get_by_id(attempted.id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_detail('report_vanished', judge=attempted.judge),
        )
    return _report_response(stored)


@router.post('/runs/{run_id}/summary', response_model=SummaryResponse)
async def build_run_summary(run_id: str, user=Depends(get_admin_user)) -> SummaryResponse:
    """Assemble the run's summary in code and store it as the next revision.

    No model is called: the narrative is built from the tally and the verbatim
    text of the reports the tally included.
    """
    run = await AnswerCompareRuns.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_detail('run_not_found', run_id=run_id),
        )

    run_tally = await _tally_for_run(run_id)
    if run_tally['n_included'] == 0:
        # Every section would be empty and the tally panel already says why;
        # writing such a row is noise (architect decision, DECISIONS.md#013).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail('no_verdicts_to_summarise', excluded=run_tally['excluded']),
        )

    reports = await _summary_reports(run_id, run_tally['included_judges'])
    result = summary.build_summary(run_tally, reports)

    row = await AnswerCompareSummaries.insert_next_revision(
        run_id=run_id,
        narrative=result.narrative,
        tally=result.tally,
        judged_versions=result.judged_versions,
        partial=result.partial,
        included_judges=result.included_judges,
    )
    # Freshly built from the current state, so it cannot be outdated yet.
    return _summary_response(row, False)

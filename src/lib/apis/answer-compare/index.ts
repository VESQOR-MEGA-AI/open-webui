import { WEBUI_API_BASE_URL } from '$lib/constants';

/**
 * Client for the admin answer-comparison API (VQ-25).
 *
 * Deliberately does NOT reuse the helpers in `$lib/apis/vesqor`: those do
 * `error = err.detail ?? 'Network error'`, which stringifies this API's dict
 * `detail` into `[object Object]`. This page branches on `detail.code`, so the
 * code has to survive.
 *
 * The two error classes below are the whole point of this module. The backend
 * writes its `pending` row *before* calling a provider so that a dropped
 * connection is recoverable — which only helps if the page can tell "our
 * backend said this failed" apart from "we never heard back". So:
 *
 *   - a typed `detail.code` from our backend  -> CompareApiError, shown directly;
 *   - anything else (rejected fetch, a 502/504 carrying an HTML error page, a
 *     body that is not the shape we expect) -> CompareConnectionError, which
 *     means *we do not know what happened* and must re-read the run instead of
 *     guessing that the provider failed.
 */

export type ProviderId = 'chatgpt' | 'gemini' | 'vesqor';

export interface ApiErrorDetail {
	code: string;
	message?: string;
	[key: string]: unknown;
}

export interface ProviderConfig {
	id: ProviderId;
	configured: boolean;
	missing: string[];
	base_url: string | null;
	model: string | null;
	/** Whether this provider may be offered as a judge — every provider can be an answer, not every one can score. */
	can_judge: boolean;
}

export interface AnswerError {
	code: string;
	message: string;
}

export interface AnswerRow {
	id: string;
	run_id: string;
	provider: ProviderId;
	revision: number;
	status: 'pending' | 'complete' | 'failed';
	requested_model: string | null;
	model: string | null;
	engine_version: string | null;
	params: Record<string, unknown> | null;
	text: string | null;
	error: AnswerError | null;
}

export interface RunLineage {
	run_id: string;
	created_at: number;
}

export interface RunRow {
	id: string;
	prompt: string;
	reference: string | null;
	status: string;
	rerun_of_run_id: string | null;
	created_at: number;
	/** Derived server-side; null when the source run no longer resolves. */
	rerun_of: RunLineage | null;
	rerun_count: number;
}

export interface ProviderInputSize {
	provider: ProviderId;
	limit_chars: number;
	exceeds: boolean;
}

export interface InputSize {
	chars: number;
	per_provider: ProviderInputSize[];
}

export interface CreateRunResponse {
	run: RunRow;
	providers: ProviderConfig[];
	input_size: InputSize;
}

export interface ProviderAnswers {
	provider: ProviderId;
	current: AnswerRow | null;
	latest_attempt: AnswerRow | null;
}

export type VerdictKind = 'winner' | 'tie' | 'no_reliable_winner';

export interface ReportFinding {
	passage: string;
	note: string;
}

/** One judge's findings for one answer, as the judge wrote them. */
export interface ReportAnswerSection {
	label: string;
	strengths: ReportFinding[];
	errors_or_unsupported: ReportFinding[];
	omissions: ReportFinding[];
	useful_extras: ReportFinding[];
	unnecessary: ReportFinding[];
	improvements: ReportFinding[];
}

/** The raw report: anonymous labels, exactly as returned by the judge (audit trail). */
export interface RawReport {
	answers: ReportAnswerSection[];
	verdict: { kind: VerdictKind; labels: string[] };
	rationale: string;
	needs_verification: string[];
	engine_version?: string;
	rubric_version?: string;
	raw?: Record<string, unknown>;
	normalized?: Record<string, unknown>;
}

/** The six claim classifications of the adjudication specification. */
export type ClaimClassification =
	| 'VERIFIED'
	| 'PARTIALLY_VERIFIED'
	| 'UNSUPPORTED'
	| 'CONTRADICTED'
	| 'FABRICATED_OR_HALLUCINATED'
	| 'NOT_VERIFIABLE';

export type PenaltySeverity = 'MINOR' | 'MODERATE' | 'SEVERE';

export interface AppliedPenalty {
	kind: string;
	severity: PenaltySeverity;
	/** Deducted by the server from its own table — never a figure the model chose. */
	points: number;
	passage: string;
	note: string;
	evidence_ids: string[];
}

export interface ClaimSummary {
	total: number;
	material: number;
	by_classification: Partial<Record<ClaimClassification, number>>;
	material_by_classification: Partial<Record<ClaimClassification, number>>;
}

/** One candidate's computed result, with the provider resolved from its label. */
export interface CandidateScore {
	label: string;
	provider: ProviderId;
	category_scores: Record<string, number>;
	raw_score: number;
	penalty_total: number;
	integrity_capped: boolean;
	final_score: number;
	penalties: AppliedPenalty[];
	claim_summary: ClaimSummary;
	/** null when nothing was verifiable — not the same as zero accuracy. */
	accuracy_confidence_ratio: number | null;
	/** null when the corpus marks nothing CRITICAL. */
	critical_coverage_rate: number | null;
	covered_evidence_ids: string[];
	missed_evidence_ids: string[];
}

export interface PairwiseResult {
	first: ProviderId;
	second: ProviderId;
	stronger: ProviderId | 'tie';
	margin: number;
	reason: string;
	/** false when the judge's prose disagreed with the computed scores. */
	agrees_with_scores: boolean;
}

export interface InjectionSignal {
	label: string;
	provider: ProviderId;
	kind: string;
	excerpt: string;
}

/**
 * The normalized adjudication, label-resolved. Every number here was computed
 * by the server from the judge's findings; nothing is a total the model wrote.
 */
export interface MappedAdjudication {
	engine_version: string;
	rubric_version: string;
	scores: Partial<Record<ProviderId, number>>;
	category_scores: Partial<Record<ProviderId, Record<string, number>>>;
	category_weights: Record<string, number>;
	category_winners: Record<string, ProviderId[]>;
	overall_winner: ProviderId | null;
	overall_ranking: ProviderId[];
	tied_providers: ProviderId[];
	winning_margin: number;
	tie_break_used: string | null;
	confidence: 'high' | 'medium' | 'low';
	confidence_reasons: string[];
	decisive_reasons: string[];
	claim_validation_summary: Partial<Record<ProviderId, ClaimSummary>>;
	pairwise_results: PairwiseResult[];
	candidate_scores: CandidateScore[];
	winner_gap_analysis: string[];
	loser_recovery_analysis: Partial<Record<ProviderId, string[]>>;
	unresolved_uncertainty: string[];
	evidence_complete: boolean;
	dropped_evidence_ids: string[];
	truncated_providers: ProviderId[];
	injection_signals: InjectionSignal[];
	final_adjudication: string;
}

/**
 * The same report with labels resolved to provider ids — produced on the
 * server by the one implementation of that transform. The page renders this
 * and never maps labels itself.
 */
export interface MappedReport {
	answers: Partial<Record<ProviderId, ReportAnswerSection>>;
	verdict: { kind: VerdictKind; providers: ProviderId[] };
	rationale: string;
	needs_verification: string[];
	/**
	 * Absent on reports stored before the canonical engine existed. The page
	 * renders the narrative sections either way and only adds the scored view
	 * when this is present, so old rows stay readable rather than breaking.
	 */
	adjudication?: MappedAdjudication;
}

export interface ReportRow {
	id: string;
	run_id: string;
	judge: ProviderId;
	revision: number;
	status: 'pending' | 'complete' | 'failed';
	requested_model: string | null;
	model: string | null;
	label_map: Record<string, ProviderId> | null;
	report: RawReport | null;
	judged_versions: { provider: ProviderId; revision: number }[] | null;
	blinding_compromised: ProviderId[] | null;
	missing_providers: ProviderId[];
	params: Record<string, unknown> | null;
	mapped: MappedReport | null;
	mapping_error: string | null;
	error: AnswerError | null;
}

export interface JudgeReports {
	judge: ProviderId;
	current: ReportRow | null;
	latest_attempt: ReportRow | null;
	/** Derived by the server on read: the current report judged an older answer set. */
	outdated: boolean;
}

export type ExclusionReason =
	| 'no_report'
	| 'outdated'
	| 'failed'
	| 'malformed'
	| 'not_configured'
	| 'unmappable';

export interface TallyExcluded {
	judge: ProviderId;
	reason: ExclusionReason;
	/** A failed re-judge on top of an outdated report, when both facts hold. */
	latest_attempt?: 'failed' | 'malformed';
}

export interface TallyVerdict {
	judge: ProviderId;
	kind: VerdictKind;
	providers: ProviderId[];
}

/**
 * The tally, computed on the server from validated, fresh verdicts. The page
 * renders it and never recounts: counts and kinds only, no confidence figure.
 */
export interface Tally {
	current_versions: { provider: ProviderId; revision: number }[];
	included_reports: { judge: ProviderId; revision: number }[];
	included_judges: ProviderId[];
	excluded: TallyExcluded[];
	partial: boolean;
	verdicts: TallyVerdict[];
	votes: Record<ProviderId, number>;
	n_included: number;
	outcome: { kind: 'preferred' | 'no_majority' | 'no_valid_verdicts'; provider: ProviderId | null };
	ties: { judge: ProviderId; providers: ProviderId[] }[];
	inconclusive: ProviderId[];
	self_votes: { judge: ProviderId; kind: VerdictKind }[];
	self_votes_excluded: { judge: ProviderId; kind: VerdictKind }[];
}

export interface SummaryRow {
	id: string;
	run_id: string;
	revision: number;
	/** Assembled on the server, in code. The page renders it and changes nothing. */
	narrative: string;
	tally: Tally;
	judged_versions: { provider: ProviderId; revision: number }[];
	partial: boolean;
	included_judges: ProviderId[];
	/** Derived on read across both dimensions: answers moved, or reports moved. */
	outdated: boolean;
}

export interface RunSummary {
	current: SummaryRow | null;
	revisions: number;
}

export interface GetRunResponse {
	run: RunRow;
	providers: ProviderConfig[];
	answers: ProviderAnswers[];
	reports: JudgeReports[];
	tally: Tally;
	summary: RunSummary;
}

export interface RunAllEntry {
	judge: ProviderId;
	status: 'complete' | 'failed' | 'skipped';
	report: ReportRow | null;
	reason: ApiErrorDetail | null;
}

export interface RunAllResponse {
	results: RunAllEntry[];
	tally: Tally;
}

/** Our backend answered, and told us in its own vocabulary what went wrong. */
export class CompareApiError extends Error {
	status: number;
	detail: ApiErrorDetail;

	constructor(status: number, detail: ApiErrorDetail) {
		super(detail.message ?? detail.code);
		this.name = 'CompareApiError';
		this.status = status;
		this.detail = detail;
	}

	get code(): string {
		return this.detail.code;
	}
}

/** We did not hear back, or heard something we cannot read. Never a verdict. */
export class CompareConnectionError extends Error {
	status: number | null;

	constructor(reason: string, status: number | null = null) {
		super(reason);
		this.name = 'CompareConnectionError';
		this.status = status;
	}
}

const headers = (token: string) => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	authorization: `Bearer ${token}`
});

const readJson = async (res: Response): Promise<unknown | undefined> => {
	try {
		return await res.json();
	} catch {
		return undefined;
	}
};

const request = async <T>(
	token: string,
	method: 'GET' | 'POST',
	path: string,
	body?: object
): Promise<T> => {
	let res: Response;

	try {
		res = await fetch(`${WEBUI_API_BASE_URL}/compare${path}`, {
			method,
			headers: headers(token),
			...(body !== undefined ? { body: JSON.stringify(body) } : {})
		});
	} catch {
		// The request never completed. Nothing is known about the server side.
		throw new CompareConnectionError('request-failed');
	}

	if (res.ok) {
		const payload = await readJson(res);
		if (payload === undefined || payload === null || typeof payload !== 'object') {
			throw new CompareConnectionError('unreadable-body', res.status);
		}
		return payload as T;
	}

	if (res.status === 401) {
		// The route guard emits a plain-string detail here, so synthesise the code.
		throw new CompareApiError(401, { code: 'unauthorized' });
	}

	const payload = await readJson(res);
	const detail = (payload as { detail?: unknown } | undefined)?.detail;

	if (detail && typeof detail === 'object' && typeof (detail as ApiErrorDetail).code === 'string') {
		throw new CompareApiError(res.status, detail as ApiErrorDetail);
	}

	// A gateway page, an empty body, or a shape we do not recognise: this is a
	// connection-level failure, not a provider verdict.
	throw new CompareConnectionError('untyped-error-body', res.status);
};

export const getCompareConfig = async (token: string): Promise<{ providers: ProviderConfig[] }> =>
	request(token, 'GET', '/config');

export const createRun = async (
	token: string,
	body: { prompt?: string; reference?: string | null; rerun_of_run_id?: string }
): Promise<CreateRunResponse> => request(token, 'POST', '/runs', body);

export const getRun = async (token: string, runId: string): Promise<GetRunResponse> =>
	request(token, 'GET', `/runs/${encodeURIComponent(runId)}`);

export const generateAnswer = async (
	token: string,
	runId: string,
	provider: ProviderId
): Promise<AnswerRow> =>
	request(
		token,
		'POST',
		`/runs/${encodeURIComponent(runId)}/answers/${encodeURIComponent(provider)}`
	);

export const judgeRun = async (
	token: string,
	runId: string,
	judge: ProviderId
): Promise<ReportRow> =>
	request(token, 'POST', `/runs/${encodeURIComponent(runId)}/reports/${encodeURIComponent(judge)}`);

export const runAllJudges = async (token: string, runId: string): Promise<RunAllResponse> =>
	request(token, 'POST', `/runs/${encodeURIComponent(runId)}/reports`);

export interface RunListItem {
	id: string;
	created_at: number;
	/** Every admin's runs are listed, so a row says whose it is. */
	admin_email: string;
	prompt_excerpt: string;
	prompt_truncated: boolean;
	has_reference: boolean;
	rerun_of_run_id: string | null;
	/**
	 * Counts by the project's rule: "current" is the highest *complete* revision,
	 * so a provider can be both complete and failed-on-its-latest-attempt.
	 * Rendered as sent; the page never recounts.
	 */
	answers: { complete: number; failed_attempts: number; pending: number };
	reports: { complete: number; failed_attempts: number };
	has_summary: boolean;
}

export interface RunListResponse {
	runs: RunListItem[];
	/** Compound cursor: both halves are needed, seconds alone lose same-second rows. */
	next_before: number | null;
	next_before_id: string | null;
}

export const listRuns = async (
	token: string,
	options: { limit?: number; before?: number | null; before_id?: string | null } = {}
): Promise<RunListResponse> => {
	const query = new URLSearchParams();
	if (options.limit !== undefined) query.set('limit', String(options.limit));
	if (options.before !== undefined && options.before !== null) {
		query.set('before', String(options.before));
		// The cursor is a row, not a moment: without the id, two runs created in
		// the same second straddling a page boundary fall through the gap.
		if (options.before_id) query.set('before_id', options.before_id);
	}
	const suffix = query.toString();
	return request(token, 'GET', `/runs${suffix ? `?${suffix}` : ''}`);
};

export const buildSummary = async (token: string, runId: string): Promise<SummaryRow> =>
	request(token, 'POST', `/runs/${encodeURIComponent(runId)}/summary`);

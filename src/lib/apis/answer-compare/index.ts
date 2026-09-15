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

/**
 * Who may judge. The compared trio can be re-enabled as judges per deployment
 * (`ANSWER_COMPARE_<ID>_CAN_JUDGE`), and `sonnet` is the independent judge that
 * never writes an answer — so it is a JudgeId and never a ProviderId
 * (DECISIONS.md#016).
 */
export type JudgeId = ProviderId | 'sonnet';

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

/** One entry of the judge registry: the same shape, keyed by JudgeId. */
export interface JudgeConfig extends Omit<ProviderConfig, 'id'> {
	id: JudgeId;
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
	/** The judge registry — the page builds judge cards from this, never from `providers`. */
	judges: JudgeConfig[];
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
}

export interface ReportRow {
	id: string;
	run_id: string;
	judge: JudgeId;
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
	judge: JudgeId;
	current: ReportRow | null;
	latest_attempt: ReportRow | null;
	/** Derived by the server on read: the current report judged an older answer set. */
	outdated: boolean;
	/**
	 * Whether this judge may be called now. False for a legacy participant judge
	 * whose stored report must stay visible but which is never called again.
	 */
	capable: boolean;
}

export type ExclusionReason =
	| 'no_report'
	| 'outdated'
	| 'failed'
	| 'malformed'
	| 'not_configured'
	| 'not_capable'
	| 'unmappable';

export interface TallyExcluded {
	judge: JudgeId;
	reason: ExclusionReason;
	/** A failed re-judge on top of an outdated report, when both facts hold. */
	latest_attempt?: 'failed' | 'malformed';
}

export interface TallyVerdict {
	judge: JudgeId;
	kind: VerdictKind;
	providers: ProviderId[];
}

/**
 * The tally, computed on the server from validated, fresh verdicts. The page
 * renders it and never recounts: counts and kinds only, no confidence figure.
 */
export interface Tally {
	current_versions: { provider: ProviderId; revision: number }[];
	included_reports: { judge: JudgeId; revision: number }[];
	included_judges: JudgeId[];
	excluded: TallyExcluded[];
	partial: boolean;
	verdicts: TallyVerdict[];
	/** Votes are per ANSWER provider: a judge is never a column here. */
	votes: Record<ProviderId, number>;
	n_included: number;
	/** The denominator: how many judges may judge right now (the capable set). */
	n_judges: number;
	outcome: { kind: 'preferred' | 'no_majority' | 'no_valid_verdicts'; provider: ProviderId | null };
	ties: { judge: JudgeId; providers: ProviderId[] }[];
	inconclusive: JudgeId[];
	self_votes: { judge: JudgeId; kind: VerdictKind }[];
	self_votes_excluded: { judge: JudgeId; kind: VerdictKind }[];
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
	included_judges: JudgeId[];
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
	judges: JudgeConfig[];
	answers: ProviderAnswers[];
	reports: JudgeReports[];
	tally: Tally;
	summary: RunSummary;
}

export interface RunAllEntry {
	judge: JudgeId;
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

export const getCompareConfig = async (
	token: string
): Promise<{ providers: ProviderConfig[]; judges: JudgeConfig[] }> =>
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

export const judgeRun = async (token: string, runId: string, judge: JudgeId): Promise<ReportRow> =>
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

import type { RunListItem, RunListResponse, RunRow } from '$lib/apis/answer-compare';

/**
 * History state, a sibling of `tallyState`/`summaryState` with the same
 * discipline: one write point, pure transitions, the page writes only here.
 *
 * The rule, as everywhere on this page: **the module renders what the server
 * sent and recounts nothing.** The row's `complete` / `failed_attempts` /
 * `pending` follow the project's "current means highest *complete* revision"
 * rule, which the page cannot re-derive from an excerpt anyway — so it does not
 * try. Paging **appends**; it never replaces what is already listed.
 */

export interface HistoryState {
	runs: RunListItem[];
	/** The compound cursor for the next page: both halves, or none. */
	nextBefore: number | null;
	nextBeforeId: string | null;
	loaded: boolean;
}

export const EMPTY_HISTORY_COPY = 'No saved comparisons yet.';
export const RERUN_NOTICE_COPY =
	'New run created from the earlier prompt. Nothing has been generated yet — use Generate all three answers.';

export const initialHistoryState = (): HistoryState => ({
	runs: [],
	nextBefore: null,
	nextBeforeId: null,
	loaded: false
});

/** The single write point. */
const withHistory = (
	runs: RunListItem[],
	response: Pick<RunListResponse, 'next_before' | 'next_before_id'>
): HistoryState => ({
	runs,
	nextBefore: response.next_before ?? null,
	nextBeforeId: response.next_before_id ?? null,
	loaded: true
});

/** The first page: replaces whatever was listed. */
export const applyHistory = (response: RunListResponse): HistoryState => withHistory(response.runs, response);

/**
 * A later page: **appends**. A run already listed is not added twice — the
 * compound cursor makes duplicates unlikely, but a refresh racing a page would
 * otherwise show one run in two places.
 */
export const appendHistory = (state: HistoryState, response: RunListResponse): HistoryState => {
	const seen = new Set(state.runs.map((run) => run.id));
	return withHistory([...state.runs, ...response.runs.filter((run) => !seen.has(run.id))], response);
};

export const clearHistory = (): HistoryState => initialHistoryState();

/** Whether there is an older page to ask for. Both halves of the cursor or nothing. */
export const hasOlder = (state: HistoryState): boolean => state.nextBefore !== null && state.nextBeforeId !== null;

export const olderCursor = (state: HistoryState): { before: number; before_id: string } | null =>
	hasOlder(state) ? { before: state.nextBefore as number, before_id: state.nextBeforeId as string } : null;

export interface Copy {
	key: string;
	params: Record<string, string | number>;
}

export interface HistoryRowView {
	id: string;
	createdAt: number;
	adminEmail: string;
	/** The server's excerpt, untouched, plus whether to show an ellipsis. */
	excerpt: string;
	truncated: boolean;
	hasReference: boolean;
	isRerun: boolean;
	/** Chips, in fixed order, rendered exactly as the server counted them. */
	chips: Copy[];
}

const chipsFor = (run: RunListItem): Copy[] => {
	const chips: Copy[] = [{ key: '{{count}} answers', params: { count: run.answers.complete } }];
	if (run.answers.failed_attempts > 0) {
		chips.push({ key: '{{count}} failed', params: { count: run.answers.failed_attempts } });
	}
	if (run.answers.pending > 0) {
		chips.push({ key: '{{count}} pending', params: { count: run.answers.pending } });
	}
	chips.push({ key: '{{count}} reports', params: { count: run.reports.complete } });
	if (run.reports.failed_attempts > 0) {
		chips.push({ key: '{{count}} reports failed', params: { count: run.reports.failed_attempts } });
	}
	if (run.has_summary) {
		chips.push({ key: 'summary', params: {} });
	}
	return chips;
};

export const historyRows = (state: HistoryState): HistoryRowView[] =>
	state.runs.map((run) => ({
		id: run.id,
		createdAt: run.created_at,
		adminEmail: run.admin_email,
		excerpt: run.prompt_excerpt,
		truncated: run.prompt_truncated,
		hasReference: run.has_reference,
		isRerun: run.rerun_of_run_id !== null,
		chips: chipsFor(run)
	}));

/** The lineage lines for an open run, or null when it has none. */
export const lineageLines = (run: RunRow): { parent: Copy | null; children: Copy | null } => ({
	parent: run.rerun_of ? { key: 'Rerun of {{when}}', params: { when: run.rerun_of.created_at } } : null,
	children: run.rerun_count > 0 ? { key: 'Re-run {{count}} times', params: { count: run.rerun_count } } : null
});

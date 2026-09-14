import type { GetRunResponse, SummaryRow } from '$lib/apis/answer-compare';

/**
 * Summary panel state, a sibling of `tallyState.ts` with the same discipline:
 * one write point, pure transitions, the page writes only through this module.
 *
 * The one rule: **this module renders, it does not assemble.** The narrative is
 * built on the server, in code, deterministically; nothing here re-orders,
 * re-words, truncates or reflows it. It travels through untouched, and a vitest
 * asserts that by identity.
 */

export interface SummaryPanelState {
	summary: SummaryRow | null;
	revisions: number;
}

export const initialSummaryState = (): SummaryPanelState => ({ summary: null, revisions: 0 });

/** The single write point. */
const withSummary = (summary: SummaryRow | null, revisions: number): SummaryPanelState => ({ summary, revisions });

/** Adopt the summary a GET carries. Reports and tally in the same response are not consulted. */
export const applySummary = (response: Partial<Pick<GetRunResponse, 'summary'>>): SummaryPanelState =>
	withSummary(response.summary?.current ?? null, response.summary?.revisions ?? 0);

/** Adopt the row a successful POST returned; the GET that follows confirms `outdated`. */
export const applySummaryRow = (state: SummaryPanelState, row: SummaryRow): SummaryPanelState =>
	withSummary(row, Math.max(state.revisions, row.revision));

export const clearSummary = (): SummaryPanelState => withSummary(null, 0);

export const OUTDATED_COPY = 'Built on earlier answers or earlier reports. Build again to summarise the current state.';
export const NO_VERDICTS_COPY = 'Summary needs at least one valid verdict.';

export interface Copy {
	key: string;
	params: Record<string, string | number>;
}

export interface SummaryView {
	/** The server's text, byte for byte. */
	narrative: string;
	version: Copy;
	outdated: Copy | null;
}

export const summaryView = (summary: SummaryRow): SummaryView => ({
	narrative: summary.narrative,
	version: { key: 'Version {{revision}}', params: { revision: summary.revision } },
	outdated: summary.outdated ? { key: OUTDATED_COPY, params: {} } : null
});

/** Why the Build summary button is disabled, or null when it is enabled. */
export const summaryButtonDisabledReason = (includedVerdicts: number): Copy | null =>
	includedVerdicts < 1 ? { key: NO_VERDICTS_COPY, params: {} } : null;

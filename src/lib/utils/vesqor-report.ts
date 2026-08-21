// VESQOR report helpers — detect, parse and describe VESQOR MEGA AI
// report messages (brain output rendered as a document, not a chat bubble).
//
// The brain (api.vesqorai.com) returns reports as Markdown in
// chat.completion.content. In NON-streaming completions it additionally
// returns the machine-readable envelope `vq_meta`:
//   { report, mermaid, confidenceScore, notes, systemPromptApplied, source }
// with `reportTitle` landing in the same envelope (parallel brain work).
// In streaming mode only the report Markdown travels (SSE content chunks),
// so the streaming path detects the report from the final message text.

export interface ReportMeta {
	/** Derived title: reportTitle from vq_meta, else first markdown heading, else null. */
	title?: string;
	confidenceScore?: number;
	notes?: string;
	source?: string;
	reportClass?: string;
	reportType?: string;
	systemPromptApplied?: string;
}

/** Extract the machine-readable VESQOR envelope from a message, wherever it lives. */
export const parseVqMeta = (message: Record<string, any> | null | undefined): ReportMeta | null => {
	if (!message) {
		return null;
	}

	// The envelope may arrive top-level on the wire payload, under `info`
	// (usage/info convention), or already nested as `vq_meta`.
	const candidates = [
		message['vq_meta'],
		message['info']?.['vq_meta'],
		message['meta']?.['vq_meta'],
		message['raw']?.['vq_meta']
	];

	for (const candidate of candidates) {
		if (candidate && typeof candidate === 'object') {
			const meta: ReportMeta = {};
			if (typeof candidate.confidenceScore === 'number') {
				meta.confidenceScore = candidate.confidenceScore;
			}
			if (typeof candidate.notes === 'string') {
				meta.notes = candidate.notes;
			}
			if (typeof candidate.source === 'string') {
				meta.source = candidate.source;
			}
			if (typeof candidate.reportClass === 'string') {
				meta.reportClass = candidate.reportClass;
			}
			if (typeof candidate.reportType === 'string') {
				meta.reportType = candidate.reportType;
			}
			if (typeof candidate.systemPromptApplied === 'string') {
				meta.systemPromptApplied = candidate.systemPromptApplied;
			}
			if (typeof candidate.reportTitle === 'string') {
				meta.title = candidate.reportTitle;
			} else if (typeof candidate.title === 'string') {
				meta.title = candidate.title;
			}
			return meta;
		}
	}

	// Legacy fallback: a top-level `title` field on the message itself.
	if (typeof message['title'] === 'string' && message['title'].trim()) {
		return { title: message['title'].trim() };
	}

	return null;
};

/**
 * Derive a report title from Markdown content:
 *   - the first `# ` / `## ` heading, cleaned of markdown syntax
 *   - else a truncated version of the first meaningful line
 *   - else null.
 */
export const deriveReportTitle = (content: string | null | undefined, maxLength = 90): string | null => {
	if (!content || !content.trim()) {
		return null;
	}

	const headingMatch = content.match(/^\s{0,3}(#{1,3})\s+(.+)$/m);
	if (headingMatch) {
		const heading = stripMarkdown(headingMatch[2]);
		return truncateTitle(heading, maxLength);
	}

	const firstLine = content
		.split('\n')
		.map((l) => l.trim())
		.find((l) => l.length > 0 && !l.startsWith('|') && !l.startsWith('```') && !l.startsWith('<!--'));
	if (firstLine) {
		const cleaned = stripMarkdown(firstLine);
		if (cleaned.length >= 12) {
			return truncateTitle(cleaned, maxLength);
		}
	}

	return null;
};

/** Best-effort markdown syntax strip for title display. */
const stripMarkdown = (text: string): string =>
	text
		.replace(/[`*_~>]+/g, '')
		.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
		.replace(/#{1,6}\s+/g, '')
		.trim();

const truncateTitle = (text: string, maxLength: number): string =>
	text.length <= maxLength ? text : `${text.slice(0, maxLength).trimEnd()}…`;

/**
 * Detect whether a message is a VESQOR report (rendered as a document):
 *   - explicit vq_meta envelope present, OR
 *   - long content (>= 1500 chars) containing markdown `## ` headings,
 *     which matches the brain's report anatomy.
 * Short/ordinary chat answers always stay as chat.
 */
export const isReportMessage = (
	message: Record<string, any> | null | undefined,
	content: string | null | undefined
): boolean => {
	const meta = parseVqMeta(message);
	if (meta && (meta.confidenceScore !== undefined || meta.reportClass || meta.reportType || meta.title)) {
		return true;
	}

	const text = content ?? '';
	if (text.trim().length < 1500) {
		return false;
	}

	const headingCount = (text.match(/^\s{0,3}##+\s+/gm) ?? []).length;
	if (headingCount >= 2) {
		return true;
	}

	// Single `# ` (H1) title + substantial body is also report-shaped.
	return /^\s{0,3}#\s+.+$/m.test(text) && headingCount >= 1 && text.trim().length >= 2000;
};

/** The body rendered in the document view: full content (no details stripped). */
export const reportBody = (message: Record<string, any> | null | undefined): string => {
	if (!message) {
		return '';
	}

	// The brain envelope may carry a dedicated `report` markdown field.
	// Look in every location parseVqMeta checks.
	for (const candidate of [message['vq_meta'], message['info']?.['vq_meta'], message['meta']?.['vq_meta'], message['raw']?.['vq_meta']]) {
		if (candidate && typeof candidate === 'object' && typeof candidate['report'] === 'string') {
			return candidate['report'];
		}
	}

	return message?.content ?? '';
};

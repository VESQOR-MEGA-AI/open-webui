// Shared helpers for the Legal & Enterprise Documents UI.
//
// `fieldValues` / `missingRequired` / `missingRecommended` in the legal API
// contract use dotted paths (e.g. "counterparty.legal.name") into a nested
// onboarding-profile object. These helpers translate between the flat paths
// used for form fields and the nested object the API expects.

export const getByPath = (obj: Record<string, unknown> | null | undefined, path: string): unknown => {
	if (!obj) return undefined;
	return path.split('.').reduce<unknown>((acc, key) => {
		if (acc && typeof acc === 'object') {
			return (acc as Record<string, unknown>)[key];
		}
		return undefined;
	}, obj);
};

export const setByPath = (obj: Record<string, unknown>, path: string, value: unknown): void => {
	const keys = path.split('.');
	let cursor = obj;
	for (let i = 0; i < keys.length - 1; i++) {
		const key = keys[i];
		if (typeof cursor[key] !== 'object' || cursor[key] === null) {
			cursor[key] = {};
		}
		cursor = cursor[key] as Record<string, unknown>;
	}
	cursor[keys[keys.length - 1]] = value;
};

export type LegalErrorInfo = {
	message: string;
	blockers?: { code: string; message: string; field?: string }[];
	reason?: string;
	touched?: string;
	raw?: unknown;
};

// Routes proxying to the brain surface the brain's raw JSON error body as a
// string (backend/open_webui/routers/legal.py passes `response.text` through
// as the HTTPException detail). The api client then re-throws that string.
// Try to recover the structured `blockers`/`reason` payload from it.
export const parseLegalError = (err: unknown): LegalErrorInfo => {
	const text = typeof err === 'string' ? err : (err as any)?.message ?? String(err ?? '');

	try {
		const parsed = JSON.parse(text);
		if (parsed && typeof parsed === 'object') {
			return {
				message: parsed.reason ?? parsed.message ?? text,
				blockers: Array.isArray(parsed.blockers) ? parsed.blockers : undefined,
				reason: parsed.reason,
				touched: parsed.touched,
				raw: parsed
			};
		}
	} catch {
		// Not JSON — fall through to the raw text.
	}

	return { message: text };
};

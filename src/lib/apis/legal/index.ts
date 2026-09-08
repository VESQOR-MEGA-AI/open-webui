import { WEBUI_API_BASE_URL } from '$lib/constants';

// ── VESQOR Legal & Enterprise Documents department — client ────────────────
// Mirrors the API_CONTRACT.md binding shapes (Legal & Enterprise Documents
// HTTP surface, v1.0). All routes are proxied by the backend
// (backend/open_webui/routers/legal.py) to the brain under the service token.
// When LEGAL_DEPARTMENT_ENABLED=false the backend returns 404 for every route.

const _get = async (token: string, path: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/legal${path}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err ?? 'Network error';
			return null;
		});

	if (error) {
		const msg = typeof error === 'string' ? error : JSON.stringify(error);
		throw msg;
	}

	return res;
};

const _mutate = async (token: string, method: 'POST' | 'PATCH' | 'DELETE', path: string, body?: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/legal${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...(body !== undefined ? { body: JSON.stringify(body) } : {})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err ?? 'Network error';
			return null;
		});

	if (error) {
		const msg = typeof error === 'string' ? error : JSON.stringify(error);
		throw msg;
	}

	return res;
};

// ── Types (API_CONTRACT.md v1.0) ───────────────────────────────────────────

export type DocumentState =
	| 'TEMPLATE'
	| 'DRAFT'
	| 'MISSING_INFORMATION'
	| 'READY_FOR_REVIEW'
	| 'APPROVED'
	| 'READY_FOR_SIGNATURE'
	| 'PARTIALLY_SIGNED'
	| 'EXECUTED'
	| 'SUPERSEDED'
	| 'TERMINATED'
	| 'ARCHIVED';

export type Readiness = 'ready_to_sign' | 'needs_input' | 'blocked' | 'additional_compliance_review';

export type LegalAuthority = {
	signatoryId: string;
	name: string;
	title: string;
	code: 'AUTHORIZED' | 'SIGNATORY_AUTHORITY_NOT_ACTIVE' | 'CEO_APPROVAL_REQUIRED' | 'EXECUTIVE_LEGAL_REVIEW' | 'AUTHORITY_NOT_VERIFIED';
	allowed: boolean;
	reason?: string;
	escalateTo?: string;
	alternativeSignatory?: { id: string; name: string; title: string };
	requiresCounselReview?: boolean;
	restrictedHits?: string[];
};

export type LegalSignatory = { id: string; name: string; title: string };

export type LegalBlocker = { code: string; message: string; field?: string };

export type LegalCounterparty = {
	name?: string;
	entityType?: string;
	jurisdiction?: string;
	address?: string;
};

export type LegalTemplate = {
	templateId: string;
	code: string;
	title: string;
	category: string;
	stage: string;
	version: string;
	jurisdiction: string;
	requiredFields: string[];
	optionalFields: string[];
	allowedSignatories: LegalSignatory[];
};

export type LegalDocumentSummary = {
	documentId: string;
	templateId: string;
	templateVersion: string;
	title: string;
	counterpartyName: string | null;
	state: DocumentState;
	readiness: Readiness;
	revisionNumber: number;
	updatedAt: string;
};

export type LegalDocumentFull = LegalDocumentSummary & {
	counterparty: { name: string; entityType: string; jurisdiction?: string; address?: string } | null;
	effectiveDate: string | null;
	fieldValues: Record<string, unknown>;
	renderedContent?: string;
	signatories: LegalSignatory[];
	missingRequired: string[];
	missingRecommended: string[];
	authority: LegalAuthority | null;
	blockers: LegalBlocker[];
	materialModified: boolean;
	counselReviewRequired: boolean;
	createdAt: string;
	updatedAt: string;
};

export type LegalValidationResult = {
	documentId: string;
	valid: boolean;
	blockers: LegalBlocker[];
	missingRequired: string[];
	warnings: string[];
	readiness: Readiness;
	authority: LegalAuthority | null;
};

export type LegalComparison = {
	comparison: {
		added: { clause: string; detail: string }[];
		removed: { clause: string; detail: string }[];
		changed: { clause: string; detail: string }[];
		partyChanges: unknown;
		governingLaw: unknown;
		confidentialityDuration: unknown;
		ipProvisions: unknown;
		aiDataClauses: unknown;
		liabilityTerms: unknown;
		signatoryDifferences: unknown;
		unusualObligations: unknown;
	};
	disclaimer: string;
};

export type LegalPackage = {
	packageId: string;
	counterparty: LegalCounterparty | null;
	state: string;
	documents: {
		documentId?: string;
		templateId: string;
		title: string;
		because: string;
		executionOrder: number;
		state?: DocumentState;
		readiness?: Readiness;
		blocked?: boolean;
		blockedReason?: string;
	}[];
	overallReady?: boolean;
};

export type LegalAuditEvent = {
	eventType: string;
	actor: string;
	revisionNumber: number;
	source: string;
	details: string;
	createdAt: string;
};

// ── Templates ──────────────────────────────────────────────────────────────

export const getLegalTemplates = async (token: string): Promise<{ templates: LegalTemplate[] }> => {
	return _get(token, '/templates');
};

// ── Documents ──────────────────────────────────────────────────────────────

export const createLegalDocument = async (
	token: string,
	body: {
		templateId: string;
		counterparty?: LegalCounterparty;
		effectiveDate?: string;
		prefill?: Record<string, unknown>;
		signatoryId?: string;
	}
): Promise<LegalDocumentFull> => {
	return _mutate(token, 'POST', '/documents', body);
};

export const getLegalDocument = async (token: string, documentId: string): Promise<LegalDocumentFull> => {
	return _get(token, `/documents/${documentId}`);
};

export const searchLegalDocuments = async (
	token: string,
	params: { q?: string; templateId?: string; state?: string; readiness?: string } = {}
): Promise<{ documents: LegalDocumentSummary[] }> => {
	const query = new URLSearchParams();
	if (params.q) query.set('q', params.q);
	if (params.templateId) query.set('templateId', params.templateId);
	if (params.state) query.set('state', params.state);
	if (params.readiness) query.set('readiness', params.readiness);
	const qs = query.toString();
	return _get(token, `/documents${qs ? `?${qs}` : ''}`);
};

export const populateLegalDocument = async (token: string, documentId: string, fields: Record<string, unknown>): Promise<LegalDocumentFull> => {
	return _mutate(token, 'POST', `/documents/${documentId}/populate`, { fields });
};

export const updateLegalDocument = async (
	token: string,
	documentId: string,
	body: { instruction?: string; fieldValues?: Record<string, unknown> }
): Promise<LegalDocumentFull> => {
	return _mutate(token, 'PATCH', `/documents/${documentId}`, body);
};

export const validateLegalDocument = async (token: string, documentId: string): Promise<LegalValidationResult> => {
	return _mutate(token, 'POST', `/documents/${documentId}/validate`);
};

export const renderLegalDocument = async (
	token: string,
	documentId: string,
	format: 'md' | 'html' | 'docx' | 'pdf',
	signatureCopy = false
): Promise<unknown> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/legal/documents/${documentId}/render`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ format, signatureCopy })
	});

	if (!res.ok) {
		const err = await res.json().catch(() => null);
		throw JSON.stringify(err?.detail ?? err ?? 'Render failed');
	}

	const contentType = res.headers.get('content-type') ?? '';
	if (contentType.includes('json')) {
		return res.json();
	}
	return res.blob();
};

export const transitionLegalDocument = async (
	token: string,
	documentId: string,
	to: string
): Promise<{ documentId: string; from: string; to: string; allowed: boolean; revisionNumber: number }> => {
	return _mutate(token, 'POST', `/documents/${documentId}/state`, { to });
};

export const getLegalAuditTrail = async (token: string, documentId: string): Promise<{ events: LegalAuditEvent[] }> => {
	return _get(token, `/documents/${documentId}/audit`);
};

// ── Compare (multipart) ────────────────────────────────────────────────────

export const compareLegalDocument = async (
	token: string,
	file: File,
	templateId?: string
): Promise<LegalComparison> => {
	const formData = new FormData();
	formData.append('file', file);
	if (templateId) formData.append('templateId', templateId);

	const res = await fetch(`${WEBUI_API_BASE_URL}/legal/compare`, {
		method: 'POST',
		headers: {
			authorization: `Bearer ${token}`
		},
		body: formData
	});

	if (!res.ok) {
		const err = await res.json().catch(() => null);
		throw JSON.stringify(err?.detail ?? err ?? 'Compare failed');
	}
	return res.json();
};

// ── Packages (onboarding) ──────────────────────────────────────────────────

export const createLegalPackage = async (
	token: string,
	body: {
		counterparty: { name: string; entityType: string; jurisdiction?: string; address?: string };
		answers: {
			m365Integration: boolean;
			personalDataProcessing: boolean;
			pilot: boolean;
			productionServices: boolean;
			sla: boolean;
		};
	}
): Promise<LegalPackage> => {
	return _mutate(token, 'POST', '/packages', body);
};

export const getLegalPackage = async (token: string, packageId: string): Promise<LegalPackage> => {
	return _get(token, `/packages/${packageId}`);
};

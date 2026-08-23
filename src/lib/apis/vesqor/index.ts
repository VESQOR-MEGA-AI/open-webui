import { WEBUI_API_BASE_URL } from '$lib/constants';

const _get = async (token: string, path: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/vesqor${path}`, {
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
			error = err.detail ?? 'Network error';
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

const _mutate = async (token: string, method: 'POST' | 'PATCH' | 'DELETE', path: string, body?: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/vesqor${path}`, {
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
			error = err.detail ?? 'Network error';
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getVesqorBillingStatus = async (token: string) => {
	return _get(token, '/billing/status');
};

export const createVesqorCheckout = async (token: string, variant: string) => {
	return _mutate(token, 'POST', '/checkout', { variant });
};

export const createVesqorPortalSession = async (token: string) => {
	return _mutate(token, 'POST', '/portal');
};

export const getVesqorCredits = async (token: string) => {
	return _get(token, '/credits');
};

export const createVesqorCreditTopup = async (token: string, credits: number) => {
	return _mutate(token, 'POST', '/credits/topup', { credits });
};

export const getVesqorProviders = async (token: string) => {
	return _get(token, '/admin/providers');
};

export const addVesqorProvider = async (token: string, body: object) => {
	return _mutate(token, 'POST', '/admin/providers', body);
};

export const updateVesqorProvider = async (token: string, id: string, body: object) => {
	return _mutate(token, 'PATCH', `/admin/providers/${id}`, body);
};

export const deleteVesqorProvider = async (token: string, id: string) => {
	return _mutate(token, 'DELETE', `/admin/providers/${id}`);
};

export const testVesqorProvider = async (token: string, id: string) => {
	return _mutate(token, 'POST', `/admin/providers/${id}/test`);
};

export const getVesqorTokens = async (token: string) => {
	return _get(token, '/admin/tokens');
};

export const createVesqorToken = async (token: string, body: object) => {
	return _mutate(token, 'POST', '/admin/tokens', body);
};

export const updateVesqorToken = async (token: string, id: string, body: object) => {
	return _mutate(token, 'PATCH', `/admin/tokens/${id}`, body);
};

export const deleteVesqorToken = async (token: string, id: string) => {
	return _mutate(token, 'DELETE', `/admin/tokens/${id}`);
};

export interface VesqorExportParams {
	format: 'pdf' | 'docx' | 'html' | 'md' | 'json';
	reportId?: string;
	title?: string;
	body?: string;
	chat_id?: string;
	chat_title?: string;
	message_id?: string;
}

/**
 * Export a VESQOR report through the Open WebUI backend proxy
 * (POST /api/v1/vesqor/export → brain POST /api/v1/export).
 * Returns the raw response so the caller can save the file blob.
 */
export const exportVesqorReport = async (token: string, params: VesqorExportParams): Promise<Response> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/vesqor/export`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(params)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => null);
		throw err?.detail ?? 'Export failed';
	}

	return res;
};

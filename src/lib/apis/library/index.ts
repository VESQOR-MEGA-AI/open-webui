import { WEBUI_API_BASE_URL } from '$lib/constants';

export type LibraryItem = {
	id: string;
	user_id: string;
	chat_id?: string | null;
	message_id?: string | null;
	chat_title?: string | null;
	filename: string;
	content_type?: string | null;
	size?: number | null;
	format?: string | null;
	source: string;
	path?: string | null;
	created_at: number;
};

type GetLibraryParams = {
	chat_id?: string;
	q?: string;
	type?: 'report' | 'file';
};

export const getLibrary = async (
	token: string,
	params: GetLibraryParams = {}
): Promise<LibraryItem[]> => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (params.chat_id) searchParams.set('chat_id', params.chat_id);
	if (params.q) searchParams.set('q', params.q);
	if (params.type) searchParams.set('type', params.type);

	const qs = searchParams.toString();

	const res = await fetch(`${WEBUI_API_BASE_URL}/library/${qs ? `?${qs}` : ''}`, {
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
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res ?? [];
};

export const deleteLibraryItem = async (token: string, id: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/library/${id}`, {
		method: 'DELETE',
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
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const downloadLibraryItem = async (token: string, id: string): Promise<Response> => {
	return fetch(`${WEBUI_API_BASE_URL}/library/${id}/download`, {
		method: 'GET',
		headers: {
			authorization: `Bearer ${token}`
		}
	});
};

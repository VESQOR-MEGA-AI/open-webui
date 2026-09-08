<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { goto } from '$app/navigation';

	import dayjs from '$lib/dayjs';
	import {
		getLegalTemplates,
		searchLegalDocuments,
		type LegalTemplate,
		type LegalDocumentSummary,
		type DocumentState
	} from '$lib/apis/legal';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n: Writable<any> = getContext('i18n');

	const STATES: DocumentState[] = [
		'TEMPLATE',
		'DRAFT',
		'MISSING_INFORMATION',
		'READY_FOR_REVIEW',
		'APPROVED',
		'READY_FOR_SIGNATURE',
		'PARTIALLY_SIGNED',
		'EXECUTED',
		'SUPERSEDED',
		'TERMINATED',
		'ARCHIVED'
	];

	const READINESS_BADGE: Record<string, 'success' | 'warning' | 'error' | 'info'> = {
		ready_to_sign: 'success',
		needs_input: 'warning',
		blocked: 'error',
		additional_compliance_review: 'info'
	};

	let loading = true;
	let unavailable = false;
	let error: string | null = null;

	let templates: LegalTemplate[] = [];
	let documents: LegalDocumentSummary[] = [];

	let query = '';
	let stateFilter = '';
	let templateFilter = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	const search = async () => {
		loading = true;
		error = null;
		try {
			const res = await searchLegalDocuments(localStorage.token, {
				q: query || undefined,
				state: stateFilter || undefined,
				templateId: templateFilter || undefined
			});
			documents = res?.documents ?? [];
		} catch (err) {
			unavailable = true;
			error = typeof err === 'string' ? err : $i18n.t('Could not load documents.');
		} finally {
			loading = false;
		}
	};

	const handleSearchInput = () => {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(search, 300);
	};

	onMount(async () => {
		try {
			const templatesRes = await getLegalTemplates(localStorage.token);
			templates = templatesRes?.templates ?? [];
		} catch (err) {
			// Template list is only used for the filter dropdown — a failure here
			// is not fatal, the main search call below will surface unavailability.
		}
		await search();
	});
</script>

<div class="w-full h-full flex flex-col overflow-y-auto px-2.5 py-3">
	<div class="flex items-center gap-2 mb-3">
		<h1 class="text-sm font-medium">{$i18n.t('Legal Documents')}</h1>
		<button
			type="button"
			class="ml-auto text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition"
			on:click={() => goto('/legal/new')}
		>
			{$i18n.t('Create Document')}
		</button>
	</div>

	<div class="flex flex-wrap items-center gap-2 mb-3">
		<div class="flex min-w-0 flex-1 items-center rounded-xl bg-gray-50 dark:bg-gray-850 px-2 h-8">
			<Search className="size-3.5" />
			<input
				class="w-full text-sm px-2 py-1 bg-transparent outline-hidden"
				bind:value={query}
				on:input={handleSearchInput}
				placeholder={$i18n.t('Search documents')}
			/>
			{#if query}
				<button
					class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
					on:click={() => {
						query = '';
						search();
					}}
				>
					<XMark className="size-3" strokeWidth="2" />
				</button>
			{/if}
		</div>

		<select
			bind:value={stateFilter}
			on:change={search}
			class="text-xs rounded-xl bg-gray-50 dark:bg-gray-850 px-2 h-8 outline-hidden"
		>
			<option value="">{$i18n.t('All states')}</option>
			{#each STATES as s (s)}
				<option value={s}>{s}</option>
			{/each}
		</select>

		<select
			bind:value={templateFilter}
			on:change={search}
			class="text-xs rounded-xl bg-gray-50 dark:bg-gray-850 px-2 h-8 outline-hidden"
		>
			<option value="">{$i18n.t('All templates')}</option>
			{#each templates as t (t.templateId)}
				<option value={t.templateId}>{t.title}</option>
			{/each}
		</select>
	</div>

	{#if loading}
		<div class="flex justify-center py-12">
			<Spinner className="size-5" />
		</div>
	{:else if unavailable}
		<div class="flex flex-col items-center justify-center min-h-[40vh] text-center gap-2">
			<div class="text-sm text-gray-600 dark:text-gray-400">
				{$i18n.t('Legal & Enterprise Documents is not available right now.')}
			</div>
			{#if error}
				<div class="text-xs text-gray-400 dark:text-gray-600">{error}</div>
			{/if}
		</div>
	{:else if documents.length === 0}
		<div class="flex flex-col items-center justify-center min-h-[40vh] text-center gap-1">
			<div class="text-sm text-gray-600 dark:text-gray-400">{$i18n.t('No documents found.')}</div>
			<div class="text-xs text-gray-400 dark:text-gray-600">
				{$i18n.t('Create your first document to get started.')}
			</div>
		</div>
	{:else}
		<div class="flex flex-col gap-0.5">
			<div
				class="flex items-center gap-2 px-2.5 pb-1.5 text-xs text-gray-400 dark:text-gray-600"
			>
				<div class="flex-1 min-w-0">{$i18n.t('Title')}</div>
				<div class="w-36 shrink-0 hidden md:block">{$i18n.t('Counterparty')}</div>
				<div class="w-28 shrink-0">{$i18n.t('State')}</div>
				<div class="w-24 shrink-0 hidden sm:block">{$i18n.t('Readiness')}</div>
				<div class="w-14 shrink-0 text-right hidden sm:block">{$i18n.t('Rev')}</div>
				<div class="w-24 shrink-0 text-right">{$i18n.t('Updated at')}</div>
			</div>

			{#each documents as doc (doc.documentId)}
				<button
					type="button"
					class="flex items-center gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-900 transition"
					on:click={() => goto(`/legal/${doc.documentId}`)}
				>
					<div class="flex-1 min-w-0 truncate text-sm text-gray-800 dark:text-gray-200">
						{doc.title}
					</div>
					<div class="w-36 shrink-0 hidden md:block truncate text-xs text-gray-500 dark:text-gray-500">
						{doc.counterpartyName ?? '—'}
					</div>
					<div class="w-28 shrink-0 text-xs text-gray-500 dark:text-gray-500">{doc.state}</div>
					<div class="w-24 shrink-0 hidden sm:block">
						<Badge type={READINESS_BADGE[doc.readiness] ?? 'muted'} content={doc.readiness} />
					</div>
					<div class="w-14 shrink-0 text-right text-xs text-gray-400 dark:text-gray-600">
						{doc.revisionNumber}
					</div>
					<div class="w-24 shrink-0 text-right text-xs text-gray-400 dark:text-gray-600">
						{dayjs(doc.updatedAt).format('MMM D')}
					</div>
				</button>
			{/each}
		</div>
	{/if}
</div>

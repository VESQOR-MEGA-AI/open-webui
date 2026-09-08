<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { goto } from '$app/navigation';

	import { getLegalTemplates, createLegalDocument, type LegalTemplate } from '$lib/apis/legal';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let initialTemplateId: string | null = null;

	const i18n: Writable<any> = getContext('i18n');

	let loading = true;
	let unavailable = false;
	let templates: LegalTemplate[] = [];

	let templateId = '';
	let counterpartyName = '';
	let counterpartyEntityType = '';
	let counterpartyJurisdiction = '';
	let effectiveDate = '';
	let signatoryId = '';

	let submitting = false;
	let submitError: string | null = null;

	$: selectedTemplate = templates.find((t) => t.templateId === templateId) ?? null;

	onMount(async () => {
		loading = true;
		try {
			const res = await getLegalTemplates(localStorage.token);
			templates = res?.templates ?? [];
			if (initialTemplateId && templates.some((t) => t.templateId === initialTemplateId)) {
				templateId = initialTemplateId;
			} else if (templates.length === 1) {
				templateId = templates[0].templateId;
			}
		} catch (err) {
			unavailable = true;
		} finally {
			loading = false;
		}
	});

	const submit = async () => {
		if (!templateId) {
			submitError = $i18n.t('Choose a template to continue.');
			return;
		}

		submitting = true;
		submitError = null;
		try {
			const doc = await createLegalDocument(localStorage.token, {
				templateId,
				counterparty: counterpartyName
					? {
							name: counterpartyName,
							entityType: counterpartyEntityType || undefined,
							jurisdiction: counterpartyJurisdiction || undefined
						}
					: undefined,
				effectiveDate: effectiveDate || undefined,
				signatoryId: signatoryId || undefined
			});
			goto(`/legal/${doc.documentId}`);
		} catch (err) {
			submitError = typeof err === 'string' ? err : $i18n.t('Could not create the document.');
		} finally {
			submitting = false;
		}
	};
</script>

<div class="w-full h-full flex flex-col overflow-y-auto px-2.5 py-3">
	<div class="max-w-lg w-full mx-auto">
		<h1 class="text-sm font-medium mb-3">{$i18n.t('Create Document')}</h1>

		{#if loading}
			<div class="flex justify-center py-12">
				<Spinner className="size-5" />
			</div>
		{:else if unavailable}
			<div class="flex flex-col items-center justify-center min-h-[40vh] text-center gap-2">
				<div class="text-sm text-gray-600 dark:text-gray-400">
					{$i18n.t('Legal & Enterprise Documents is not available right now.')}
				</div>
			</div>
		{:else}
			<div class="flex flex-col gap-3">
				<label class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Template')}
					<select
						bind:value={templateId}
						class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
					>
						<option value="">{$i18n.t('Select a template')}</option>
						{#each templates as t (t.templateId)}
							<option value={t.templateId}>{t.title}</option>
						{/each}
					</select>
				</label>

				<div class="grid grid-cols-2 gap-2">
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Counterparty name')}
						<input
							type="text"
							bind:value={counterpartyName}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Entity type')}
						<input
							type="text"
							bind:value={counterpartyEntityType}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Jurisdiction')}
						<input
							type="text"
							bind:value={counterpartyJurisdiction}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Effective date')}
						<input
							type="date"
							bind:value={effectiveDate}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
				</div>

				{#if selectedTemplate && selectedTemplate.allowedSignatories.length > 0}
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Signatory')}
						<select
							bind:value={signatoryId}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						>
							<option value="">{$i18n.t('Select a signatory')}</option>
							{#each selectedTemplate.allowedSignatories as s (s.id)}
								<option value={s.id}>{s.name} — {s.title}</option>
							{/each}
						</select>
					</label>
				{/if}

				{#if submitError}
					<div class="text-xs text-red-500">{submitError}</div>
				{/if}

				<div class="flex justify-end gap-2 mt-1">
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={() => goto('/legal')}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						disabled={submitting}
						class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={submit}
					>
						{#if submitting}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Create Document')}
						{/if}
					</button>
				</div>
			</div>
		{/if}
	</div>
</div>

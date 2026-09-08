<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import {
		getLegalTemplates,
		searchLegalDocuments,
		compareLegalDocument,
		createLegalPackage,
		type LegalTemplate,
		type LegalDocumentSummary,
		type LegalComparison,
		type LegalPackage
	} from '$lib/apis/legal';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import LegalIcon from '$lib/components/layout/Sidebar/icons/Legal.svelte';

	const i18n: Writable<any> = getContext('i18n');

	const QUICK_CREATE_CODES = ['NDA', 'PILOT', 'MSA', 'DPA', 'SECURITY', 'M365', 'SOW', 'SLA'];

	let loading = true;
	let unavailable = false;
	let templates: LegalTemplate[] = [];
	let recentDocuments: LegalDocumentSummary[] = [];

	const findTemplate = (code: string): LegalTemplate | undefined => {
		const needle = code.toUpperCase();
		return templates.find(
			(t) => t.code?.toUpperCase() === needle || t.title?.toUpperCase().includes(needle)
		);
	};

	const load = async () => {
		loading = true;
		unavailable = false;
		try {
			const [templatesRes, documentsRes] = await Promise.all([
				getLegalTemplates(localStorage.token),
				searchLegalDocuments(localStorage.token)
			]);
			templates = templatesRes?.templates ?? [];
			recentDocuments = (documentsRes?.documents ?? [])
				.slice()
				.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
				.slice(0, 5);
		} catch (err) {
			unavailable = true;
		} finally {
			loading = false;
		}
	};

	onMount(load);

	const quickCreate = (code: string) => {
		const template = findTemplate(code);
		if (!template) {
			toast.error($i18n.t('That template is not available yet.'));
			return;
		}
		goto(`/legal/new?template=${encodeURIComponent(template.templateId)}`);
	};

	// ── Review Document (upload & compare) ──────────────────────────────────
	let showReview = false;
	let reviewFile: FileList | null = null;
	let reviewTemplateId = '';
	let reviewLoading = false;
	let reviewResult: LegalComparison | null = null;
	let reviewError: string | null = null;

	const resetReview = () => {
		reviewFile = null;
		reviewTemplateId = '';
		reviewResult = null;
		reviewError = null;
		reviewLoading = false;
	};

	const openReview = () => {
		resetReview();
		showReview = true;
	};

	const runReview = async () => {
		if (!reviewFile || reviewFile.length === 0) {
			reviewError = $i18n.t('Choose a file to compare.');
			return;
		}
		reviewLoading = true;
		reviewError = null;
		try {
			reviewResult = await compareLegalDocument(
				localStorage.token,
				reviewFile[0],
				reviewTemplateId || undefined
			);
		} catch (err) {
			reviewError = typeof err === 'string' ? err : $i18n.t('Comparison failed.');
		} finally {
			reviewLoading = false;
		}
	};

	// ── Customer Onboarding (package builder) ───────────────────────────────
	let showOnboarding = false;
	let onboardingLoading = false;
	let onboardingError: string | null = null;
	let onboardingResult: LegalPackage | null = null;
	let onboardingForm = {
		name: '',
		entityType: '',
		jurisdiction: '',
		address: '',
		m365Integration: false,
		personalDataProcessing: false,
		pilot: false,
		productionServices: false,
		sla: false
	};

	const resetOnboarding = () => {
		onboardingForm = {
			name: '',
			entityType: '',
			jurisdiction: '',
			address: '',
			m365Integration: false,
			personalDataProcessing: false,
			pilot: false,
			productionServices: false,
			sla: false
		};
		onboardingResult = null;
		onboardingError = null;
		onboardingLoading = false;
	};

	const openOnboarding = () => {
		resetOnboarding();
		showOnboarding = true;
	};

	const runOnboarding = async () => {
		if (!onboardingForm.name.trim()) {
			onboardingError = $i18n.t('Counterparty name is required.');
			return;
		}
		onboardingLoading = true;
		onboardingError = null;
		try {
			onboardingResult = await createLegalPackage(localStorage.token, {
				counterparty: {
					name: onboardingForm.name,
					entityType: onboardingForm.entityType,
					jurisdiction: onboardingForm.jurisdiction || undefined,
					address: onboardingForm.address || undefined
				},
				answers: {
					m365Integration: onboardingForm.m365Integration,
					personalDataProcessing: onboardingForm.personalDataProcessing,
					pilot: onboardingForm.pilot,
					productionServices: onboardingForm.productionServices,
					sla: onboardingForm.sla
				}
			});
		} catch (err) {
			onboardingError = typeof err === 'string' ? err : $i18n.t('Could not create the package.');
		} finally {
			onboardingLoading = false;
		}
	};
</script>

<div class="w-full h-full flex flex-col overflow-y-auto px-2.5 py-3">
	{#if loading}
		<div class="flex justify-center py-12">
			<Spinner className="size-5" />
		</div>
	{:else if unavailable}
		<div class="flex flex-col items-center justify-center min-h-[50vh] text-center gap-2">
			<LegalIcon className="size-8 text-gray-300 dark:text-gray-700" />
			<div class="text-sm text-gray-600 dark:text-gray-400">
				{$i18n.t('Legal & Enterprise Documents is not available right now.')}
			</div>
			<div class="text-xs text-gray-400 dark:text-gray-600 max-w-sm">
				{$i18n.t(
					'The department may be disabled or temporarily unreachable. Please try again later.'
				)}
			</div>
		</div>
	{:else}
		<div class="max-w-3xl w-full mx-auto flex flex-col gap-4">
			<div
				class="rounded-2xl border border-emerald-200/60 dark:border-emerald-800/40 bg-emerald-50/60 dark:bg-emerald-950/30 backdrop-blur-sm p-4"
			>
				<div class="flex items-center gap-2 mb-1">
					<LegalIcon className="size-5 text-emerald-600 dark:text-emerald-400" />
					<h1 class="text-base font-medium text-emerald-800 dark:text-emerald-200">
						{$i18n.t('Legal & Enterprise Documents')}
					</h1>
				</div>
				<p class="text-xs leading-relaxed text-emerald-800/80 dark:text-emerald-200/80">
					{$i18n.t(
						'Create, review, and manage NDAs, MSAs, DPAs, and other enterprise documents — with authority checks and a full audit trail.'
					)}
				</p>
			</div>

			<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
				<button
					type="button"
					class="flex flex-col items-center gap-1.5 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={() => goto('/legal/new')}
				>
					{$i18n.t('Create Document')}
				</button>
				<button
					type="button"
					class="flex flex-col items-center gap-1.5 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={openReview}
				>
					{$i18n.t('Review Document')}
				</button>
				<button
					type="button"
					class="flex flex-col items-center gap-1.5 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={openOnboarding}
				>
					{$i18n.t('Customer Onboarding')}
				</button>
				<button
					type="button"
					class="flex flex-col items-center gap-1.5 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={() => goto('/legal/documents')}
				>
					{$i18n.t('Existing Documents')}
				</button>
			</div>

			<div>
				<div class="text-xs text-gray-500 dark:text-gray-500 mb-1.5 px-0.5">
					{$i18n.t('Quick Create')}
				</div>
				<div class="flex flex-wrap gap-1.5">
					{#each QUICK_CREATE_CODES as code (code)}
						{@const template = findTemplate(code)}
						<button
							type="button"
							disabled={!template}
							class="px-2.5 py-1 rounded-full text-xs font-medium border transition {template
								? 'border-teal-200 dark:border-teal-800 text-teal-700 dark:text-teal-300 hover:bg-teal-50 dark:hover:bg-teal-950/40'
								: 'border-gray-100 dark:border-gray-800 text-gray-300 dark:text-gray-700 cursor-not-allowed'}"
							on:click={() => quickCreate(code)}
						>
							{code}
						</button>
					{/each}
				</div>
			</div>

			{#if recentDocuments.length > 0}
				<div>
					<div class="flex items-center justify-between mb-1.5 px-0.5">
						<div class="text-xs text-gray-500 dark:text-gray-500">
							{$i18n.t('Recent Documents')}
						</div>
						<button
							type="button"
							class="text-xs text-emerald-600 dark:text-emerald-400 hover:underline"
							on:click={() => goto('/legal/documents')}
						>
							{$i18n.t('View all')}
						</button>
					</div>
					<div class="flex flex-col gap-0.5">
						{#each recentDocuments as doc (doc.documentId)}
							<button
								type="button"
								class="flex items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-900 transition"
								on:click={() => goto(`/legal/${doc.documentId}`)}
							>
								<span class="truncate text-sm text-gray-800 dark:text-gray-200">{doc.title}</span>
								<span class="shrink-0 text-xs text-gray-400 dark:text-gray-600">{doc.state}</span>
							</button>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>

<Modal bind:show={showReview} size="md">
	<div class="p-4">
		<div class="text-base font-medium mb-3">{$i18n.t('Review Document')}</div>

		{#if reviewResult}
			<div class="flex flex-col gap-3 max-h-[60vh] overflow-y-auto text-sm">
				{#each [['added', 'Added'], ['removed', 'Removed'], ['changed', 'Changed']] as [key, label] (key)}
					{@const entries = (reviewResult.comparison as any)[key] ?? []}
					{#if entries.length > 0}
						<div>
							<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
								{$i18n.t(label)}
							</div>
							<ul class="list-disc pl-4 space-y-0.5 text-xs">
								{#each entries as entry}
									<li><span class="font-medium">{entry.clause}:</span> {entry.detail}</li>
								{/each}
							</ul>
						</div>
					{/if}
				{/each}
				<div class="text-[11px] text-gray-400 dark:text-gray-600 italic">
					{reviewResult.disclaimer}
				</div>
			</div>
			<div class="flex justify-end mt-3">
				<button
					type="button"
					class="text-xs px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
					on:click={() => (showReview = false)}
				>
					{$i18n.t('Close')}
				</button>
			</div>
		{:else}
			<div class="flex flex-col gap-2.5">
				<label class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Document to compare')}
					<input
						type="file"
						accept=".pdf,.docx,.md,.txt"
						bind:files={reviewFile}
						class="block w-full text-xs mt-1"
					/>
				</label>
				<label class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Compare against template (optional)')}
					<select
						bind:value={reviewTemplateId}
						class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
					>
						<option value="">{$i18n.t('Auto-detect')}</option>
						{#each templates as t (t.templateId)}
							<option value={t.templateId}>{t.title}</option>
						{/each}
					</select>
				</label>

				{#if reviewError}
					<div class="text-xs text-red-500">{reviewError}</div>
				{/if}

				<div class="flex justify-end gap-2 mt-1">
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={() => (showReview = false)}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						disabled={reviewLoading}
						class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={runReview}
					>
						{#if reviewLoading}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Compare')}
						{/if}
					</button>
				</div>
			</div>
		{/if}
	</div>
</Modal>

<Modal bind:show={showOnboarding} size="md">
	<div class="p-4">
		<div class="text-base font-medium mb-3">{$i18n.t('Customer Onboarding')}</div>

		{#if onboardingResult}
			<div class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto text-sm">
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('The following documents were prepared for this counterparty:')}
				</div>
				{#each onboardingResult.documents as doc (doc.templateId + doc.executionOrder)}
					<button
						type="button"
						disabled={!doc.documentId}
						class="flex flex-col items-start gap-0.5 rounded-xl px-2.5 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-900 transition disabled:hover:bg-transparent"
						on:click={() => doc.documentId && goto(`/legal/${doc.documentId}`)}
					>
						<span class="text-sm text-gray-800 dark:text-gray-200">{doc.title}</span>
						<span class="text-[11px] text-gray-400 dark:text-gray-600">{doc.because}</span>
					</button>
				{/each}
			</div>
			<div class="flex justify-end mt-3">
				<button
					type="button"
					class="text-xs px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
					on:click={() => (showOnboarding = false)}
				>
					{$i18n.t('Close')}
				</button>
			</div>
		{:else}
			<div class="flex flex-col gap-2.5">
				<div class="grid grid-cols-2 gap-2">
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Counterparty name')}
						<input
							type="text"
							bind:value={onboardingForm.name}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Entity type')}
						<input
							type="text"
							bind:value={onboardingForm.entityType}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Jurisdiction')}
						<input
							type="text"
							bind:value={onboardingForm.jurisdiction}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
					<label class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Address')}
						<input
							type="text"
							bind:value={onboardingForm.address}
							class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
						/>
					</label>
				</div>

				<div class="grid grid-cols-2 gap-1.5 mt-1">
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={onboardingForm.m365Integration} />
						{$i18n.t('Microsoft 365 integration')}
					</label>
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={onboardingForm.personalDataProcessing} />
						{$i18n.t('Processes personal data')}
					</label>
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={onboardingForm.pilot} />
						{$i18n.t('Pilot engagement')}
					</label>
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={onboardingForm.productionServices} />
						{$i18n.t('Production services')}
					</label>
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={onboardingForm.sla} />
						{$i18n.t('Needs an SLA')}
					</label>
				</div>

				{#if onboardingError}
					<div class="text-xs text-red-500">{onboardingError}</div>
				{/if}

				<div class="flex justify-end gap-2 mt-1">
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={() => (showOnboarding = false)}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						disabled={onboardingLoading}
						class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={runOnboarding}
					>
						{#if onboardingLoading}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Create Package')}
						{/if}
					</button>
				</div>
			</div>
		{/if}
	</div>
</Modal>

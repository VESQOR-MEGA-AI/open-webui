<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { marked } from 'marked';

	import dayjs from '$lib/dayjs';
	import {
		getLegalDocument,
		getLegalTemplates,
		populateLegalDocument,
		updateLegalDocument,
		validateLegalDocument,
		renderLegalDocument,
		transitionLegalDocument,
		getLegalAuditTrail,
		type LegalDocumentFull,
		type LegalTemplate,
		type LegalValidationResult,
		type LegalAuditEvent
	} from '$lib/apis/legal';
	import { getByPath, setByPath, parseLegalError } from './utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Modal from '$lib/components/common/Modal.svelte';

	export let id: string;

	const i18n: Writable<any> = getContext('i18n');

	const READINESS_BADGE: Record<string, 'success' | 'warning' | 'error' | 'info'> = {
		ready_to_sign: 'success',
		needs_input: 'warning',
		blocked: 'error',
		additional_compliance_review: 'info'
	};

	let loading = true;
	let unavailable = false;
	let doc: LegalDocumentFull | null = null;
	let template: LegalTemplate | null = null;

	const humanizeField = (path: string): string => {
		const last = path.split('.').pop() ?? path;
		const spaced = last.replace(/_/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2');
		return spaced.charAt(0).toUpperCase() + spaced.slice(1);
	};

	// ── Load ─────────────────────────────────────────────────────────────────
	let populateValues: Record<string, string> = {};

	const buildPopulateValues = () => {
		const fields = template
			? [...new Set([...template.requiredFields, ...template.optionalFields])]
			: [...new Set([...(doc?.missingRequired ?? []), ...(doc?.missingRecommended ?? [])])];

		const values: Record<string, string> = {};
		for (const path of fields) {
			const existing = getByPath(doc?.fieldValues, path);
			values[path] = existing !== undefined && existing !== null ? String(existing) : '';
		}
		populateValues = values;
	};

	const load = async () => {
		loading = true;
		unavailable = false;
		try {
			doc = await getLegalDocument(localStorage.token, id);
			try {
				const templatesRes = await getLegalTemplates(localStorage.token);
				template = (templatesRes?.templates ?? []).find((t) => t.templateId === doc?.templateId) ?? null;
			} catch (err) {
				template = null;
			}
			buildPopulateValues();
		} catch (err) {
			unavailable = true;
		} finally {
			loading = false;
		}
	};

	onMount(load);

	// ── Populate ─────────────────────────────────────────────────────────────
	let populateSubmitting = false;
	let populateError: string | null = null;

	const submitPopulate = async () => {
		if (!doc) return;
		populateSubmitting = true;
		populateError = null;
		try {
			const fields: Record<string, unknown> = {};
			for (const [path, value] of Object.entries(populateValues)) {
				if (value !== '') setByPath(fields, path, value);
			}
			doc = await populateLegalDocument(localStorage.token, doc.documentId, fields);
			buildPopulateValues();
			toast.success($i18n.t('Fields updated.'));
		} catch (err) {
			const info = parseLegalError(err);
			populateError = info.message;
		} finally {
			populateSubmitting = false;
		}
	};

	// ── Instruction (natural-language edit) ─────────────────────────────────
	let instruction = '';
	let instructionSubmitting = false;
	let instructionError: string | null = null;
	let instructionBlockers: { code: string; message: string; field?: string }[] = [];

	const submitInstruction = async () => {
		if (!doc || !instruction.trim()) return;
		instructionSubmitting = true;
		instructionError = null;
		instructionBlockers = [];
		try {
			doc = await updateLegalDocument(localStorage.token, doc.documentId, { instruction });
			buildPopulateValues();
			instruction = '';
			toast.success($i18n.t('Document updated.'));
		} catch (err) {
			const info = parseLegalError(err);
			instructionError = info.message;
			instructionBlockers = info.blockers ?? [];
		} finally {
			instructionSubmitting = false;
		}
	};

	// ── Validate ─────────────────────────────────────────────────────────────
	let validating = false;
	let validationResult: LegalValidationResult | null = null;
	let validationError: string | null = null;

	const runValidate = async () => {
		if (!doc) return;
		validating = true;
		validationError = null;
		try {
			validationResult = await validateLegalDocument(localStorage.token, doc.documentId);
		} catch (err) {
			const info = parseLegalError(err);
			validationError = info.message;
		} finally {
			validating = false;
		}
	};

	// ── Render / export ──────────────────────────────────────────────────────
	let renderFormat: 'md' | 'html' | 'docx' | 'pdf' = 'md';
	let renderSignatureCopy = false;
	let rendering = false;
	let renderError: string | null = null;
	let renderPreview: { format: 'md' | 'html'; content: string } | null = null;
	let showRenderPreview = false;

	const runRender = async () => {
		if (!doc) return;
		rendering = true;
		renderError = null;
		try {
			const res = await renderLegalDocument(
				localStorage.token,
				doc.documentId,
				renderFormat,
				renderSignatureCopy
			);

			if (renderFormat === 'md' || renderFormat === 'html') {
				const content = renderFormat === 'md' ? (res as any).markdown : (res as any).html;
				renderPreview = { format: renderFormat, content };
				showRenderPreview = true;
			} else {
				const blob = res as Blob;
				const url = URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = `${(doc.title || 'document').replace(/[^a-z0-9-_]+/gi, '_')}.${renderFormat}`;
				document.body.appendChild(a);
				a.click();
				a.remove();
				URL.revokeObjectURL(url);
			}
		} catch (err) {
			const info = parseLegalError(err);
			renderError = info.message;
		} finally {
			rendering = false;
		}
	};

	// ── Prepare for signature ────────────────────────────────────────────────
	let transitioning = false;
	let transitionError: string | null = null;
	let transitionReason: string | null = null;

	const prepareForSignature = async () => {
		if (!doc) return;
		transitioning = true;
		transitionError = null;
		transitionReason = null;
		try {
			await transitionLegalDocument(localStorage.token, doc.documentId, 'READY_FOR_SIGNATURE');
			doc = await getLegalDocument(localStorage.token, doc.documentId);
			toast.success($i18n.t('Document is ready for signature.'));
		} catch (err) {
			const info = parseLegalError(err);
			transitionError = info.message;
			transitionReason = info.reason ?? null;
		} finally {
			transitioning = false;
		}
	};

	// ── Audit trail ───────────────────────────────────────────────────────────
	let showAudit = false;
	let auditLoading = false;
	let auditEvents: LegalAuditEvent[] | null = null;

	const toggleAudit = async () => {
		showAudit = !showAudit;
		if (showAudit && auditEvents === null && doc) {
			auditLoading = true;
			try {
				const res = await getLegalAuditTrail(localStorage.token, doc.documentId);
				auditEvents = res?.events ?? [];
			} catch (err) {
				auditEvents = [];
			} finally {
				auditLoading = false;
			}
		}
	};
</script>

<div class="w-full h-full flex flex-col overflow-y-auto px-2.5 py-3">
	{#if loading}
		<div class="flex justify-center py-12">
			<Spinner className="size-5" />
		</div>
	{:else if unavailable || !doc}
		<div class="flex flex-col items-center justify-center min-h-[40vh] text-center gap-2">
			<div class="text-sm text-gray-600 dark:text-gray-400">
				{$i18n.t('This document is not available.')}
			</div>
			<button
				type="button"
				class="text-xs text-emerald-600 dark:text-emerald-400 hover:underline"
				on:click={() => goto('/legal/documents')}
			>
				{$i18n.t('Back to documents')}
			</button>
		</div>
	{:else}
		<div class="max-w-3xl w-full mx-auto flex flex-col gap-4">
			<div class="flex items-start gap-2">
				<div class="min-w-0 flex-1">
					<h1 class="text-base font-medium truncate">{doc.title}</h1>
					<div class="text-xs text-gray-500 dark:text-gray-500">
						{doc.counterparty?.name ?? $i18n.t('No counterparty set')} · {$i18n.t('Revision')}
						{doc.revisionNumber}
					</div>
				</div>
				<div class="flex shrink-0 items-center gap-1.5">
					<Badge type="muted" content={doc.state} />
					<Badge type={READINESS_BADGE[doc.readiness] ?? 'muted'} content={doc.readiness} />
				</div>
			</div>

			{#if doc.state === 'EXECUTED'}
				<div
					class="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
				>
					{$i18n.t('This document is executed. Further changes may be blocked or create a new revision.')}
				</div>
			{/if}

			{#if doc.blockers.length > 0}
				<div class="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 px-3 py-2">
					<div class="text-xs font-medium text-red-700 dark:text-red-300 mb-1">
						{$i18n.t('Blockers')}
					</div>
					<ul class="text-xs text-red-700 dark:text-red-300 list-disc pl-4 space-y-0.5">
						{#each doc.blockers as b}
							<li>{b.message}{b.field ? ` (${b.field})` : ''}</li>
						{/each}
					</ul>
				</div>
			{/if}

			{#if doc.authority}
				<div
					class="rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-2.5"
				>
					<div class="flex items-center gap-2 mb-1">
						<div class="text-xs font-medium">{$i18n.t('Signing authority')}</div>
						<Badge type={doc.authority.allowed ? 'success' : 'error'} content={doc.authority.code} />
					</div>
					<div class="text-xs text-gray-600 dark:text-gray-400">
						{doc.authority.name} — {doc.authority.title}
					</div>
					{#if doc.authority.reason}
						<div class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{doc.authority.reason}</div>
					{/if}
					{#if doc.authority.escalateTo}
						<div class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">
							{$i18n.t('Escalate to')}: {doc.authority.escalateTo}
						</div>
					{/if}
					{#if doc.authority.alternativeSignatory}
						<div class="text-xs text-gray-500 dark:text-gray-500 mt-0.5">
							{$i18n.t('Alternative signatory')}: {doc.authority.alternativeSignatory.name} —
							{doc.authority.alternativeSignatory.title}
						</div>
					{/if}
				</div>
			{/if}

			{#if doc.signatories.length > 0}
				<div class="text-xs text-gray-500 dark:text-gray-500">
					{$i18n.t('Signatories')}: {doc.signatories.map((s) => `${s.name} (${s.title})`).join(', ')}
				</div>
			{/if}

			<!-- Populate fields -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<div class="text-sm font-medium mb-2">{$i18n.t('Populate fields')}</div>

				{#if doc.missingRequired.length > 0}
					<div class="text-xs text-amber-600 dark:text-amber-400 mb-2">
						{$i18n.t('Missing required fields')}: {doc.missingRequired.map(humanizeField).join(', ')}
					</div>
				{/if}

				{#if Object.keys(populateValues).length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-600">
						{$i18n.t('No fields to populate.')}
					</div>
				{:else}
					<div class="grid grid-cols-2 gap-2">
						{#each Object.keys(populateValues) as path (path)}
							<label class="text-xs text-gray-500 dark:text-gray-400">
								{humanizeField(path)}
								{#if doc.missingRequired.includes(path)}<span class="text-red-500">*</span>{/if}
								<input
									type="text"
									bind:value={populateValues[path]}
									class="block w-full mt-1 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden"
								/>
							</label>
						{/each}
					</div>

					{#if populateError}
						<div class="text-xs text-red-500 mt-2">{populateError}</div>
					{/if}

					<div class="flex justify-end mt-2">
						<button
							type="button"
							disabled={populateSubmitting}
							class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
							on:click={submitPopulate}
						>
							{#if populateSubmitting}
								<Spinner className="size-3.5" />
							{:else}
								{$i18n.t('Save fields')}
							{/if}
						</button>
					</div>
				{/if}
			</div>

			<!-- Natural-language edit -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<div class="text-sm font-medium mb-2">{$i18n.t('Edit instruction')}</div>
				<textarea
					bind:value={instruction}
					rows="2"
					placeholder={$i18n.t('Describe the change in plain language, e.g. "Change the client to ABC Corp."')}
					class="block w-full rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 text-sm outline-hidden resize-none"
				></textarea>

				{#if instructionError}
					<div class="text-xs text-red-500 mt-2">{instructionError}</div>
					{#if instructionBlockers.length > 0}
						<ul class="text-xs text-red-500 list-disc pl-4 mt-1">
							{#each instructionBlockers as b}
								<li>{b.message}</li>
							{/each}
						</ul>
					{/if}
				{/if}

				<div class="flex justify-end mt-2">
					<button
						type="button"
						disabled={instructionSubmitting || !instruction.trim()}
						class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={submitInstruction}
					>
						{#if instructionSubmitting}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Apply')}
						{/if}
					</button>
				</div>
			</div>

			<!-- Validate -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<div class="flex items-center justify-between mb-2">
					<div class="text-sm font-medium">{$i18n.t('Validate')}</div>
					<button
						type="button"
						disabled={validating}
						class="text-xs px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition disabled:opacity-50"
						on:click={runValidate}
					>
						{#if validating}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Run validation')}
						{/if}
					</button>
				</div>

				{#if validationError}
					<div class="text-xs text-red-500">{validationError}</div>
				{:else if validationResult}
					<div class="flex items-center gap-2 mb-1.5">
						<Badge type={validationResult.valid ? 'success' : 'error'} content={validationResult.valid ? $i18n.t('Valid') : $i18n.t('Invalid')} />
						<Badge type={READINESS_BADGE[validationResult.readiness] ?? 'muted'} content={validationResult.readiness} />
					</div>
					{#if validationResult.blockers.length > 0}
						<ul class="text-xs text-red-500 list-disc pl-4 space-y-0.5">
							{#each validationResult.blockers as b}
								<li>{b.message}{b.field ? ` (${b.field})` : ''}</li>
							{/each}
						</ul>
					{/if}
					{#if validationResult.warnings.length > 0}
						<ul class="text-xs text-amber-600 dark:text-amber-400 list-disc pl-4 space-y-0.5 mt-1">
							{#each validationResult.warnings as w}
								<li>{w}</li>
							{/each}
						</ul>
					{/if}
				{/if}
			</div>

			<!-- Render / export -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<div class="text-sm font-medium mb-2">{$i18n.t('Render / Export')}</div>
				<div class="flex flex-wrap items-center gap-2">
					<select
						bind:value={renderFormat}
						class="text-xs rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1.5 outline-hidden"
					>
						<option value="md">{$i18n.t('Markdown')}</option>
						<option value="html">{$i18n.t('HTML')}</option>
						<option value="docx">{$i18n.t('Word (.docx)')}</option>
						<option value="pdf">{$i18n.t('PDF')}</option>
					</select>
					<label class="flex items-center gap-1.5 text-xs">
						<input type="checkbox" bind:checked={renderSignatureCopy} />
						{$i18n.t('Signature copy')}
					</label>
					<button
						type="button"
						disabled={rendering}
						class="ml-auto text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={runRender}
					>
						{#if rendering}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Render')}
						{/if}
					</button>
				</div>
				{#if renderError}
					<div class="text-xs text-red-500 mt-2">{renderError}</div>
				{/if}
			</div>

			<!-- Prepare for signature -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<div class="flex items-center justify-between">
					<div class="text-sm font-medium">{$i18n.t('Prepare for Signature')}</div>
					<button
						type="button"
						disabled={transitioning}
						class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition disabled:opacity-50"
						on:click={prepareForSignature}
					>
						{#if transitioning}
							<Spinner className="size-3.5" />
						{:else}
							{$i18n.t('Prepare for Signature')}
						{/if}
					</button>
				</div>
				{#if transitionError}
					<div class="text-xs text-red-500 mt-2">
						{transitionReason ?? transitionError}
					</div>
				{/if}
			</div>

			<!-- Audit trail -->
			<div class="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
				<button
					type="button"
					class="flex items-center justify-between w-full text-sm font-medium"
					on:click={toggleAudit}
				>
					{$i18n.t('Audit trail')}
					<span class="text-xs text-gray-400 dark:text-gray-600">{showAudit ? '▲' : '▼'}</span>
				</button>

				{#if showAudit}
					<div class="mt-2">
						{#if auditLoading}
							<div class="flex justify-center py-4">
								<Spinner className="size-4" />
							</div>
						{:else if (auditEvents ?? []).length === 0}
							<div class="text-xs text-gray-400 dark:text-gray-600">{$i18n.t('No audit events yet.')}</div>
						{:else}
							<ul class="flex flex-col gap-1.5">
								{#each auditEvents ?? [] as event, idx (idx)}
									<li class="text-xs border-l-2 border-gray-200 dark:border-gray-800 pl-2">
										<div class="text-gray-700 dark:text-gray-300">
											{event.eventType} · {event.actor}
										</div>
										<div class="text-gray-400 dark:text-gray-600">
											{dayjs(event.createdAt).format('LLL')} · {$i18n.t('Revision')} {event.revisionNumber}
										</div>
										{#if event.details}
											<div class="text-gray-500 dark:text-gray-500">{event.details}</div>
										{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<Modal bind:show={showRenderPreview} size="lg">
	<div class="p-4">
		<div class="text-base font-medium mb-3">{$i18n.t('Preview')}</div>
		{#if renderPreview}
			<div class="max-h-[65vh] overflow-y-auto prose prose-sm dark:prose-invert max-w-none">
				{#if renderPreview.format === 'md'}
					{@html marked.parse(renderPreview.content)}
				{:else}
					{@html renderPreview.content}
				{/if}
			</div>
		{/if}
		<div class="flex justify-end mt-3">
			<button
				type="button"
				class="text-xs px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
				on:click={() => (showRenderPreview = false)}
			>
				{$i18n.t('Close')}
			</button>
		</div>
	</div>
</Modal>

<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	const i18n = getContext<Writable<i18nType>>('i18n');

	import { settings, user } from '$lib/stores';
	import Markdown from './Markdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';

	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Printer from '$lib/components/icons/Printer.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Share from '$lib/components/icons/Share.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	import {
		copyToClipboard as _copyToClipboard,
		formatMessageTimestampFull
	} from '$lib/utils';
	import { getOutputText } from './structuredOutput';
	import { exportVesqorReport } from '$lib/apis/vesqor';
	import { getUserSettings, updateUserSettings } from '$lib/apis/users';
	import { parseVqMeta, deriveReportTitle, reportBody, type ReportMeta } from '$lib/utils/vesqor-report';

	type ExportFormat = 'pdf' | 'docx' | 'md' | 'html' | 'json';

	const EXPORT_FORMATS: { id: ExportFormat; label: string }[] = [
		{ id: 'pdf', label: 'PDF' },
		{ id: 'docx', label: 'DOCX' },
		{ id: 'md', label: 'Markdown' },
		{ id: 'html', label: 'HTML' },
		{ id: 'json', label: 'JSON' }
	];
	const FORMAT_MIME: Record<Exclude<ExportFormat, 'pdf' | 'docx'>, string> = {
		md: 'text/markdown',
		html: 'text/html',
		json: 'application/json'
	};

	export let messageId: string;
	export let chatId: string;
	export let content: string;
	export let message: Record<string, any> = {};
	export let done = true;
	export let model: any = null;
	export let readOnly = false;
	export let compactPreview = false;
	export let editCodeBlock = true;
	export let topPadding = false;
	export let regenerateResponse: (message: any, prompt?: string | null) => void = () => {};

	$: meta = parseVqMeta(message) as ReportMeta | null;
	$: body = reportBody(message);
	$: title =
		meta?.title ??
		deriveReportTitle(body || content) ??
		$i18n.t('VESQOR MEGA AI Report');
	$: preparedBy = `${$i18n.t('Prepared by')}: VESQOR MEGA AI${model?.name ? ` — ${model.name}` : ''}`;
	$: preparedFor = `${$i18n.t('Prepared for')}: ${$user?.name || $user?.email || $i18n.t('Guest')}`;

	let reportElement: HTMLDivElement;
	let isExporting = false;
	let copied = false;
	let shareCopied = false;
	let preferredFormat: ExportFormat = 'pdf';
	let showFormatMenu = false;

	onMount(async () => {
		try {
			const userSettings = await getUserSettings(localStorage.token);
			const savedFormat = userSettings?.vesqor?.export_format;
			if (EXPORT_FORMATS.some((f) => f.id === savedFormat)) {
				preferredFormat = savedFormat;
			}
		} catch (error) {
			console.error('Failed to load export format preference', error);
		}
	});

	const getMarkdown = (): string => {
		const raw = body.trim() || content.trim() || '';
		return raw
			.replace(/<details[\s\S]*?<\/details>/g, '')
			.trim();
	};

	const visibleContent = (): string => getOutputText(message?.output) || getMarkdown();

	const copyReport = async () => {
		const text = visibleContent();
		const res = await _copyToClipboard(text, null, false);
		if (res) {
			copied = true;
			setTimeout(() => (copied = false), 2000);
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	const shareReport = async () => {
		try {
			// Prefer the standard Open WebUI chat share link (the report lives in this chat).
			const { shareChatById } = await import('$lib/apis/chats');
			const shared = await shareChatById(localStorage.token, chatId);
			const shareUrl = `${window.location.origin}/s/${shared.share_id}`;
			const res = await _copyToClipboard(shareUrl, null, false);
			if (res) {
				shareCopied = true;
				setTimeout(() => (shareCopied = false), 2000);
				toast.success($i18n.t('Share link copied to clipboard.'));
			}
		} catch (err) {
			console.error(err);
			// Fallback: copy the raw markdown so the report can be pasted anywhere.
			await copyReport();
		}
	};

	const exportViaServer = async (format: ExportFormat): Promise<boolean> => {
		try {
			const res = await exportVesqorReport(localStorage.token, {
				format,
				...(meta?.title ? { title: meta.title } : {}),
				body: getMarkdown(),
				chat_id: chatId,
				chat_title: title,
				message_id: messageId
			});
			if (!res || !res.ok) return false;

			const blob = await res.blob();
			const safeTitle = (title || 'vesqor-report').replace(/[^\p{L}\p{N} _-]+/gu, '').slice(0, 80) || 'vesqor-report';
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${safeTitle}.${format}`;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);
			return true;
		} catch (error) {
			console.error(`Server export ${format} failed, falling back to client-side`, error);
			return false;
		}
	};

	const pdfClientFallback = async () => {
		if (!reportElement) return;
		// Fallback: client-side HTML → canvas → PDF.
		const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
			import('jspdf'),
			import('html2canvas-pro')
		]);

		const isDarkMode = document.documentElement.classList.contains('dark');
		const virtualWidth = 800;

		const clonedElement = reportElement.cloneNode(true) as HTMLElement;
		clonedElement.classList.add('text-black');
		clonedElement.classList.add('dark:text-white');
		clonedElement.style.width = `${virtualWidth}px`;
		clonedElement.style.position = 'absolute';
		clonedElement.style.left = '-9999px';
		clonedElement.style.top = '0';
		clonedElement.style.height = 'auto';
		clonedElement.style.maxHeight = 'none';
		clonedElement.style.overflow = 'visible';
		document.body.appendChild(clonedElement);

		await new Promise((r) => requestAnimationFrame(r));

		const canvas = await html2canvas(clonedElement, {
			backgroundColor: isDarkMode ? '#000' : '#fff',
			useCORS: true,
			scale: 2,
			width: virtualWidth
		});

		document.body.removeChild(clonedElement);

		const pdf = new jsPDF('p', 'mm', 'a4');
		const pageWidthMM = 210;
		const pageHeightMM = 297;
		const pxPerPDFMM = canvas.width / pageWidthMM;
		const pagePixelHeight = Math.floor(pxPerPDFMM * pageHeightMM);

		let offsetY = 0;
		let page = 0;
		while (offsetY < canvas.height) {
			const sliceHeight = Math.min(pagePixelHeight, canvas.height - offsetY);
			const pageCanvas = document.createElement('canvas');
			pageCanvas.width = canvas.width;
			pageCanvas.height = sliceHeight;
			const ctx = pageCanvas.getContext('2d');
			if (!ctx) break;
			ctx.fillStyle = isDarkMode ? '#000' : '#fff';
			ctx.fillRect(0, 0, canvas.width, sliceHeight);
			ctx.drawImage(canvas, 0, offsetY, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);

			const imgData = pageCanvas.toDataURL('image/jpeg', 0.92);
			const imgHeightMM = (sliceHeight * pageWidthMM) / canvas.width;
			if (page > 0) pdf.addPage();
			if (isDarkMode) {
				pdf.setFillColor(0, 0, 0);
				pdf.rect(0, 0, pageWidthMM, pageHeightMM, 'F');
			}
			pdf.addImage(imgData, 'JPEG', 0, 0, pageWidthMM, imgHeightMM);
			offsetY += sliceHeight;
			page++;
		}

		const safeTitle = (title || 'vesqor-report').replace(/[^\p{L}\p{N} _-]+/gu, '').slice(0, 80) || 'vesqor-report';
		pdf.save(`${safeTitle}.pdf`);
	};

	const docxClientFallback = async () => {
		const JSZip = (await import('jszip')).default;
		const markdown = getMarkdown();
		const paragraphs = markdown
			.split(/\n{2,}/)
			.map((block) => block.replace(/\n/g, ' ').trim())
			.filter(Boolean);

		const xmlEsc = (s: string) =>
			s
				.replace(/&/g, '&amp;')
				.replace(/</g, '&lt;')
				.replace(/>/g, '&gt;')
				.replace(/"/g, '&quot;');

		const bodyXml = paragraphs
			.map((p) => `  <w:p><w:r><w:t xml:space="preserve">${xmlEsc(p)}</w:t></w:r></w:p>`)
			.join('\n');

		const documentXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:rPr><w:b/></w:rPr></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">${xmlEsc(title || 'VESQOR MEGA AI Report')}</w:t></w:r></w:p>
${bodyXml}
  </w:body>
</w:document>`;

		const zip = new JSZip();
		zip.file(
			'[Content_Types].xml',
			`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`
		);
		zip.file(
			'_rels/.rels',
			`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`
		);
		zip.file('word/document.xml', documentXml);

		const blob = await zip.generateAsync({ type: 'blob' });
		const fileSaver = await import('file-saver');
		const safeTitle = (title || 'vesqor-report').replace(/[^\p{L}\p{N} _-]+/gu, '').slice(0, 80) || 'vesqor-report';
		fileSaver.saveAs(blob, `${safeTitle}.docx`);
	};

	const blobClientFallback = async (fmt: Exclude<ExportFormat, 'pdf' | 'docx'>) => {
		const safeTitle = (title || 'vesqor-report').replace(/[^\p{L}\p{N} _-]+/gu, '').slice(0, 80) || 'vesqor-report';
		const fileSaver = await import('file-saver');

		let content: string;
		if (fmt === 'json') {
			content = JSON.stringify({ title, meta, body: getMarkdown() }, null, 2);
		} else if (fmt === 'html') {
			const inner = reportElement ? reportElement.outerHTML : `<pre>${getMarkdown()}</pre>`;
			content = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title || 'VESQOR MEGA AI Report'}</title></head><body>${inner}</body></html>`;
		} else {
			content = getMarkdown();
		}

		const blob = new Blob([content], { type: FORMAT_MIME[fmt] });
		fileSaver.saveAs(blob, `${safeTitle}.${fmt}`);
	};

	const downloadReport = async (fmt: ExportFormat) => {
		isExporting = true;
		try {
			// Preferred: brain export engine (RPT-3) via backend proxy.
			const serverResult = await exportViaServer(fmt);
			if (serverResult) {
				toast.success($i18n.t('Saved to Library'));
				return;
			}

			if (fmt === 'pdf') {
				await pdfClientFallback();
			} else if (fmt === 'docx') {
				await docxClientFallback();
			} else {
				await blobClientFallback(fmt);
			}
		} catch (error) {
			console.error(`Error generating ${fmt.toUpperCase()}`, error);
			toast.error($i18n.t('Failed to generate {{format}}', { format: fmt.toUpperCase() }));
		} finally {
			isExporting = false;
		}
	};

	const selectExportFormat = async (fmt: ExportFormat) => {
		preferredFormat = fmt;
		showFormatMenu = false;
		try {
			await updateUserSettings(localStorage.token, { ui: $settings, vesqor: { export_format: fmt } });
		} catch (error) {
			console.error('Failed to save export format preference', error);
		}
		await downloadReport(fmt);
	};

	const printReport = async () => {
		if (!reportElement) return;
		try {
			const clonedElement = reportElement.cloneNode(true) as HTMLElement;
			clonedElement.style.width = '100%';
			clonedElement.style.maxHeight = 'none';
			clonedElement.style.overflow = 'visible';

			const printWindow = window.open('', '_blank', 'width=900,height=1200');
			if (!printWindow) {
				toast.error($i18n.t('Popup blocked — allow popups to print'));
				return;
			}

			const isDarkMode = document.documentElement.classList.contains('dark');
			const style = `
				* { box-sizing: border-box; }
				body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937; background: #fff; margin: 0; padding: 40px; }
				h1, h2, h3 { line-height: 1.25; }
				h1 { font-size: 24px; margin: 0 0 8px; }
				h2 { font-size: 18px; margin-top: 24px; }
				table { border-collapse: collapse; width: 100%; margin: 12px 0; }
				th, td { border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; font-size: 13px; }
				th { background: #f3f4f6; }
				code { background: #f3f4f6; padding: 1px 4px; border-radius: 4px; font-size: 12px; }
				pre { background: #f8f9fa; border: 1px solid #e5e7eb; padding: 12px; border-radius: 6px; overflow-x: auto; }
				pre code { background: none; padding: 0; }
				blockquote { border-left: 3px solid #d1d5db; margin: 12px 0; padding-left: 12px; color: #6b7280; }
				.meta { color: #6b7280; font-size: 12px; margin: 4px 0 20px; }
				@media print { body { padding: 0; } }
			`;

			const styleTag = '<' + 'style>';
			printWindow.document.write(`<!DOCTYPE html><html><head><title>${title || 'VESQOR MEGA AI Report'}</title>${styleTag}${style}</style></head><body>${clonedElement.outerHTML}</body></html>`);
			printWindow.document.close();
			await new Promise((r) => setTimeout(r, 300));
			printWindow.focus();
			printWindow.print();
			// Keep the popup open so the user can save as PDF from the print dialog.
		} catch (error) {
			console.error('Error printing report', error);
			toast.error($i18n.t('Failed to print report'));
		}
	};

	const regenerate = () => {
		regenerateResponse(message);
	};
</script>

<div class="w-full flex flex-col gap-3" data-report-view>
	<!-- Document header -->
	<div
		class="w-full border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden bg-white dark:bg-gray-850"
	>
		<!-- Header band -->
		<div
			class="px-4 sm:px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gradient-to-br from-gray-50 to-white dark:from-gray-800 dark:to-gray-850"
		>
			<div class="flex items-center gap-2 mb-1">
				<Sparkles className="size-4 text-emerald-600 dark:text-emerald-400" />
				<span class="text-[0.7rem] font-semibold tracking-[0.14em] uppercase text-emerald-700 dark:text-emerald-400">
					VESQOR MEGA AI
				</span>
			</div>
			<h1 class="text-xl sm:text-2xl font-bold text-gray-900 dark:text-gray-50 leading-snug">{title}</h1>

			<div class="mt-2 text-xs text-gray-500 dark:text-gray-400 space-y-0.5">
				<div>{preparedFor}</div>
				<div>{preparedBy}</div>
				{#if message?.timestamp}
					<div>{formatMessageTimestampFull(message.timestamp)}</div>
				{/if}
			</div>

			{#if meta && (meta.confidenceScore !== undefined || meta.notes)}
				<div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-gray-500 dark:text-gray-400">
					{#if meta.confidenceScore !== undefined}
						<span class="inline-flex items-center gap-1">
							<span class="font-medium text-gray-600 dark:text-gray-300">{$i18n.t('Confidence')}:</span>
							{Math.round(meta.confidenceScore * 100)}%
						</span>
					{/if}
					{#if meta.source}
						<span class="inline-flex items-center gap-1">
							<span class="font-medium text-gray-600 dark:text-gray-300">{$i18n.t('Source')}:</span>
							{meta.source}
						</span>
					{/if}
					{#if meta.notes}
						<span class="italic">{meta.notes}</span>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Report body -->
		<div class="px-4 sm:px-6 py-4">
			<div bind:this={reportElement} class="report-document markdown-prose w-full max-w-none">
				<Markdown
					id={`${messageId}-report-view`}
					content={body || content}
					{model}
					{done}
					save={!readOnly}
					preview={!readOnly}
					{compactPreview}
					{editCodeBlock}
					{topPadding}
					sourceIds={[]}
				/>
			</div>
		</div>

		<!-- Action bar -->
		<div
			class="px-4 sm:px-6 py-2.5 border-t border-gray-200 dark:border-gray-700 flex items-center gap-1 flex-wrap bg-gray-50/60 dark:bg-gray-800/40"
		>
			<Dropdown bind:show={showFormatMenu} align="start" sideOffset={6}>
				<Tooltip content={$i18n.t('Download format')} placement="top">
					<button
						type="button"
						class="p-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition text-gray-600 dark:text-gray-400 flex items-center gap-0.5"
						aria-label={$i18n.t('Download format')}
						disabled={isExporting}
					>
						{#if isExporting}
							<Spinner className="size-4" />
						{:else}
							<ArrowDownTray className="size-4" />
						{/if}
						<ChevronDown className="size-2.5" strokeWidth="2.5" />
					</button>
				</Tooltip>

				<div slot="content">
					<DropdownMenu className="min-w-[150px]">
						{#each EXPORT_FORMATS as format (format.id)}
							<button
								type="button"
								on:click={() => selectExportFormat(format.id)}
							>
								<span class="flex-1 text-left">{$i18n.t(format.label)}</span>
								{#if preferredFormat === format.id}
									<Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
								{/if}
							</button>
						{/each}
					</DropdownMenu>
				</div>
			</Dropdown>

			<Tooltip content={$i18n.t('Print')} placement="top">
				<button
					type="button"
					class="p-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition text-gray-600 dark:text-gray-400"
					aria-label={$i18n.t('Print')}
					on:click={printReport}
				>
					<Printer className="size-4" />
				</button>
			</Tooltip>

			<Tooltip content={$i18n.t('Copy')} placement="top">
				<button
					type="button"
					class="p-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition text-gray-600 dark:text-gray-400"
					aria-label={$i18n.t('Copy')}
					on:click={copyReport}
				>
					{#if copied}
						<Check className="size-4 text-emerald-600 dark:text-emerald-400" />
					{:else}
						<Clipboard className="size-4" />
					{/if}
				</button>
			</Tooltip>

			<Tooltip content={$i18n.t('Share')} placement="top">
				<button
					type="button"
					class="p-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition text-gray-600 dark:text-gray-400"
					aria-label={$i18n.t('Share')}
					on:click={shareReport}
				>
					{#if shareCopied}
						<Check className="size-4 text-emerald-600 dark:text-emerald-400" />
					{:else}
						<Share className="size-4" />
					{/if}
				</button>
			</Tooltip>

			{#if !readOnly && (message?.done ?? false)}
				<Tooltip content={$i18n.t('Regenerate')} placement="top">
					<button
						type="button"
						class="p-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition text-gray-600 dark:text-gray-400"
						aria-label={$i18n.t('Regenerate')}
						on:click={regenerate}
					>
						<ArrowPath className="size-4" />
					</button>
				</Tooltip>
			{/if}

			<span class="ml-auto text-[0.65rem] text-gray-400 dark:text-gray-600 select-none">
				VESQOR MEGA AI
			</span>
		</div>
	</div>
</div>

<style>
	/* Ensure the report document renders with real typography, not chat bubble styles. */
	.report-document :global(p) {
		margin: 0.6rem 0;
		line-height: 1.65;
	}
	.report-document :global(h1) {
		font-size: 1.5rem;
		margin: 1.2rem 0 0.5rem;
	}
	.report-document :global(h2) {
		font-size: 1.25rem;
		margin: 1.1rem 0 0.45rem;
	}
	.report-document :global(h3) {
		font-size: 1.05rem;
		margin: 1rem 0 0.4rem;
	}
	.report-document :global(table) {
		width: 100%;
		border-collapse: collapse;
		margin: 0.8rem 0;
	}
	.report-document :global(th),
	.report-document :global(td) {
		border: 1px solid var(--color-gray-300, #d1d5db);
		padding: 0.4rem 0.6rem;
		text-align: left;
		font-size: 0.875rem;
	}
	.report-document :global(th) {
		background: var(--color-gray-100, #f3f4f6);
		font-weight: 600;
	}
</style>

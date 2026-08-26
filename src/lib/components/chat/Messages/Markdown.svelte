<script context="module">
	import { marked } from 'marked';

	import markedExtension from '$lib/utils/marked/extension';
	import markedKatexExtension from '$lib/utils/marked/katex-extension';
	import { disableSingleTilde } from '$lib/utils/marked/strikethrough-extension';
	import { mentionExtension } from '$lib/utils/marked/mention-extension';
	import colonFenceExtension from '$lib/utils/marked/colon-fence-extension';
	import footnoteExtension from '$lib/utils/marked/footnote-extension';
	import citationExtension from '$lib/utils/marked/citation-extension';

	const options = {
		throwOnError: false
	};

	marked.use(markedKatexExtension(options));
	marked.use(markedExtension(options));
	marked.use(citationExtension(options));
	marked.use(footnoteExtension(options));
	marked.use(colonFenceExtension(options));
	marked.use(disableSingleTilde);
	marked.use({
		extensions: [
			mentionExtension({ triggerChar: '@' }),
			mentionExtension({ triggerChar: '#' }),
			mentionExtension({ triggerChar: '$' })
		]
	});
</script>

<script>
	import { onDestroy } from 'svelte';
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';

	export let id = '';
	export let chatId = '';
	export let messageId = '';
	export let content;
	export let done = true;
	export let model = null;
	export let save = false;
	export let preview = false;
	export let compactPreview = false;

	export let paragraphTag = 'p';
	export let editCodeBlock = true;
	export let topPadding = false;
	export let allowEmbeds = true;

	export let sourceIds = [];
	export let onSave = () => {};
	export let onUpdate = () => {};

	export let onPreview = () => {};

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};
	export let onToolCallResolved = () => {};

	/**
	 * Strip stray backslash-escapes that some models emit in Markdown output.
	 * Symptom (2026-08-21, chat.vesqorai.com): reports rendered raw pipe
	 * characters instead of a table. Root cause: `\|` inside GFM table rows,
	 * a trailing `\` before newlines, and `\- ` list markers. A single
	 * backslash at the end of the table delimiter row (`|---|---|---|\`)
	 * makes marked fall back to a plain paragraph — the whole table leaks as
	 * literal pipes. Mirror of lib/brain/parse.ts sanitizeReportMarkdown so
	 * already-stored messages render correctly without re-generation.
	 */
	const sanitizeReportMarkdown = (report) => {
		if (!report) return report;
		return report
			.replace(/\\\|/g, '|')
			.replace(/(\|(?:-+\|)+)\| /g, '$1\n| ')
			.replace(/\\\n/g, '\n')
			.replace(/([^A-Za-z0-9\s])\\- /g, '$1\n- ')
			.replace(/([^A-Za-z0-9\s])\\\+ /g, '$1\n+ ')
			.replace(/([^A-Za-z0-9\s])\\\* /g, '$1\n* ');
	};

	let tokens = [];
	let pendingUpdate = null;
	let lastContent = '';
	let lastParsedContent = '';

	const parseTokens = () => {
		if (content === lastContent) return;
		lastContent = content;

		const processed = replaceTokens(processResponseContent(sanitizeReportMarkdown(content)), model?.name, $user?.name);
		if (processed === lastParsedContent) return;
		lastParsedContent = processed;

		tokens = marked.lexer(processed);
	};

	const updateHandler = (content) => {
		if (content) {
			if (done) {
				cancelAnimationFrame(pendingUpdate);
				pendingUpdate = null;
				parseTokens();
			} else if (!pendingUpdate) {
				pendingUpdate = requestAnimationFrame(() => {
					pendingUpdate = null;
					parseTokens();
				});
			}
		}
	};

	$: updateHandler(content);

	// Throttle parsing to once per animation frame while streaming
	onDestroy(() => {
		cancelAnimationFrame(pendingUpdate);
	});
</script>

{#key id}
	<MarkdownTokens
		{tokens}
		{id}
		{chatId}
		{messageId}
		{done}
		{save}
		{preview}
		{compactPreview}
		{paragraphTag}
		{editCodeBlock}
		{sourceIds}
		{topPadding}
		{allowEmbeds}
		{onTaskClick}
		{onSourceClick}
		{onToolCallResolved}
		{onSave}
		{onUpdate}
		{onPreview}
	/>
{/key}

<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Chat from '$lib/components/chat/Chat.svelte';
	import { page } from '$app/stores';

	onMount(() => {
		if ($page.url.searchParams.get('error')) {
			toast.error($page.url.searchParams.get('error') || 'An unknown error occurred.');
		}
	});
</script>

<!-- VESQOR: chat page renders the stock Open WebUI Chat component directly.
     The glass-scoped wrapper (vesqor-chat-root) was removed 2026-09-11 — the
     chat UI is stock Open WebUI; only the living matrix background stays. -->
<Chat />

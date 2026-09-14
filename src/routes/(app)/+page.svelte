<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Chat from '$lib/components/chat/Chat.svelte';
	import MatrixRainStage from '$lib/components/admin/MatrixRainStage.svelte';
	import { page } from '$app/stores';

	onMount(() => {
		if ($page.url.searchParams.get('error')) {
			toast.error($page.url.searchParams.get('error') || 'An unknown error occurred.');
		}
	});
</script>

<!-- VESQOR: живой Matrix rain только на главной (owner 2026-09-14). -->
<MatrixRainStage />
<div class="vesqor-typing-matte" aria-hidden="true"></div>

<!-- VESQOR: chat root wrapper — the matte glass styling in custom.css is
     scoped to this container so admin pages, modals and dialogs keep their
     normal opaque backgrounds. display:contents keeps it as a CSS-scoping
     anchor WITHOUT creating a layout box, so Chat's h-full / flex height
     chain from the parent h-screen stays intact. -->
<div class="vesqor-chat-root" style="display: contents">
	<Chat />
</div>

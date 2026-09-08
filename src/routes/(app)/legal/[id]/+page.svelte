<script lang="ts">
	import { page } from '$app/stores';
	import { showSidebar } from '$lib/stores';
	import LegalDocumentDetail from '$lib/components/legal/LegalDocumentDetail.svelte';
</script>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex-1 max-h-full overflow-y-auto">
		<LegalDocumentDetail id={$page.params.id} />
	</div>
</div>

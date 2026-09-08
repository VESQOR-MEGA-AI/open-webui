<script lang="ts">
	import { showSidebar } from '$lib/stores';
	import LegalDepartment from '$lib/components/legal/LegalDepartment.svelte';
</script>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex-1 max-h-full overflow-y-auto">
		<LegalDepartment />
	</div>
</div>

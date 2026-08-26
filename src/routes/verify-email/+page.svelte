<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { goto } from '$app/navigation';

	let status: 'loading' | 'success' | 'error' = 'loading';
	let message = '';

	onMount(async () => {
		const token = $page.url.searchParams.get('token');
		if (!token) {
			status = 'error';
			message = 'Missing verification token.';
			return;
		}

		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/auths/verify-email?token=${encodeURIComponent(token)}`);
			const data = await res.json();
			if (res.ok && data.verified) {
				status = 'success';
				message = data.detail ?? 'Email verified. You can now sign in.';
			} else {
				status = 'error';
				message = data.detail ?? 'Verification failed.';
			}
		} catch (err) {
			status = 'error';
			message = 'Verification failed. Please try again.';
		}
	});
</script>

<svelte:head>
	<title>Verify email — VESQOR MEGA AI</title>
</svelte:head>

<div class="flex min-h-screen w-full flex-col items-center justify-center bg-white px-6 text-center dark:bg-black">
	<div class="flex flex-col items-center">
		<img
			id="logo"
			crossorigin="anonymous"
			src="{WEBUI_BASE_URL}/static/favicon.png"
			class="size-24 rounded-full"
			alt="VESQOR MEGA AI logo"
		/>
		{#if status === 'loading'}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">Verifying your email…</div>
			<div class="mt-4 size-6 animate-spin rounded-full border-2 border-gray-300 border-t-black dark:border-t-white" />
		{:else if status === 'success'}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">Email verified ✓</div>
			<p class="mt-2 max-w-md text-sm font-normal text-gray-600 dark:text-gray-400">{message}</p>
			<button
				class="mt-6 rounded-full bg-black px-8 py-2.5 text-sm font-normal text-white transition hover:opacity-90 dark:bg-white dark:text-black"
				on:click={() => goto('/auth')}
			>
				Sign in
			</button>
		{:else}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">Verification failed</div>
			<p class="mt-2 max-w-md text-sm font-normal text-gray-600 dark:text-gray-400">{message}</p>
			<button
				class="mt-6 rounded-full bg-black px-8 py-2.5 text-sm font-normal text-white transition hover:opacity-90 dark:bg-white dark:text-black"
				on:click={() => goto('/auth')}
			>
				Back to sign in
			</button>
		{/if}
	</div>
</div>

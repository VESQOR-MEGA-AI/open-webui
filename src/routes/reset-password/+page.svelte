<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { resetPassword } from '$lib/apis/auths';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let token = '';
	let password = '';
	let confirmPassword = '';

	let submitting = false;
	let status: 'form' | 'success' | 'error' = 'form';
	let message = '';

	onMount(() => {
		token = $page.url.searchParams.get('token') ?? '';
		if (!token) {
			status = 'error';
			message = $i18n.t('Invalid or expired reset link. Please request a new one.');
		}
	});

	const submitHandler = async () => {
		if (submitting) {
			return;
		}

		if (password !== confirmPassword) {
			message = $i18n.t('Passwords do not match.');
			return;
		}

		submitting = true;
		message = '';
		try {
			await resetPassword(token, password);
			status = 'success';
		} catch (error) {
			// The backend distinguishes a dead token from a rejected password;
			// surface its message so the user knows which one to fix.
			message = `${error}`;
		} finally {
			submitting = false;
		}
	};
</script>

<svelte:head>
	<title>Reset password — VESQOR MEGA AI</title>
</svelte:head>

<div
	class="flex min-h-screen w-full flex-col items-center justify-center bg-white px-6 text-center dark:bg-black"
>
	<div class="flex w-full max-w-sm flex-col items-center">
		<img
			id="logo"
			crossorigin="anonymous"
			src="{WEBUI_BASE_URL}/static/favicon.png"
			class="size-24 rounded-full"
			alt="VESQOR MEGA AI logo"
		/>

		{#if status === 'success'}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">
				{$i18n.t('Reset password')}
			</div>
			<p class="mt-2 max-w-md text-sm font-normal text-gray-600 dark:text-gray-400">
				{$i18n.t('Password reset. You can now sign in.')}
			</p>
			<button
				class="mt-6 rounded-full bg-black px-8 py-2.5 text-sm font-normal text-white transition hover:opacity-90 dark:bg-white dark:text-black"
				on:click={() => goto('/auth')}
			>
				{$i18n.t('Sign in')}
			</button>
		{:else if status === 'error'}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">
				{$i18n.t('Reset your password')}
			</div>
			<p class="mt-2 max-w-md text-sm font-normal text-gray-600 dark:text-gray-400">
				{message}
			</p>
			<button
				class="mt-6 rounded-full bg-black px-8 py-2.5 text-sm font-normal text-white transition hover:opacity-90 dark:bg-white dark:text-black"
				on:click={() => goto('/auth')}
			>
				{$i18n.t('Back to sign in')}
			</button>
		{:else}
			<div class="mt-6 text-lg font-normal text-gray-900 dark:text-white">
				{$i18n.t('Reset your password')}
			</div>
			<p class="mt-2 max-w-md text-sm font-normal text-gray-600 dark:text-gray-400">
				{$i18n.t('Enter your new password')}
			</p>

			<form
				class="mt-6 flex w-full flex-col text-black dark:text-white"
				on:submit={(e) => {
					e.preventDefault();
					submitHandler();
				}}
			>
				<div class="text-left">
					<label for="new-password" class="mb-1 block text-sm font-normal">
						{$i18n.t('Password')}
					</label>
					<SensitiveInput
						bind:value={password}
						type="password"
						id="new-password"
						class="my-0.5 w-full bg-transparent text-sm outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-600"
						placeholder={$i18n.t('Enter Your Password')}
						autocomplete="new-password"
						name="new-password"
						screenReader={true}
						required
						aria-required="true"
					/>
				</div>

				<div class="mt-2 text-left">
					<label for="confirm-password" class="mb-1 block text-sm font-normal">
						{$i18n.t('Confirm Password')}
					</label>
					<SensitiveInput
						bind:value={confirmPassword}
						type="password"
						id="confirm-password"
						class="my-0.5 w-full bg-transparent text-sm outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-600"
						placeholder={$i18n.t('Confirm Your Password')}
						autocomplete="new-password"
						name="confirm-password"
						required
						aria-required="true"
					/>
				</div>

				{#if message}
					<p class="mt-3 text-left text-sm font-normal text-red-500">{message}</p>
				{/if}

				<button
					class="mt-5 flex w-full justify-center rounded-full bg-black py-2.5 text-sm font-normal text-white transition hover:opacity-90 disabled:opacity-50 dark:bg-white dark:text-black"
					type="submit"
					disabled={submitting}
				>
					<div class="self-center">{$i18n.t('Reset password')}</div>
					{#if submitting}
						<div class="ml-1.5 self-center">
							<Spinner />
						</div>
					{/if}
				</button>
			</form>

			<button
				class="mt-4 text-sm font-normal text-gray-500 underline transition hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
				type="button"
				on:click={() => goto('/auth')}
			>
				{$i18n.t('Back to sign in')}
			</button>
		{/if}
	</div>
</div>

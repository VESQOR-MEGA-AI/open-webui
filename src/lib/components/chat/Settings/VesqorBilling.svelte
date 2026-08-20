<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import {
		getVesqorBillingStatus,
		createVesqorCheckout,
		createVesqorPortalSession,
		createVesqorCreditTopup
	} from '$lib/apis/vesqor';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import UserSettingRow from './UserSettingRow.svelte';

	const i18n: Writable<any> = getContext('i18n');

	let loading = true;
	let error: string | null = null;
	let status: any = null;
	let redirecting = false;

	const planNames: Record<string, string> = {
		trial: 'Trial',
		monthly: 'Monthly',
		quarterly: 'Quarterly',
		weekly: 'Weekly',
		none: 'None'
	};

	$: planName = planNames[(status?.plan ?? 'none').toLowerCase()] ?? (status?.plan ?? 'None');

	const load = async () => {
		loading = true;
		error = null;
		try {
			status = await getVesqorBillingStatus(localStorage.token);
		} catch (err: any) {
			error = typeof err === 'string' ? err : (err?.detail ?? 'Failed to load billing status');
		} finally {
			loading = false;
		}
	};

	const redirectTo = (url?: string) => {
		if (url) {
			window.location.href = url;
		}
	};

	const upgrade = async () => {
		redirecting = true;
		try {
			const res = await createVesqorCheckout(localStorage.token, 'quarterly');
			redirectTo(res?.url);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to start checkout'));
		} finally {
			redirecting = false;
		}
	};

	const manageSubscription = async () => {
		redirecting = true;
		try {
			const res = await createVesqorPortalSession(localStorage.token);
			redirectTo(res?.url);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to open portal'));
		} finally {
			redirecting = false;
		}
	};

	const buyCredits = async (credits: number) => {
		redirecting = true;
		try {
			const res = await createVesqorCreditTopup(localStorage.token, credits);
			redirectTo(res?.url);
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : (err?.detail ?? 'Failed to start credit purchase'));
		} finally {
			redirecting = false;
		}
	};

	onMount(load);
</script>

<div class="w-full h-full flex flex-col overflow-y-auto">
	{#if loading}
		<div class="flex justify-center py-8">
			<Spinner />
		</div>
	{:else if error}
		<div class="text-xs text-red-500 py-4">{error}</div>
	{:else}
		<UserSettingSection title="Subscription" first>
			<UserSettingRow label="Plan">
				<span class="text-xs font-medium">{planName}</span>
			</UserSettingRow>
			<UserSettingRow label="Status">
				<span class="text-xs font-medium capitalize">{status?.status ?? 'No active plan'}</span>
			</UserSettingRow>
			{#if status?.next_billing_date}
				<UserSettingRow label="Next billing date">
					<span class="text-xs font-medium"
						>{new Date(status.next_billing_date).toLocaleDateString()}</span
					>
				</UserSettingRow>
			{/if}
			<UserSettingRow label="Credits balance">
				<span class="text-xs font-medium">{status?.credits ?? 0}</span>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title="Manage">
			<div class="flex flex-wrap gap-2">
				<button
					class="px-3 py-1.5 text-xs font-medium rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
					disabled={redirecting}
					on:click={upgrade}
				>
					Upgrade
				</button>
				<button
					class="px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-100 dark:bg-gray-800 disabled:opacity-50"
					disabled={redirecting}
					on:click={manageSubscription}
				>
					Manage subscription
				</button>
			</div>
		</UserSettingSection>

		<UserSettingSection title="Buy credits">
			<div class="flex flex-wrap gap-2">
				<button
					class="px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-100 dark:bg-gray-800 disabled:opacity-50"
					disabled={redirecting}
					on:click={() => buyCredits(50)}
				>
					50 credits
				</button>
				<button
					class="px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-100 dark:bg-gray-800 disabled:opacity-50"
					disabled={redirecting}
					on:click={() => buyCredits(150)}
				>
					150 credits
				</button>
			</div>
		</UserSettingSection>
	{/if}
</div>

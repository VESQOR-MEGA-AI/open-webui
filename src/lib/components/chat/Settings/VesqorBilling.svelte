<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';

	import { getVesqorBillingStatus } from '$lib/apis/vesqor';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import UserSettingRow from './UserSettingRow.svelte';

	const i18n: Writable<any> = getContext('i18n');

	let loading = true;
	let error: string | null = null;
	let status: any = null;

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
		<div class="p-3 mb-3 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-xs leading-relaxed">
			<span class="font-semibold text-emerald-700 dark:text-emerald-300">VESQOR is in open BETA.</span>
			<span class="text-emerald-800 dark:text-emerald-200">
				Every registered account gets 1 report per day, free. Payments are paused — you will not be charged.
			</span>
		</div>

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

		<UserSettingSection title="Free during BETA">
			<p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
				Billing is currently disabled. You can run one report per day for free. Once the BETA ends,
				paid plans will open here.
			</p>
		</UserSettingSection>
	{/if}
</div>

<script lang="ts">
	import { WEBUI_NAME } from '$lib/constants';
	import { goto } from '$app/navigation';
</script>

<svelte:head>
	<title>Refund Policy — VESQOR MEGA AI</title>
	<meta name="description" content="VESQOR MEGA AI refund policy: subscription cancellation, non-refundable digital services, EU withdrawal rights, and chargeback rules." />
</svelte:head>

<div class="flex w-full flex-col items-center justify-start bg-gray-50 dark:bg-gray-950 min-h-screen">
	<div class="mt-16 w-full max-w-3xl rounded-lg border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-700 dark:bg-gray-900">
		<div class="mb-6 flex items-center justify-between">
			<h1 class="text-2xl font-bold text-gray-900 dark:text-white">Refund Policy</h1>
			<button
				class="text-xs text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
				on:click={() => goto('/')}
			>
				← Back
			</button>
		</div>
		<p class="mb-6 text-sm text-gray-500 dark:text-gray-400">Effective: August 21, 2026</p>

		<div class="space-y-6 text-sm leading-relaxed text-gray-700 dark:text-gray-300">
			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">1. The service and billing</h2>
				<p>
					{$WEBUI_NAME} (the &quot;Service&quot;) is operated by <strong>VESQOR LLC</strong>, a
					Delaware limited liability company (EIN 39-4773373). Access to the Service requires a
					paid subscription or prepaid credit packs, billed through Stripe. Prices are in U.S.
					dollars. This policy explains when you can cancel and when you can receive a refund, in
					addition to any refund rights that are mandatorily granted by applicable law.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">2. Subscription terms</h2>
				<p>
					New accounts may start with a one-time paid trial (US$1.99 for 7 days of full access),
					after which the subscription continues at <strong>US$29.99 per month</strong> unless
					canceled. Alternatively you may purchase quarterly access at
					<strong>US$59.97 per 3 months</strong>. Subscriptions renew automatically until canceled.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">3. Cancellation</h2>
				<p>
					You can cancel your subscription at any time from the customer portal in your account.
					Cancellation disables automatic renewal: you keep full access until the end of the period
					you already paid for, and you are not charged again. To avoid being charged for the next
					billing period, cancel at least 24 hours before the renewal date. Deleting your account
					does not by itself cancel a subscription — cancel the subscription first.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">4. Refunds</h2>
				<p>
					The Service is a digital service. To the fullest extent permitted by applicable law, all
					purchases are final, non-refundable, and non-exchangeable. A purchase is considered
					consumed as soon as you generate your first report or use any prepaid credit. After any
					credit or report generation has been used, the purchase is no longer eligible for a
					refund. Except where required by applicable law, we do not provide refunds, partial
					refunds, or credits for unused subscription periods, unused features, or if you decide to
					stop using the Service.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">5. European Union consumers</h2>
				<p>
					If you are a resident of the European Union, you have the right to withdraw from a digital
					content purchase within fourteen (14) days without providing a reason or incurring any
					cost. However, this right no longer applies once the digital content has been delivered
					with your express consent and your acknowledgment that you lose your right of withdrawal.
					By completing your purchase you expressly consent to the immediate performance of the
					digital service and confirm that you waive your right of withdrawal once the service is
					made available.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">6. Technical defects</h2>
				<p>
					If you experience a consistent technical defect that materially prevents you from using
					the Service, contact us at <strong>info@vesqor.com</strong> with supporting evidence,
					such as screenshots or other relevant information. We may, at our discretion, provide a
					refund where the defect is verified. In the event of any discrepancy, our server logs and
					system records prevail in determining eligibility.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">7. Chargebacks and disputes</h2>
				<p>
					If you initiate a chargeback or otherwise reverse any payment on your account, we may
					suspend or terminate your access immediately, at our sole discretion, for breach of your
					payment obligations. We reserve the right to dispute any chargeback received, including
					by providing the relevant financial institution with information proving the payment was
					authorized.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">8. Changes to this policy</h2>
				<p>
					We may update this Refund Policy from time to time. Changes will be posted on this page
					with a new effective date. If you do not agree with the changes, you can cancel your
					subscription before they take effect.
				</p>
			</div>

			<div>
				<h2 class="mb-1 font-semibold text-gray-900 dark:text-white">9. Contact</h2>
				<p>
					Questions about this policy: <strong>info@vesqor.com</strong>.
				</p>
			</div>
		</div>
	</div>
</div>

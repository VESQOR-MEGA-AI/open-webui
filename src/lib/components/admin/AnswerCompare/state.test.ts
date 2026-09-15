import { describe, expect, it } from 'vitest';
import type { AnswerRow, ProviderConfig, ProviderAnswers } from '$lib/apis/answer-compare';
import {
	applyFailure,
	applyNotConfigured,
	applyOversized,
	applyProviderConfigs,
	applyResult,
	applyRunState,
	cardPhase,
	describeFailure,
	elapsedSeconds,
	ERROR_COPY,
	initialCards,
	isRetryDisabled,
	PROVIDER_IDS,
	startGeneration,
	UNKNOWN_ERROR_COPY
} from './state';

const answerRow = (overrides: Partial<AnswerRow> = {}): AnswerRow => ({
	id: 'answer-1',
	run_id: 'run-1',
	provider: 'chatgpt',
	revision: 1,
	status: 'complete',
	requested_model: 'gpt-test-1',
	model: 'gpt-test-1-2026-01-01',
	engine_version: null,
	params: { stream: false },
	text: 'the good answer',
	error: null,
	...overrides
});

const config = (overrides: Partial<ProviderConfig> = {}): ProviderConfig => ({
	id: 'chatgpt',
	configured: true,
	missing: [],
	base_url: 'https://api.openai.com/v1',
	model: 'gpt-test-1',
	can_judge: true,
	...overrides
});

/** A card holding a rendered answer — the state every rule below protects. */
const withAnswer = () => applyResult(initialCards(), 'chatgpt', answerRow());

describe('card independence', () => {
	it('leaves the other providers byte-for-byte untouched on a failure', () => {
		const before = initialCards();
		const after = applyFailure(before, 'gemini', { code: 'upstream_error' });

		// Object identity, not deep equality: a transition that rebuilt all three
		// would still pass a toEqual check and be wrong.
		expect(after.chatgpt).toBe(before.chatgpt);
		expect(after.vesqor).toBe(before.vesqor);
		expect(after.gemini).not.toBe(before.gemini);
		expect(after.gemini.failure).toEqual({ code: 'upstream_error' });
	});

	it('leaves the other providers untouched on every transition', () => {
		const before = applyResult(
			applyResult(initialCards(), 'chatgpt', answerRow()),
			'vesqor',
			answerRow({ provider: 'vesqor', text: 'vesqor answer' })
		);

		const transitions = [
			() => startGeneration(before, 'gemini', 1000),
			() => applyNotConfigured(before, 'gemini', ['ANSWER_COMPARE_GEMINI_MODEL']),
			() => applyOversized(before, 'gemini', 100, 400),
			() => applyFailure(before, 'gemini', { code: 'timeout' }),
			() => applyResult(before, 'gemini', answerRow({ provider: 'gemini' })),
			() => applyResult(before, 'gemini', answerRow({ provider: 'gemini', status: 'pending' }))
		];

		for (const transition of transitions) {
			const after = transition();
			expect(after.chatgpt).toBe(before.chatgpt);
			expect(after.vesqor).toBe(before.vesqor);
		}
	});

	it('keeps one provider failing from disturbing a neighbour that is mid-flight', () => {
		const started = startGeneration(initialCards(), 'vesqor', 1000);
		const after = applyFailure(started, 'chatgpt', { code: 'auth' });

		expect(after.vesqor).toBe(started.vesqor);
		expect(after.vesqor.inFlight).toBe(true);
		expect(cardPhase(after.vesqor)).toBe('generating');
	});
});

describe('an answer is removed by nothing but a successful new version', () => {
	it('keeps the text and adds a banner when a retry fails', () => {
		const before = withAnswer();
		const after = applyResult(
			before,
			'chatgpt',
			answerRow({ id: 'answer-2', revision: 2, status: 'failed', text: null, error: { code: 'timeout', message: 'x' } })
		);

		expect(after.chatgpt.answer).toBe(before.chatgpt.answer);
		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(after.chatgpt.failure).toEqual({ code: 'timeout' });
		expect(cardPhase(after.chatgpt)).toBe('failed');
	});

	it('does not clear the existing text when a regeneration starts', () => {
		const before = withAnswer();
		const after = startGeneration(before, 'chatgpt', 5000);

		expect(after.chatgpt.answer).toBe(before.chatgpt.answer);
		expect(after.chatgpt.answer?.text).toBe('the good answer');
		// Regenerating, not Generating: the answer stays rendered underneath.
		expect(cardPhase(after.chatgpt)).toBe('regenerating');
		expect(after.chatgpt.inFlight).toBe(true);
		expect(after.chatgpt.startedAt).toBe(5000);
	});

	it('shows Generating, not Regenerating, when there is no answer yet', () => {
		const after = startGeneration(initialCards(), 'chatgpt', 5000);
		expect(cardPhase(after.chatgpt)).toBe('generating');
	});

	it('clears the previous failure banner but not the answer when retrying', () => {
		const failed = applyFailure(withAnswer(), 'chatgpt', { code: 'timeout' });
		const retrying = startGeneration(failed, 'chatgpt', 9000);

		expect(retrying.chatgpt.failure).toBeNull();
		expect(retrying.chatgpt.answer?.text).toBe('the good answer');
	});

	it('replaces the answer only with a newer complete one', () => {
		const before = withAnswer();
		const after = applyResult(
			before,
			'chatgpt',
			answerRow({ id: 'answer-3', revision: 3, text: 'a better answer' })
		);

		expect(after.chatgpt.answer?.text).toBe('a better answer');
		expect(after.chatgpt.answer?.revision).toBe(3);
		expect(after.chatgpt.failure).toBeNull();
		expect(cardPhase(after.chatgpt)).toBe('complete');
	});

	it('keeps the rendered answer when a stored run reports only a failed attempt', () => {
		const before = withAnswer();
		const answers: ProviderAnswers[] = [
			{
				provider: 'chatgpt',
				current: answerRow(),
				latest_attempt: answerRow({ id: 'answer-2', revision: 2, status: 'failed', text: null, error: { code: 'stale', message: 'x' } })
			}
		];

		const after = applyRunState(before, answers);

		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(after.chatgpt.failure).toEqual({ code: 'stale' });
		expect(cardPhase(after.chatgpt)).toBe('failed');
	});

	it('stays in flight when the stored latest attempt is still pending', () => {
		const before = withAnswer();
		const answers: ProviderAnswers[] = [
			{
				provider: 'chatgpt',
				current: answerRow(),
				latest_attempt: answerRow({ id: 'answer-2', revision: 2, status: 'pending', text: null })
			}
		];

		const after = applyRunState(before, answers);

		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(cardPhase(after.chatgpt)).toBe('regenerating');
	});

	it('keeps the rendered answer when the server reports no complete answer at all', () => {
		// The reload path. `current` is null because the only stored attempt failed;
		// the answer already on screen must survive being told that.
		const before = withAnswer();
		const answers: ProviderAnswers[] = [
			{
				provider: 'chatgpt',
				current: null,
				latest_attempt: answerRow({ id: 'answer-2', revision: 2, status: 'failed', text: null, error: { code: 'timeout', message: 'x' } })
			}
		];

		const after = applyRunState(before, answers);

		expect(after.chatgpt.answer).toBe(before.chatgpt.answer);
		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(after.chatgpt.failure).toEqual({ code: 'timeout' });
	});

	it('keeps the rendered answer when the server reports nothing for the provider', () => {
		const before = withAnswer();
		const answers: ProviderAnswers[] = [{ provider: 'chatgpt', current: null, latest_attempt: null }];

		const after = applyRunState(before, answers);

		expect(after.chatgpt.answer).toBe(before.chatgpt.answer);
		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(cardPhase(after.chatgpt)).toBe('complete');
	});

	it('keeps the rendered answer while the server reports only a pending attempt', () => {
		const before = withAnswer();
		const answers: ProviderAnswers[] = [
			{
				provider: 'chatgpt',
				current: null,
				latest_attempt: answerRow({ id: 'answer-2', revision: 2, status: 'pending', text: null })
			}
		];

		const after = applyRunState(before, answers);

		expect(after.chatgpt.answer?.text).toBe('the good answer');
		expect(cardPhase(after.chatgpt)).toBe('regenerating');
	});

	it('does not raise a banner for a failure older than the rendered answer', () => {
		const answers: ProviderAnswers[] = [
			{
				provider: 'chatgpt',
				current: answerRow({ revision: 2, text: 'newest good answer' }),
				latest_attempt: answerRow({ revision: 2, text: 'newest good answer' })
			}
		];

		const after = applyRunState(initialCards(), answers);

		expect(after.chatgpt.failure).toBeNull();
		expect(cardPhase(after.chatgpt)).toBe('complete');
	});
});

describe('requires configuration', () => {
	it('names the missing variables and holds no answer', () => {
		const after = applyNotConfigured(initialCards(), 'gemini', [
			'ANSWER_COMPARE_GEMINI_API_KEY',
			'ANSWER_COMPARE_GEMINI_MODEL'
		]);

		expect(cardPhase(after.gemini)).toBe('requires_configuration');
		expect(after.gemini.missing).toEqual([
			'ANSWER_COMPARE_GEMINI_API_KEY',
			'ANSWER_COMPARE_GEMINI_MODEL'
		]);
		expect(after.gemini.answer).toBeNull();
		expect(isRetryDisabled(after.gemini)).toBe(false);
	});

	it('marks only the unconfigured providers from a run response', () => {
		const before = initialCards();
		const after = applyProviderConfigs(before, [
			config({ id: 'chatgpt' }),
			config({ id: 'gemini', configured: false, missing: ['ANSWER_COMPARE_GEMINI_MODEL'] }),
			config({ id: 'vesqor' })
		]);

		expect(after.chatgpt).toBe(before.chatgpt);
		expect(after.vesqor).toBe(before.vesqor);
		expect(cardPhase(after.gemini)).toBe('requires_configuration');
	});
});

describe('oversized input', () => {
	it('carries both numbers and the provider name into the copy', () => {
		const after = applyOversized(initialCards(), 'chatgpt', 100000, 140233);
		const described = describeFailure(after.chatgpt.failure!);

		expect(described.key).toBe(ERROR_COPY.oversized);
		expect(described.params.actualChars).toBe(140233);
		expect(described.params.limitChars).toBe(100000);
		expect(described.params.provider).toBe('ChatGPT');
		expect(isRetryDisabled(after.chatgpt)).toBe(true);
	});
});

describe('error copy', () => {
	it('has a message for every code the backend can emit', () => {
		const backendCodes = [
			'auth',
			'rate_limit',
			'timeout',
			'context_length_exceeded',
			'upstream_error',
			'network',
			'malformed_response',
			'stale',
			'not_configured',
			'oversized',
			'already_running'
		];

		for (const code of backendCodes) {
			expect(ERROR_COPY[code], `no copy for ${code}`).toBeTruthy();
		}
	});

	it('carries an unknown code through instead of hiding it', () => {
		const described = describeFailure({ code: 'teapot' });

		expect(described.key).toBe(UNKNOWN_ERROR_COPY);
		expect(described.params.code).toBe('teapot');
	});

	it('disables retry only where retrying cannot help', () => {
		const running = applyFailure(initialCards(), 'chatgpt', { code: 'already_running' });
		const timedOut = applyFailure(initialCards(), 'chatgpt', { code: 'timeout' });

		expect(isRetryDisabled(running.chatgpt)).toBe(true);
		expect(isRetryDisabled(timedOut.chatgpt)).toBe(false);
	});
});

describe('elapsed counter', () => {
	it('counts whole seconds from the start, and is zero when idle', () => {
		const started = startGeneration(initialCards(), 'chatgpt', 10_000);

		expect(elapsedSeconds(started.chatgpt, 10_000)).toBe(0);
		expect(elapsedSeconds(started.chatgpt, 73_400)).toBe(63);
		expect(elapsedSeconds(initialCards().chatgpt, 99_999)).toBe(0);
	});
});

describe('initial state', () => {
	it('starts every provider empty, in the fixed order', () => {
		const cards = initialCards();

		expect(PROVIDER_IDS).toEqual(['chatgpt', 'gemini', 'vesqor']);
		for (const provider of PROVIDER_IDS) {
			expect(cardPhase(cards[provider])).toBe('empty');
			expect(cards[provider].answer).toBeNull();
		}
	});
});

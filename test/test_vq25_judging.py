"""VQ-25 stage 3a: one judge — blinding, mapping, the report schema, the ladder.

Run from the repository root:  pytest test/test_vq25_judging.py -q

Same machinery as stage 2: no `pytest-asyncio`, async driven with
`asyncio.run(...)`, provider doubles as `httpx.MockTransport` installed over the
client's `_build_async_client` seam. The rule of this file: **assert on what the
double received**, not on what we stored. A judge that is blind on disk and not
on the wire is not blind.
"""

import asyncio
import json
import random
import re
import secrets
import time
from typing import Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.answer_compare import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    AnswerCompareAnswer,
    AnswerCompareAnswers,
    AnswerCompareReport,
    AnswerCompareReports,
    AnswerCompareRun,
    AnswerCompareRunForm,
    AnswerCompareRuns,
    AnswerCompareSummary,
)
from open_webui.models.users import UserModel
from open_webui.routers import answer_compare as answer_compare_router
from open_webui.utils import answer_compare_client as provider_client
from open_webui.utils import answer_compare_judge as judge
from open_webui.utils import answer_compare_providers as providers
from open_webui.utils.auth import get_current_user
from sqlalchemy import select

DUMMY_KEY = 'sk-vq25-judging-secret-do-not-leak'
PROMPT = 'Compare ChatGPT and Gemini on latency.'  # the admin may name providers; we must not
REFERENCE = 'Latency is per-request; throughput is per-unit-time.'

PROVIDER_ENV = {
    'chatgpt': ('ANSWER_COMPARE_CHATGPT_BASE_URL', 'ANSWER_COMPARE_CHATGPT_API_KEY', 'ANSWER_COMPARE_CHATGPT_MODEL'),
    'gemini': ('ANSWER_COMPARE_GEMINI_BASE_URL', 'ANSWER_COMPARE_GEMINI_API_KEY', 'ANSWER_COMPARE_GEMINI_MODEL'),
    'vesqor': ('ANSWER_COMPARE_VESQOR_BASE_URL', 'ANSWER_COMPARE_VESQOR_API_KEY', 'ANSWER_COMPARE_VESQOR_MODEL'),
}
PROVIDER_HOST = {
    'chatgpt': 'https://chatgpt.example/v1',
    'gemini': 'https://gemini.example/v1',
    'vesqor': 'https://door.example/api/v1',
}
# Realistic model ids on purpose: the scaffolding test must hold even when the
# judge's own model id names its vendor.
PROVIDER_MODEL = {
    'chatgpt': 'gpt-test-1',
    'gemini': 'gemini-test-1',
    'vesqor': 'vesqor-reasoning',
}
ANSWER_TEXT = {
    'chatgpt': 'Latency matters most for interactive use. Throughput is a batch concern.',
    'gemini': 'Throughput and latency trade off; caching helps both in practice.',
    'vesqor': 'Measure p99 latency first, then raise throughput without regressing it.',
}

PROVIDER_NAME_RE = re.compile(r'chatgpt|gemini|vesqor|openai|google|gpt-', re.I)
ANSWER_BLOCK_RE = re.compile(r'=== ANSWER ([A-C]) ===\n(.*?)\n=== END ANSWER \1 ===', re.S)


####################
# Fixtures and doubles
####################


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for names in PROVIDER_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    for provider_id in providers.PROVIDER_IDS:
        monkeypatch.delenv(providers.max_input_chars_env(provider_id), raising=False)
        monkeypatch.delenv(providers.judge_max_input_chars_env(provider_id), raising=False)
    monkeypatch.delenv(provider_client.ENV_REQUEST_TIMEOUT_SECONDS, raising=False)
    judge.reset_mode_cache()
    asyncio.run(_create_tables())
    return monkeypatch


def configure(monkeypatch, *provider_ids: str) -> None:
    for provider_id in provider_ids:
        base_url_env, key_env, model_env = PROVIDER_ENV[provider_id]
        monkeypatch.setenv(base_url_env, PROVIDER_HOST[provider_id])
        monkeypatch.setenv(key_env, DUMMY_KEY)
        monkeypatch.setenv(model_env, PROVIDER_MODEL[provider_id])


def labels_in(body: dict) -> list[str]:
    """The labels the request actually carried, read from the user message."""
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    return [m.group(1) for m in ANSWER_BLOCK_RE.finditer(user)]


def answers_in(body: dict) -> dict[str, str]:
    """label -> the answer text under that label, as received."""
    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
    return {m.group(1): m.group(2) for m in ANSWER_BLOCK_RE.finditer(user)}


def valid_report(labels: list[str], note_for: Optional[dict[str, str]] = None, rationale: str = 'A is best.') -> dict:
    findings = lambda label: [{'passage': 'quoted bit', 'note': (note_for or {}).get(label, f'note for {label}')}]  # noqa: E731
    return {
        'answers': [
            {
                'label': label,
                'strengths': findings(label),
                'errors_or_unsupported': [],
                'omissions': [{'passage': '', 'note': 'a general remark with no passage'}],
                'useful_extras': [],
                'unnecessary': [],
                'improvements': [],
            }
            for label in labels
        ],
        'verdict': {'kind': 'winner', 'labels': [labels[0]]},
        'rationale': rationale,
        'needs_verification': ['the p99 figure'],
    }


def completion(content, model: str = 'judge-model-2026-01-01') -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={'model': model, 'choices': [{'message': {'content': text}}]})


class JudgeDouble:
    """Records every request; replies from a handler that sees the parsed body."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responder = lambda body, request: completion(valid_report(labels_in(body)))

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.requests.append({'url': str(request.url), 'headers': dict(request.headers), 'body': body})
        return self.responder(body, request)

    @property
    def last(self) -> dict:
        return self.requests[-1]['body']


def install_double(monkeypatch) -> JudgeDouble:
    double = JudgeDouble()

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(double.handler), timeout=timeout)

    monkeypatch.setattr(provider_client, '_build_async_client', build)
    return double


def seed_rng(monkeypatch, seed: int) -> None:
    """The injection point: the judge asks the module for its RNG at call time."""
    monkeypatch.setattr(judge, 'default_rng', lambda: random.Random(seed))


def _user(role: str) -> UserModel:
    return UserModel(
        id=f'{role}-id',
        email=f'{role}@example.com',
        name=role.capitalize(),
        role=role,
        last_active_at=0,
        updated_at=0,
        created_at=0,
    )


def client_as(role: str = 'admin') -> TestClient:
    app = FastAPI()
    app.include_router(answer_compare_router.router, prefix='/api/v1/compare', tags=['compare'])
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app)


async def _create_tables() -> None:
    from open_webui.internal.db import Base, async_engine

    tables = [m.__table__ for m in (AnswerCompareRun, AnswerCompareAnswer, AnswerCompareReport, AnswerCompareSummary)]
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))


def make_run(prompt: str = PROMPT, reference: str = REFERENCE) -> str:
    async def _run():
        run = await AnswerCompareRuns.insert(
            user_id='admin-id',
            admin_email='admin@example.com',
            form=AnswerCompareRunForm(prompt=prompt, reference=reference),
        )
        return run.id

    return asyncio.run(_run())


def seed_answers(
    run_id: str, texts: Optional[dict[str, str]] = None, statuses: Optional[dict[str, str]] = None
) -> None:
    async def _seed():
        for provider_id in providers.PROVIDER_IDS:
            text = (texts or ANSWER_TEXT).get(provider_id)
            if text is None:
                continue
            await AnswerCompareAnswers.insert_next_revision(
                run_id=run_id,
                provider=provider_id,
                status=(statuses or {}).get(provider_id, STATUS_COMPLETE),
                requested_model=PROVIDER_MODEL[provider_id],
                model=f'{PROVIDER_MODEL[provider_id]}-resolved',
                text=text,
            )

    asyncio.run(_seed())


def stored_reports(run_id: str) -> list[dict]:
    async def _rows():
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            result = await db.execute(
                select(AnswerCompareReport)
                .where(AnswerCompareReport.run_id == run_id)
                .order_by(AnswerCompareReport.judge, AnswerCompareReport.revision)
            )
            return [
                {
                    'id': r.id,
                    'judge': r.judge,
                    'revision': r.revision,
                    'status': r.status,
                    'requested_model': r.requested_model,
                    'model': r.model,
                    'label_map': r.label_map,
                    'report': r.report,
                    'judged_versions': r.judged_versions,
                    'blinding_compromised': r.blinding_compromised,
                    'params': r.params,
                    'error': r.error,
                }
                for r in result.scalars().all()
            ]

    return asyncio.run(_rows())


def judge_url(run_id: str, judge_id: str) -> str:
    return f'/api/v1/compare/runs/{run_id}/reports/{judge_id}'


####################
# Pure core — 2, 3, 5, 8, 9, 10 (no DB, no router)
####################


def labeled_for(seed: int) -> list[judge.LabeledAnswer]:
    triples = [(p, 1, ANSWER_TEXT[p]) for p in providers.PROVIDER_IDS]
    return judge.shuffle_labels(triples, random.Random(seed))


def test_core_mapping_holds_on_the_message_bytes():
    labeled = labeled_for(7)
    label_map = judge.label_map_of(labeled)
    messages = judge.build_messages(PROMPT, REFERENCE, labeled)
    received = answers_in({'messages': messages})

    assert sorted(received) == sorted(label_map)
    for label, text in received.items():
        assert text == ANSWER_TEXT[label_map[label]], label


def test_core_fresh_order_per_call():
    maps = {json.dumps(judge.label_map_of(labeled_for(seed)), sort_keys=True) for seed in range(12)}
    # Twelve seeds over 3! = 6 permutations: more than one order must appear.
    assert len(maps) > 1


def test_core_scaffolding_names_no_provider_and_never_hints_at_ownership():
    labeled = labeled_for(3)
    messages = judge.build_messages(PROMPT, REFERENCE, labeled)
    text = json.dumps(messages)
    for body in ANSWER_TEXT.values():
        text = text.replace(json.dumps(body)[1:-1], '')
    text = text.replace(json.dumps(PROMPT)[1:-1], '').replace(json.dumps(REFERENCE)[1:-1], '')

    assert not PROVIDER_NAME_RE.search(text), PROVIDER_NAME_RE.search(text)
    # Silence is the blinding: nothing tells the judge that one answer is its own,
    # or even that one might be. ("your own assessment" in the criteria is about
    # the judge's judgement, not about authorship.)
    lowered = text.lower()
    for hint in ('your answer', 'your own answer', 'you wrote', 'one of these is yours', 'may be yours', 'is yours'):
        assert hint not in lowered, hint


def test_core_skeleton_is_valid_json_without_pipes():
    skeleton = judge.schema_skeleton(['A', 'B'])
    json_part, alternatives = skeleton.rsplit('\n\n', 1)
    parsed = json.loads(json_part)
    assert set(parsed) == {'answers', 'verdict', 'rationale', 'needs_verification'}
    assert '|' not in json_part
    assert alternatives == 'kind is one of: winner, tie, no_reliable_winner; labels are: A, B'
    assert skeleton in judge.build_system_message(['A', 'B'])


def test_core_blinding_leak_detection():
    labeled = judge.shuffle_labels(
        [
            ('vesqor', 1, 'As VESQOR, I would measure p99 first.'),
            ('chatgpt', 1, 'Gemini is a fine model, but latency matters most.'),
            ('gemini', 2, 'Throughput and latency trade off.'),
        ],
        random.Random(1),
    )
    assert judge.detect_blinding_leaks(labeled) == ['vesqor']
    # The text is sent unaltered.
    received = answers_in({'messages': judge.build_messages(PROMPT, None, labeled)})
    assert 'As VESQOR, I would measure p99 first.' in received.values()


@pytest.mark.parametrize(
    ('content', 'reason_fragment'),
    [
        ('this is not json', 'not a single JSON object'),
        (json.dumps(valid_report(['A', 'Z'])), 'exactly the labels'),
        (json.dumps({**valid_report(['A', 'B']), 'verdict': {'kind': 'winner', 'labels': ['A', 'B']}}), 'winner'),
        (json.dumps({**valid_report(['A', 'B']), 'verdict': {'kind': 'tie', 'labels': ['A']}}), 'tie'),
        (json.dumps(valid_report(['A'])), 'exactly the labels'),
        ('Here is my report:\n' + json.dumps(valid_report(['A', 'B'])), 'not a single JSON object'),
        (json.dumps({**valid_report(['A', 'B']), 'verdict': {'kind': 'winner', 'labels': ['C']}}), 'not sent'),
        (json.dumps({**valid_report(['A', 'B']), 'rationale': ''}), 'rationale'),
    ],
)
def test_core_malformed_reports_are_rejected(content, reason_fragment):
    with pytest.raises(judge.MalformedReport) as excinfo:
        judge.parse_report(content, ['A', 'B'])
    assert reason_fragment in excinfo.value.reason


def test_core_empty_passage_is_not_malformed():
    report = valid_report(['A', 'B'])
    report['answers'][0]['strengths'] = [{'passage': '', 'note': 'well structured overall'}]
    assert judge.parse_report(json.dumps(report), ['A', 'B']) == report


def test_core_strict_schema_obeys_strict_mode_rules():
    schema = judge.report_schema(['A', 'B', 'C'])

    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node.get('additionalProperties') is False, node
                assert sorted(node['required']) == sorted(node['properties']), node
            for key in ('minItems', 'maxItems', 'format', 'minLength'):
                assert key not in node, key
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(schema)


class LadderDouble:
    """Drives call_judge directly with a fake completion function."""

    def __init__(self, reject_when, status: int = 400, message: str = 'Invalid parameter: rejected'):
        self.reject_when = reject_when
        self.status = status
        self.message = message
        self.attempts: list[dict] = []

    async def __call__(self, base_url, api_key, model, messages, extra_body=None):
        extra = dict(extra_body or {})
        self.attempts.append(extra)
        if self.reject_when(extra):
            raise provider_client.UpstreamError(
                f'The provider returned HTTP {self.status}.', status=self.status, upstream_message=self.message
            )
        return provider_client.CompletionResult(
            content=json.dumps(valid_report(['A', 'B'])), model='m', params={'stream': False, **extra}
        )


def run_ladder(double) -> judge.JudgeCallResult:
    return asyncio.run(
        judge.call_judge(
            provider_id='chatgpt',
            base_url='https://x',
            api_key=DUMMY_KEY,
            model='gpt-test-1',
            messages=judge.build_messages(PROMPT, None, labeled_for(1)[:2]),
            labels=['A', 'B'],
            completion=double,
        )
    )


def test_core_ladder_rejected_json_schema_moves_to_json_object():
    double = LadderDouble(lambda extra: extra.get('response_format', {}).get('type') == 'json_schema')
    result = run_ladder(double)

    modes = [a.get('response_format', {}).get('type') for a in double.attempts]
    temps = ['temperature' in a for a in double.attempts]
    # Drop temperature first (same mode), then move down the ladder.
    assert modes == ['json_schema', 'json_schema', 'json_object']
    assert temps == [True, False, True]
    assert result.params['structured_output_mode'] == judge.MODE_JSON_OBJECT
    assert [r['mode'] for r in result.params['structured_output_rejections']] == [1, 1]
    assert result.params['structured_output_rejections'][0]['message'] == 'Invalid parameter: rejected'


def test_core_ladder_treats_422_as_a_rejection_too():
    """A FastAPI door with extra='forbid' answers an unknown response_format with
    422, not 400. That must walk the ladder and be recorded — not stop after one
    attempt as a silent upstream_error with the provider's text thrown away."""
    double = LadderDouble(
        lambda extra: 'response_format' in extra,
        status=422,
        message='Extra inputs are not permitted: response_format',
    )
    result = run_ladder(double)

    modes = [a.get('response_format', {}).get('type') for a in double.attempts]
    assert modes == ['json_schema', 'json_schema', 'json_object', 'json_object', None]
    assert result.params['structured_output_mode'] == judge.MODE_PLAIN
    rejections = result.params['structured_output_rejections']
    assert [r['status'] for r in rejections] == [422, 422, 422, 422]
    assert rejections[0]['message'] == 'Extra inputs are not permitted: response_format'


def test_core_ladder_temperature_dropped_before_mode_changes():
    double = LadderDouble(lambda extra: 'temperature' in extra)
    result = run_ladder(double)

    assert len(double.attempts) == 2
    assert 'temperature' not in double.attempts[1]
    assert double.attempts[1]['response_format']['type'] == 'json_schema'
    assert result.params['structured_output_mode'] == judge.MODE_JSON_SCHEMA
    assert 'temperature' not in result.params


def test_core_ladder_400_without_response_format_is_a_real_error():
    double = LadderDouble(lambda extra: True)
    with pytest.raises(provider_client.UpstreamError):
        run_ladder(double)

    # Every rung, each with and without temperature, then stop.
    modes = [a.get('response_format', {}).get('type') for a in double.attempts]
    assert modes == ['json_schema', 'json_schema', 'json_object', 'json_object', None, None]


def test_core_ladder_remembers_the_step_and_forgets_it_on_a_400():
    rejecting = LadderDouble(lambda extra: extra.get('response_format', {}).get('type') == 'json_schema')
    run_ladder(rejecting)
    assert len(rejecting.attempts) == 3

    # Second call starts at the remembered step: one request.
    remembered = LadderDouble(lambda extra: False)
    result = run_ladder(remembered)
    assert len(remembered.attempts) == 1
    assert remembered.attempts[0]['response_format']['type'] == 'json_object'
    assert result.params['structured_output_mode'] == judge.MODE_JSON_OBJECT

    # The provider changed its mind: the cached step is dropped and the ladder restarts at 1.
    changed = LadderDouble(lambda extra: extra.get('response_format', {}).get('type') == 'json_object')
    result = run_ladder(changed)
    assert changed.attempts[0]['response_format']['type'] == 'json_object'  # the cached step, rejected
    assert changed.attempts[1]['response_format']['type'] == 'json_schema'  # restart from 1
    assert result.params['structured_output_mode'] == judge.MODE_JSON_SCHEMA


def test_core_ladder_forgets_a_cached_step_even_when_the_restart_fails():
    """The remembered step is dropped the moment it is rejected — not only when
    something else later succeeds. Otherwise a door that starts refusing
    everything would keep a stale entry, and the next call would start at the
    wrong rung instead of from step 1."""
    accepting = LadderDouble(lambda extra: extra.get('response_format', {}).get('type') == 'json_schema')
    run_ladder(accepting)
    assert judge._MODE_CACHE[('chatgpt', 'gpt-test-1')] == (judge.MODE_JSON_OBJECT, True)

    refusing = LadderDouble(lambda extra: True)
    with pytest.raises(provider_client.UpstreamError):
        run_ladder(refusing)

    # The first attempt was the cached step; the second is the restart from 1.
    assert refusing.attempts[0]['response_format']['type'] == 'json_object'
    assert refusing.attempts[1]['response_format']['type'] == 'json_schema'
    # And nothing stale is left behind for the next call.
    assert ('chatgpt', 'gpt-test-1') not in judge._MODE_CACHE

    fresh = LadderDouble(lambda extra: False)
    run_ladder(fresh)
    assert fresh.attempts[0]['response_format']['type'] == 'json_schema'


def test_core_skeleton_present_in_every_mode():
    double = LadderDouble(lambda extra: 'response_format' in extra)
    run_ladder(double)
    # The same messages went out on every rung; the skeleton is in the system message regardless.
    system = judge.build_messages(PROMPT, None, labeled_for(1)[:2])[0]['content']
    assert '"needs_verification"' in system
    assert 'kind is one of: winner, tie, no_reliable_winner; labels are: A, B' in system
    assert double.attempts[-1].get('response_format') is None


def test_core_context_length_400_is_not_walked_down_the_ladder():
    class ContextDouble(LadderDouble):
        async def __call__(self, *args, **kwargs):
            self.attempts.append(dict(kwargs.get('extra_body') or {}))
            raise provider_client.ContextLengthExceededError('too long', status=400)

    double = ContextDouble(lambda extra: True)
    with pytest.raises(provider_client.ContextLengthExceededError):
        run_ladder(double)
    assert len(double.attempts) == 1


####################
# Endpoint — 1, 2, 4, 6, 7, 8, 9, 11, 12, 13, 14
####################


def test_each_judge_alone_returns_a_mapped_complete_report(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini', 'vesqor')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    for judge_id in providers.PROVIDER_IDS:
        response = api.post(judge_url(run_id, judge_id))
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['status'] == STATUS_COMPLETE
        assert payload['judge'] == judge_id
        assert payload['revision'] == 1
        assert payload['requested_model'] == PROVIDER_MODEL[judge_id]
        assert payload['model'] == 'judge-model-2026-01-01'
        assert sorted(payload['label_map']) == ['A', 'B', 'C']
        assert sorted(payload['label_map'].values()) == sorted(providers.PROVIDER_IDS)
        assert payload['missing_providers'] == []
        assert payload['error'] is None
        # Stored with anonymous labels — the audit trail.
        assert [a['label'] for a in payload['report']['answers']] == ['A', 'B', 'C']

        # The call went to the judge's own door with its own model.
        assert double.requests[-1]['url'].startswith(PROVIDER_HOST[judge_id])
        assert double.last['model'] == PROVIDER_MODEL[judge_id]


def test_mapping_holds_on_the_bytes_the_judge_received(isolated_env):
    configure(isolated_env, 'gemini')
    double = install_double(isolated_env)
    # The double writes, per label, a note quoting the answer it saw under it.
    double.responder = lambda body, request: completion(
        valid_report(labels_in(body), note_for={label: f'saw: {text[:24]}' for label, text in answers_in(body).items()})
    )
    run_id = make_run()
    seed_answers(run_id)
    # A seed whose permutation is not the identity, so that a map built from one
    # order and bodies taken from another cannot pass by coincidence.
    seed_rng(isolated_env, 0)

    payload = client_as().post(judge_url(run_id, 'gemini')).json()
    label_map = payload['label_map']
    assert [label_map[label] for label in 'ABC'] != list(providers.PROVIDER_IDS), 'the shuffle must not be the identity'

    # Invariant: the text under === ANSWER X === in the request IS provider label_map[X]'s answer.
    received = answers_in(double.last)
    assert sorted(received) == sorted(label_map)
    for label, text in received.items():
        assert text == ANSWER_TEXT[label_map[label]], label

    # And the per-label findings resolve to the right providers through the stored map.
    mapped = judge.map_report(payload['report'], label_map)
    for provider_id, findings in mapped['answers'].items():
        assert findings['strengths'][0]['note'] == f'saw: {ANSWER_TEXT[provider_id][:24]}'


def test_fresh_order_per_call_on_the_endpoint(isolated_env):
    configure(isolated_env, 'chatgpt')
    install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    seen = set()
    for seed in range(8):
        seed_rng(isolated_env, seed)
        payload = api.post(judge_url(run_id, 'chatgpt')).json()
        seen.add(json.dumps(payload['label_map'], sort_keys=True))
    assert len(seen) > 1


def test_no_cross_visibility_between_judges(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)
    marker = secrets.token_hex(16)

    async def _other_judges_report():
        await AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge='gemini',
            status=STATUS_COMPLETE,
            label_map={'A': 'chatgpt', 'B': 'gemini', 'C': 'vesqor'},
            report=valid_report(['A', 'B', 'C'], rationale=f'secret verdict {marker}'),
            judged_versions=[{'provider': p, 'revision': 1} for p in providers.PROVIDER_IDS],
        )

    asyncio.run(_other_judges_report())

    assert client_as().post(judge_url(run_id, 'chatgpt')).status_code == 200
    assert marker not in json.dumps(double.last)
    assert marker not in json.dumps(double.requests[-1]['headers'])


def test_request_body_names_no_provider_beyond_admin_text_and_answers(isolated_env):
    configure(isolated_env, 'vesqor')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)

    assert client_as().post(judge_url(run_id, 'vesqor')).status_code == 200

    body = dict(double.last)
    # The judge's own model id is configuration, not scaffolding we wrote, and it
    # says nothing about which answer is whose.
    assert body.pop('model') == PROVIDER_MODEL['vesqor']
    text = json.dumps(body)
    for answer in ANSWER_TEXT.values():
        text = text.replace(json.dumps(answer)[1:-1], '')
    text = text.replace(json.dumps(PROMPT)[1:-1], '').replace(json.dumps(REFERENCE)[1:-1], '')

    assert not PROVIDER_NAME_RE.search(text), PROVIDER_NAME_RE.search(text).group(0)
    assert [m['role'] for m in double.last['messages']] == ['system', 'user']


def test_judge_receives_its_own_answer(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)

    payload = client_as().post(judge_url(run_id, 'chatgpt')).json()

    received = answers_in(double.last)
    assert ANSWER_TEXT['chatgpt'] in received.values()
    assert 'chatgpt' in payload['label_map'].values()


def test_two_answer_judging_names_the_missing_provider(isolated_env):
    configure(isolated_env, 'gemini')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id, texts={'chatgpt': ANSWER_TEXT['chatgpt'], 'gemini': ANSWER_TEXT['gemini']})
    # vesqor has only a failed attempt: not a complete answer, so not judged.
    seed_answers(run_id, texts={'vesqor': 'never finished'}, statuses={'vesqor': STATUS_FAILED})

    payload = client_as().post(judge_url(run_id, 'gemini')).json()

    assert labels_in(double.last) == ['A', 'B']
    assert len(answers_in(double.last)) == 2
    assert sorted(payload['label_map'].values()) == ['chatgpt', 'gemini']
    assert payload['missing_providers'] == ['vesqor']
    assert payload['judged_versions'] == [{'provider': 'chatgpt', 'revision': 1}, {'provider': 'gemini', 'revision': 1}]

    system = next(m['content'] for m in double.last['messages'] if m['role'] == 'system')
    assert 'labels are: A, B' in system


def test_blinding_compromised_is_annotated_not_altered(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    run_id = make_run()
    seed_answers(
        run_id,
        texts={
            'vesqor': 'As VESQOR, I would measure p99 first.',
            'chatgpt': 'Gemini is a fine model, but latency matters most.',
            'gemini': ANSWER_TEXT['gemini'],
        },
    )

    payload = client_as().post(judge_url(run_id, 'chatgpt')).json()

    assert payload['blinding_compromised'] == ['vesqor']
    assert 'As VESQOR, I would measure p99 first.' in answers_in(double.last).values()
    # The judge is not told.
    assert 'compromis' not in json.dumps(double.last['messages']).lower()


@pytest.mark.parametrize(
    'content',
    [
        'not json at all',
        lambda labels: json.dumps(valid_report(labels[:-1] + ['Z'])),
        lambda labels: json.dumps({**valid_report(labels), 'verdict': {'kind': 'winner', 'labels': labels[:2]}}),
        lambda labels: json.dumps({**valid_report(labels), 'verdict': {'kind': 'tie', 'labels': labels[:1]}}),
        lambda labels: json.dumps(valid_report(labels[:-1])),
        lambda labels: 'Sure! ' + json.dumps(valid_report(labels)),
    ],
)
def test_malformed_report_fails_retryably_and_keeps_the_row(isolated_env, content):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.responder = lambda body, request: completion(
        content if isinstance(content, str) else content(labels_in(body))
    )
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    first = api.post(judge_url(run_id, 'chatgpt'))
    assert first.status_code == 200
    assert first.json()['status'] == STATUS_FAILED
    assert first.json()['error']['code'] == 'malformed_report'
    assert first.json()['report'] is None

    # Retry: a valid report inserts the next revision; the failed row stays.
    double.responder = lambda body, request: completion(valid_report(labels_in(body)))
    second = api.post(judge_url(run_id, 'chatgpt')).json()
    assert second['status'] == STATUS_COMPLETE
    assert second['revision'] == 2

    rows = stored_reports(run_id)
    assert [(r['revision'], r['status']) for r in rows] == [(1, STATUS_FAILED), (2, STATUS_COMPLETE)]
    assert json.loads(rows[0]['error'])['code'] == 'malformed_report'


def test_judged_versions_and_outdated_are_derived_on_read(isolated_env):
    configure(isolated_env, 'gemini')
    install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    first = api.post(judge_url(run_id, 'gemini')).json()
    assert first['judged_versions'] == [{'provider': p, 'revision': 1} for p in providers.PROVIDER_IDS]

    reports = {r['judge']: r for r in api.get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert list(reports) == list(providers.PROVIDER_IDS)  # fixed order
    assert reports['gemini']['outdated'] is False
    assert reports['gemini']['current']['revision'] == 1
    assert reports['chatgpt']['current'] is None and reports['chatgpt']['outdated'] is False

    # One answer is regenerated: the run's answer set moved, the report did not.
    seed_answers(run_id, texts={'vesqor': 'a regenerated vesqor answer'})
    reports = {r['judge']: r for r in api.get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert reports['gemini']['outdated'] is True

    # Re-judge: new revision judges the new set; the old row is still there.
    second = api.post(judge_url(run_id, 'gemini')).json()
    assert second['revision'] == 2
    assert {'provider': 'vesqor', 'revision': 2} in second['judged_versions']
    reports = {r['judge']: r for r in api.get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert reports['gemini']['outdated'] is False
    assert reports['gemini']['current']['revision'] == 2
    assert [r['revision'] for r in stored_reports(run_id)] == [1, 2]


def test_preconditions(isolated_env):
    configure(isolated_env, 'chatgpt', 'gemini')
    double = install_double(isolated_env)
    api = client_as()

    # One complete answer: nothing sent, the body names who has one.
    run_id = make_run()
    seed_answers(run_id, texts={'chatgpt': ANSWER_TEXT['chatgpt']})
    response = api.post(judge_url(run_id, 'chatgpt'))
    assert response.status_code == 409
    assert response.json()['detail'] == {'code': 'not_enough_answers', 'complete': ['chatgpt']}
    assert double.requests == []
    assert stored_reports(run_id) == []

    seed_answers(run_id, texts={'gemini': ANSWER_TEXT['gemini']})

    # Unconfigured judge.
    response = api.post(judge_url(run_id, 'vesqor'))
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'not_configured'
    assert 'ANSWER_COMPARE_VESQOR_API_KEY' in response.json()['detail']['missing']

    # Unknown judge, unknown run, non-admin.
    assert api.post(judge_url(run_id, 'claude')).status_code == 400
    assert api.post(judge_url(run_id, 'claude')).json()['detail']['code'] == 'unknown_judge'
    assert api.post(judge_url('nope', 'chatgpt')).status_code == 404
    assert client_as('user').post(judge_url(run_id, 'chatgpt')).status_code == 401

    # A fresh pending report: already running, nothing sent.
    async def _pending(age_seconds: int):
        row = await AnswerCompareReports.insert_next_revision(run_id=run_id, judge='chatgpt', status=STATUS_PENDING)
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            stored = await db.get(AnswerCompareReport, row.id)
            stored.created_at = int(time.time()) - age_seconds
            await db.commit()
        return row

    pending = asyncio.run(_pending(0))
    response = api.post(judge_url(run_id, 'chatgpt'))
    assert response.status_code == 409
    assert response.json()['detail'] == {'code': 'already_running', 'provider': 'chatgpt', 'since': pending.created_at}
    assert double.requests == []

    # Make that pending row stale: a new revision is allowed, and GET reports it as stale.
    asyncio.run(_pending(int(provider_client.request_timeout_seconds()) + 60))
    stale_before = stored_reports(run_id)
    response = api.post(judge_url(run_id, 'chatgpt'))
    assert response.status_code == 200
    assert response.json()['revision'] == len(stale_before) + 1

    # Oversized against the judge's own limit, naming the judge.
    isolated_env.setenv('ANSWER_COMPARE_GEMINI_JUDGE_MAX_INPUT_CHARS', '100')
    sent_before = len(double.requests)
    response = api.post(judge_url(run_id, 'gemini'))
    assert response.status_code == 413
    detail = response.json()['detail']
    assert detail['code'] == 'oversized' and detail['provider'] == 'gemini' and detail['limit_chars'] == 100
    assert detail['actual_chars'] > 100
    assert len(double.requests) == sent_before


def test_stale_pending_report_reads_as_failed(isolated_env):
    configure(isolated_env, 'chatgpt')
    install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)

    async def _stale():
        row = await AnswerCompareReports.insert_next_revision(run_id=run_id, judge='chatgpt', status=STATUS_PENDING)
        from open_webui.internal.db import get_async_db_context

        async with get_async_db_context() as db:
            stored = await db.get(AnswerCompareReport, row.id)
            stored.created_at = int(time.time()) - int(provider_client.request_timeout_seconds()) - 60
            await db.commit()

    asyncio.run(_stale())

    reports = {r['judge']: r for r in client_as().get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert reports['chatgpt']['current'] is None
    assert reports['chatgpt']['latest_attempt']['status'] == STATUS_FAILED
    assert reports['chatgpt']['latest_attempt']['error']['code'] == 'stale'
    assert stored_reports(run_id)[0]['status'] == STATUS_PENDING  # read-time rule only


def test_pending_report_row_exists_while_the_judge_call_is_in_flight(isolated_env):
    configure(isolated_env, 'gemini')
    run_id = make_run()
    seed_answers(run_id)
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen['rows'] = await _rows_async(run_id)
        body = json.loads(request.content.decode())
        return completion(valid_report(labels_in(body)))

    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    isolated_env.setattr(provider_client, '_build_async_client', build)

    payload = client_as().post(judge_url(run_id, 'gemini')).json()
    assert payload['status'] == STATUS_COMPLETE

    in_flight = seen['rows']
    assert len(in_flight) == 1
    assert in_flight[0]['status'] == STATUS_PENDING
    assert in_flight[0]['label_map'] is not None
    assert in_flight[0]['judged_versions'] is not None
    assert in_flight[0]['id'] == payload['id']


async def _rows_async(run_id: str) -> list[dict]:
    from open_webui.internal.db import get_async_db_context

    async with get_async_db_context() as db:
        result = await db.execute(select(AnswerCompareReport).where(AnswerCompareReport.run_id == run_id))
        return [
            {'id': r.id, 'status': r.status, 'label_map': r.label_map, 'judged_versions': r.judged_versions}
            for r in result.scalars().all()
        ]


def test_no_key_material_anywhere_even_when_the_provider_echoes_the_header(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)

    def echo_then_accept(body, request):
        # A 400 that echoes the request headers back, as some gateways do.
        if 'response_format' in body and body['response_format']['type'] == 'json_schema':
            return httpx.Response(
                400,
                json={'error': {'message': f'Unknown name "strict"; headers: {json.dumps(dict(request.headers))}'}},
            )
        return completion(valid_report(labels_in(body)))

    double.responder = echo_then_accept
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    response = api.post(judge_url(run_id, 'chatgpt'))
    assert response.status_code == 200
    assert response.json()['status'] == STATUS_COMPLETE

    assert DUMMY_KEY not in response.text
    assert DUMMY_KEY not in api.get(f'/api/v1/compare/runs/{run_id}').text
    assert DUMMY_KEY not in json.dumps(stored_reports(run_id))

    # The echoed 400 body WAS kept as a rejection diagnostic — scrubbed. This is
    # the proof that scrub_secret sits on the path the stored row comes from.
    stored = stored_reports(run_id)[0]
    rejections = stored['params']['structured_output_rejections']
    assert len(rejections) >= 1
    assert 'Unknown name "strict"' in rejections[0]['message']
    assert 'Bearer [redacted]' in rejections[0]['message']
    assert DUMMY_KEY not in json.dumps(stored['params'])

    # And the diagnostics themselves are scrubbed at the source.
    scrubbed = provider_client.scrub_secret(f'Authorization: Bearer {DUMMY_KEY} and again {DUMMY_KEY}', DUMMY_KEY)
    assert DUMMY_KEY not in scrubbed and '[redacted]' in scrubbed

    # The key did go out — in the header, and nowhere else.
    assert double.requests[0]['headers']['authorization'] == f'Bearer {DUMMY_KEY}'


def test_params_are_persisted_and_returned_on_read(isolated_env):
    """What was sent and how the ladder went, stored on the row and served by GET —
    stage 3b and the stage-7 live check read this, so both paths must carry it."""
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.responder = lambda body, request: (
        httpx.Response(400, json={'error': {'message': 'Invalid parameter: response_format'}})
        if body.get('response_format', {}).get('type') == 'json_schema'
        else completion(valid_report(labels_in(body)))
    )
    run_id = make_run()
    seed_answers(run_id)
    api = client_as()

    posted = api.post(judge_url(run_id, 'chatgpt')).json()
    assert posted['status'] == STATUS_COMPLETE
    assert posted['params']['structured_output_mode'] == judge.MODE_JSON_OBJECT
    assert posted['params']['response_format'] == {'type': 'json_object'}
    assert posted['params']['temperature'] == judge.JUDGE_TEMPERATURE
    assert posted['params']['stream'] is False
    assert [r['mode'] for r in posted['params']['structured_output_rejections']] == [1, 1]
    assert posted['params']['structured_output_rejections'][0]['message'] == 'Invalid parameter: response_format'

    stored = stored_reports(run_id)[0]
    assert stored['params'] == posted['params']

    read = {r['judge']: r for r in api.get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert read['chatgpt']['current']['params'] == posted['params']
    assert read['chatgpt']['latest_attempt']['params'] == posted['params']


def test_malformed_report_still_records_the_ladder_diagnostics(isolated_env):
    configure(isolated_env, 'gemini')
    double = install_double(isolated_env)
    double.responder = lambda body, request: completion('not json')
    run_id = make_run()
    seed_answers(run_id)

    posted = client_as().post(judge_url(run_id, 'gemini')).json()
    assert posted['status'] == STATUS_FAILED
    assert posted['error']['code'] == 'malformed_report'
    # The call happened; its diagnostics are real and are kept.
    assert posted['params']['structured_output_mode'] == judge.MODE_JSON_SCHEMA
    assert stored_reports(run_id)[0]['params']['structured_output_mode'] == judge.MODE_JSON_SCHEMA


def test_update_result_refuses_to_overwrite_a_settled_report(isolated_env):
    """The mirror of the answer-table guard: a complete verdict is never overwritten,
    whoever calls with its id. Nothing reaches this today with a settled id, but the
    rule has to hold in the table, not in the caller."""
    configure(isolated_env, 'chatgpt')
    install_double(isolated_env)
    run_id = make_run()
    seed_answers(run_id)

    complete = client_as().post(judge_url(run_id, 'chatgpt')).json()
    assert complete['status'] == STATUS_COMPLETE
    assert complete['report'] is not None and complete['label_map'] is not None

    async def _try_to_clobber():
        return await AnswerCompareReports.update_result(
            id=complete['id'],
            status=STATUS_FAILED,
            error=json.dumps({'code': 'timeout', 'message': 'should never be written'}),
        )

    assert asyncio.run(_try_to_clobber()) is None

    stored = stored_reports(run_id)
    assert len(stored) == 1
    assert stored[0]['id'] == complete['id']
    assert stored[0]['status'] == STATUS_COMPLETE
    assert stored[0]['report'] == complete['report']
    assert stored[0]['label_map'] == complete['label_map']
    assert stored[0]['error'] is None


def test_update_result_refuses_to_overwrite_a_failed_report(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.responder = lambda body, request: completion('not json')
    run_id = make_run()
    seed_answers(run_id)

    failed = client_as().post(judge_url(run_id, 'chatgpt')).json()
    assert failed['status'] == STATUS_FAILED

    async def _try_to_clobber():
        return await AnswerCompareReports.update_result(
            id=failed['id'], status=STATUS_COMPLETE, report=valid_report(['A', 'B', 'C'])
        )

    assert asyncio.run(_try_to_clobber()) is None
    stored = stored_reports(run_id)[0]
    assert stored['status'] == STATUS_FAILED
    assert stored['report'] is None
    assert json.loads(stored['error'])['code'] == 'malformed_report'


def test_update_result_returns_none_for_a_missing_report(isolated_env):
    async def _missing():
        return await AnswerCompareReports.update_result(id='no-such-report', status=STATUS_COMPLETE)

    assert asyncio.run(_missing()) is None


####################
# 3b — mapping on read lives on the server
####################


def test_mapped_resolves_every_section_and_the_verdict_in_post_and_get(isolated_env):
    """One implementation of the label transform, on the server, so the page and
    the stage-4 tally cannot disagree. Raw `report` stays anonymous."""
    configure(isolated_env, 'gemini')
    double = install_double(isolated_env)
    double.responder = lambda body, request: completion(
        {
            **valid_report(
                labels_in(body),
                note_for={label: f'saw: {text[:24]}' for label, text in answers_in(body).items()},
            ),
            'verdict': {'kind': 'tie', 'labels': labels_in(body)[:2]},
        }
    )
    run_id = make_run()
    seed_answers(run_id)
    seed_rng(isolated_env, 0)  # a non-identity permutation
    api = client_as()

    posted = api.post(judge_url(run_id, 'gemini')).json()
    label_map = posted['label_map']
    assert [label_map[label] for label in 'ABC'] != list(providers.PROVIDER_IDS)

    mapped = posted['mapped']
    assert posted['mapping_error'] is None
    # Per-answer sections keyed by provider, each carrying that provider's own note.
    assert sorted(mapped['answers']) == sorted(providers.PROVIDER_IDS)
    for provider_id, section in mapped['answers'].items():
        assert section['strengths'][0]['note'] == f'saw: {ANSWER_TEXT[provider_id][:24]}'
        assert label_map[section['label']] == provider_id
    # Verdict labels resolved to provider ids, order preserved.
    assert mapped['verdict']['kind'] == 'tie'
    assert mapped['verdict']['providers'] == [label_map[label] for label in posted['report']['verdict']['labels']]
    assert mapped['rationale'] == posted['report']['rationale']
    assert mapped['needs_verification'] == posted['report']['needs_verification']

    # The raw report is untouched and still anonymous.
    assert [a['label'] for a in posted['report']['answers']] == ['A', 'B', 'C']
    assert posted['report']['verdict']['labels'] == ['A', 'B']
    assert stored_reports(run_id)[0]['report'] == posted['report']

    # GET serves the same mapped view.
    read = {r['judge']: r for r in api.get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    assert read['gemini']['current']['mapped'] == mapped
    assert read['gemini']['latest_attempt']['mapped'] == mapped


def test_failed_report_has_no_mapped_view(isolated_env):
    configure(isolated_env, 'chatgpt')
    double = install_double(isolated_env)
    double.responder = lambda body, request: completion('not json')
    run_id = make_run()
    seed_answers(run_id)

    posted = client_as().post(judge_url(run_id, 'chatgpt')).json()
    assert posted['status'] == STATUS_FAILED
    assert posted['mapped'] is None
    assert posted['mapping_error'] is None


def test_unmappable_label_is_answered_not_mutated(isolated_env):
    """A stored complete row whose report names a label the map does not know:
    mapped is null with a code, the row is untouched, status agrees with storage."""
    run_id = make_run()
    seed_answers(run_id)

    async def _corrupt_row():
        return await AnswerCompareReports.insert_next_revision(
            run_id=run_id,
            judge='vesqor',
            status=STATUS_COMPLETE,
            label_map={'A': 'chatgpt', 'B': 'gemini'},  # no 'C'
            report=valid_report(['A', 'B', 'C']),
            judged_versions=[{'provider': p, 'revision': 1} for p in providers.PROVIDER_IDS],
        )

    row = asyncio.run(_corrupt_row())
    before = stored_reports(run_id)

    read = {r['judge']: r for r in client_as().get(f'/api/v1/compare/runs/{run_id}').json()['reports']}
    current = read['vesqor']['current']
    assert current['id'] == row.id
    assert current['status'] == STATUS_COMPLETE  # what is stored
    assert current['mapped'] is None
    assert current['mapping_error'] == 'label_not_in_map'
    assert current['report'] == valid_report(['A', 'B', 'C'])  # the audit trail is still served
    assert current['error'] is None

    # Nothing was written on the GET.
    assert stored_reports(run_id) == before


def test_map_report_is_strict_about_unknown_labels():
    with pytest.raises(judge.UnmappedLabel):
        judge.map_report(valid_report(['A', 'B']), {'A': 'chatgpt'})
    with pytest.raises(judge.UnmappedLabel):
        judge.map_report({**valid_report(['A']), 'verdict': {'kind': 'winner', 'labels': ['Q']}}, {'A': 'chatgpt'})

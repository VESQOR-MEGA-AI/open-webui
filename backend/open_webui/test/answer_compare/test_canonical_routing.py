"""Scenarios 21, 23 and 24: every judgment path reaches the canonical engine.

These are architectural tests. They read the source of the Compare feature and
assert structural properties that no unit test of the engine itself could catch:
that no second rubric has appeared, that no handler calls a provider behind the
engine's back, and that the concurrency guards are still in place.

Source-level assertions are unusual and deliberate. The failure they exist to
prevent — a new button quietly growing its own prompt — is invisible to a test
that only exercises the engine, because such a button would never call it.
"""

import ast
import re
from pathlib import Path

from open_webui.utils import answer_compare_adjudication as adj

BACKEND = Path(__file__).resolve().parents[3]
REPO = BACKEND.parent

UTILS = BACKEND / 'open_webui' / 'utils'
ROUTER = BACKEND / 'open_webui' / 'routers' / 'answer_compare.py'
ENGINE = UTILS / 'answer_compare_adjudication.py'
JUDGE = UTILS / 'answer_compare_judge.py'
SUMMARY = UTILS / 'answer_compare_summary.py'
TALLY = UTILS / 'answer_compare_tally.py'

FRONTEND = REPO / 'src' / 'lib' / 'components' / 'admin'
COMPARE_UI = FRONTEND / 'AnswerCompare.svelte'
UI_DIR = FRONTEND / 'AnswerCompare'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def imported_modules(path: Path) -> set[str]:
    """Modules a file actually imports.

    Text matching is not good enough here: these modules document what they must
    never import, so their own prose would fail a substring check.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(f'{node.module}.{alias.name}' for alias in node.names if node.module)
    return names


def calls_named(path: Path, attribute: str) -> int:
    """How many times a given attribute call appears, ignoring prose."""
    total = 0
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == attribute:
                total += 1
    return total


####################
# 23. Every AI judgment path routes through the canonical engine
####################


def test_23_the_engine_is_the_only_module_defining_the_rubric():
    """Exactly one file may contain the category weights."""
    holders = []
    for path in [*UTILS.glob('answer_compare*.py'), ROUTER]:
        text = read(path)
        if 'factual_accuracy' in text and 'completeness' in text and 'evidence_traceability' in text:
            holders.append(path.name)
    assert holders == [ENGINE.name], f'the rubric appears in more than one module: {holders}'


def test_23_only_the_engine_builds_the_adjudication_prompt():
    """A second SYSTEM_MESSAGE is how two buttons start judging differently."""
    holders = [
        path.name for path in [*UTILS.glob('answer_compare*.py'), ROUTER] if 'SYSTEM_MESSAGE_TEMPLATE' in read(path)
    ]
    assert holders == [ENGINE.name], f'more than one module defines a system message: {holders}'


def test_23_the_judge_module_delegates_the_prompt_rather_than_restating_it():
    text = read(JUDGE)
    assert 'answer_compare_adjudication' in text
    # The old four-criterion rubric must be gone, not merely unused.
    for fragment in ('Correctness — factual errors', 'Criteria, in priority order'):
        assert fragment not in text, f'legacy rubric fragment still present: {fragment!r}'


def test_23_the_router_judges_through_one_function_only():
    """Both judging endpoints must share ``judge_once``; a third path is the defect."""
    tree = ast.parse(read(ROUTER))
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'judge_once':
            callers.add('judge_once')
    assert 'judge_once' in callers

    # And only judge_once may call the judge transport.
    source = read(ROUTER)
    call_sites = [line for line in source.splitlines() if 'judge.call_judge(' in line]
    assert len(call_sites) == 1, f'call_judge is invoked from {len(call_sites)} places, expected 1'


def test_23_the_router_parses_through_the_engine_only():
    source = read(ROUTER)
    assert source.count('judge.parse_report(') == 1


def test_23_every_judgment_endpoint_reaches_judge_once():
    """The two judging routes, and nothing else, produce a report."""
    tree = ast.parse(read(ROUTER))
    judging_routes = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            path = next((arg.value for arg in decorator.args if isinstance(arg, ast.Constant)), '')
            if isinstance(path, str) and '/reports' in path:
                judging_routes[node.name] = node

    assert set(judging_routes) == {'judge_run', 'judge_run_all'}, judging_routes.keys()

    for name, node in judging_routes.items():
        body = ast.dump(node)
        reaches = 'judge_once' in body or '_run_one_judge_entry' in body
        assert reaches, f'{name} does not reach judge_once'


####################
# 24. No legacy bypass
####################


def test_24_only_the_judge_module_calls_the_provider_client_for_judging():
    """A handler calling a provider directly is a bypass of the whole engine."""
    offenders = []
    for path in [*UTILS.glob('answer_compare*.py')]:
        if path.name in (JUDGE.name, 'answer_compare_client.py', 'answer_compare_providers.py'):
            continue
        if calls_named(path, 'chat_completion'):
            offenders.append(path.name)
    assert offenders == [], f'these modules call a provider outside the engine: {offenders}'

    # The router itself never runs an adjudication completion: its only direct
    # provider call is ``client.generate``, which produces a candidate answer.
    # Producing a candidate is not judging one, and the two use different client
    # entry points precisely so this stays checkable.
    assert calls_named(ROUTER, 'chat_completion') == 0
    assert calls_named(ROUTER, 'generate') == 1


def test_24_the_summary_never_calls_a_model():
    """The summary is a projection built in code; a provider call here would be a
    second, unversioned judgement wearing the summary's name."""
    assert not any('answer_compare_client' in name for name in imported_modules(SUMMARY))
    assert calls_named(SUMMARY, 'chat_completion') == 0


def test_24_the_tally_never_calls_a_model():
    assert not any('answer_compare_client' in name for name in imported_modules(TALLY))
    assert calls_named(TALLY, 'chat_completion') == 0


def test_24_the_tally_and_summary_take_their_verdict_vocabulary_from_the_engine():
    """One vocabulary, so "winner" cannot mean two things in two files."""
    for path in (TALLY, SUMMARY):
        imports = imported_modules(path)
        assert any('answer_compare_judge' in name or 'answer_compare_adjudication' in name for name in imports)


def test_24_no_module_defines_its_own_verdict_constants():
    offenders = []
    for path in UTILS.glob('answer_compare*.py'):
        if path.name == ENGINE.name:
            continue
        text = read(path)
        if re.search(r"^VERDICT_WINNER\s*=\s*'", text, re.MULTILINE):
            offenders.append(path.name)
    assert offenders == []


####################
# The UI side of 23/24
####################


def test_23_the_ui_reaches_judgment_only_through_the_two_canonical_endpoints():
    """No Compare component may call a model or invent a judging endpoint."""
    api = read(REPO / 'src' / 'lib' / 'apis' / 'answer-compare' / 'index.ts')
    judging_calls = re.findall(r"`?/runs/\$\{[^}]+\}/reports[^`']*", api)
    assert len(judging_calls) == 2, f'expected two judging endpoints, found {judging_calls}'


def test_24_no_compare_component_embeds_a_prompt_or_a_rubric():
    """A rubric fragment in a Svelte file is a button about to diverge."""
    banned = ('factual_accuracy', 'You are an impartial evaluator', 'You are an evidence-driven adjudicator')
    offenders = []
    for path in [COMPARE_UI, *UI_DIR.glob('*.svelte'), *UI_DIR.glob('*.ts')]:
        text = read(path)
        for fragment in banned:
            # The panel and its view state legitimately name category KEYS to
            # render the table; what they must not carry is prompt text.
            if fragment in text and fragment != 'factual_accuracy':
                offenders.append((path.name, fragment))
    assert offenders == [], offenders


def test_24_the_ui_never_computes_a_winner_of_its_own():
    """The page renders the server's verdict; deciding one here is how two views
    of the same run start disagreeing."""
    text = read(UI_DIR / 'adjudicationState.ts')
    for fragment in ('Math.max', '.sort(', 'reduce('):
        assert fragment not in text, f'{fragment!r} in the view state suggests it is ranking, not rendering'


####################
# 21. Concurrency / double-click protection
####################


def test_21_the_server_refuses_a_second_concurrent_judge_run():
    """The 409 guard on an unsettled pending row is the authoritative protection."""
    source = read(ROUTER)
    assert "'already_running'" in source
    assert 'STATUS_PENDING' in source


def test_21_each_judging_handler_guards_re_entry_synchronously():
    """A reactive `disabled` lands a tick late; the guard must not depend on it."""
    source = read(COMPARE_UI)
    assert 'judgeCards[judge]?.inFlight) return;' in source
    assert 'runAllInFlight || anyJudgeInFlight' in source
    assert 'summaryInFlight || summaryReason' in source


####################
# Versioning and auditability
####################


def test_the_engine_version_is_persisted_with_every_report():
    source = read(ROUTER)
    assert 'adjudication_engine_version' in source
    assert 'adjudication_fingerprint' in source


def test_the_audit_record_carries_a_correlation_id_and_timestamp():
    """Reproducibility needs to say WHEN, and to be joinable to the logs."""
    source = read(ROUTER)
    start = source.index('def _audit_params(')
    body = source[start : source.index('\nasync def _load_run_for_judging', start)]
    for field in (
        'adjudication_correlation_id',
        'adjudicated_at',
        'adjudication_engine_version',
        'adjudication_rubric_version',
        'adjudication_fingerprint',
        'evidence_fingerprint',
        'candidate_fingerprints',
        'judge_provider',
        'judge_model',
    ):
        assert field in body, f'audit record is missing {field!r}'


def test_the_correlation_id_is_minted_before_the_provider_call():
    """So a failed adjudication is still traceable to its log line."""
    source = read(ROUTER)
    minted = source.index('correlation_id = str(uuid.uuid4())')
    called = source.index('await judge.call_judge(')
    assert minted < called, 'the correlation id must exist before the call can fail'


def test_the_audit_record_carries_no_secret():
    """Reproducibility must never mean writing a key into a report row."""
    source = read(ROUTER)
    start = source.index('def _audit_params(')
    body = source[start : source.index('\nasync def _load_run_for_judging', start)]
    for forbidden in ('api_key', 'resolve_api_key', 'base_url', 'Authorization'):
        assert forbidden not in body, f'{forbidden!r} must not reach the stored audit record'


def test_the_stored_report_records_the_engine_version():
    assert adj.ADJUDICATION_ENGINE_VERSION
    from open_webui.utils import answer_compare_judge as judge

    assert judge.ADJUDICATION_ENGINE_VERSION == adj.ADJUDICATION_ENGINE_VERSION


####################
# Exhaustive control census — "no applicable action may remain UNKNOWN"
####################

# Every event handler in the Compare UI tree, classified. The census is
# exhaustive on purpose: a handler that appears and is not listed here fails the
# test, which is the only way "every control routes through the engine" can stay
# true as the page grows. Adding a control means classifying it.
#
# ADJUDICATIVE handlers ask a model to judge reports and must reach the canonical
# engine. Everything else must demonstrably not be an adjudication.
ADJUDICATIVE_HANDLERS = {
    'AnswerCompare.svelte:runAll',  # Judge all -> POST /runs/{id}/reports
    'AnswerCompare.svelte:runJudge',  # Judge one -> POST /runs/{id}/reports/{judge}
    'JudgeCard.svelte:onRetry',  # Retry judge -> runJudge -> same endpoint
}

NON_ADJUDICATIVE_HANDLERS = {
    'AnswerCompare.svelte:generateAll': 'produces candidate answers; judges nothing',
    'AnswerCompare.svelte:buildRunSummary': 'deterministic projection, no model call',
    'AnswerCompare.svelte:openHistoryRun': 'read-only navigation',
    'AnswerCard.svelte:onRetry': 'regenerates a candidate answer, not a judgement',
    'AnswerCard.svelte:copy': 'clipboard',
    'SummaryPanel.svelte:copy': 'clipboard',
    'HistoryList.svelte:onOpenRun': 'read-only navigation',
    'HistoryList.svelte:onRerun': 'creates a new run; generates and judges nothing by itself',
    'HistoryList.svelte:onLoadOlder': 'pagination',
}

_HANDLER_RE = re.compile(r'on:(?:click|keydown|keyup|keypress|submit)=\{')


def compare_ui_files() -> list[Path]:
    return [COMPARE_UI, *sorted(UI_DIR.glob('*.svelte'))]


def test_the_compare_tree_has_no_uncounted_event_handlers():
    """The census must stay exhaustive: count handlers, not just the known ones.

    If this fails, a control was added. Classify it in one of the two sets above
    — and if it adjudicates, make it reach the canonical engine first.
    """
    total = sum(len(_HANDLER_RE.findall(read(path))) for path in compare_ui_files())
    counted = len(ADJUDICATIVE_HANDLERS) + len(NON_ADJUDICATIVE_HANDLERS)
    assert total == counted, (
        f'the Compare UI has {total} event handlers but the census classifies {counted}. '
        'A new control must be added to ADJUDICATIVE_HANDLERS or NON_ADJUDICATIVE_HANDLERS.'
    )


def test_every_adjudicative_handler_exists_and_reaches_a_canonical_endpoint():
    """Each named adjudicative handler is real, and calls a judging endpoint."""
    api = read(REPO / 'src' / 'lib' / 'apis' / 'answer-compare' / 'index.ts')
    page = read(COMPARE_UI)

    # The only two client functions that may hit a judging endpoint.
    assert 'export const judgeRun' in api
    assert 'export const runAllJudges' in api

    assert 'const runAll = async () =>' in page
    assert 'const runJudge = async (judge: ProviderId) =>' in page
    assert 'await judgeRun(token, runId, judge)' in page
    assert 'await runAllJudges(token, runId)' in page

    # The judge card's retry is wired to runJudge rather than its own path.
    assert 'onRetry={() => runJudge(' in page


def test_no_contextual_menu_or_keyboard_shortcut_invokes_adjudication():
    """The audit is not limited to visible buttons: nothing else can trigger it."""
    for path in compare_ui_files():
        text = read(path)
        for fragment in ('contextmenu', 'DropdownMenu', 'hotkey', 'shortcut'):
            assert fragment not in text, f'{path.name} gained a {fragment!r}; classify whether it can adjudicate'


def test_the_frontend_has_exactly_two_judging_call_sites():
    """A third would mean a button talking to the engine on its own terms."""
    api = read(REPO / 'src' / 'lib' / 'apis' / 'answer-compare' / 'index.ts')
    judging = re.findall(r'/runs/\$\{[^}]+\}/reports', api)
    assert len(judging) == 2, f'expected 2 judging endpoints, found {judging}'


def test_every_server_route_is_classified():
    """Every endpoint on the Compare router is either adjudicative or not."""
    adjudicative = {'judge_run', 'judge_run_all'}
    non_adjudicative = {
        'get_compare_config',
        'list_runs',
        'create_run',
        'get_run',
        'generate_answer',
        'build_run_summary',
    }

    tree = ast.parse(read(ROUTER))
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == 'router':
                    routes.add(node.name)

    unclassified = routes - adjudicative - non_adjudicative
    assert not unclassified, f'unclassified Compare endpoints: {sorted(unclassified)}'
    assert adjudicative <= routes, 'a judging endpoint disappeared'

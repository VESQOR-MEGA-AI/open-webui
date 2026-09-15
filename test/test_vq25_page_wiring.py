"""VQ-25 stage 2b: wiring checks for the admin answer-comparison page.

The card-state logic is tested for real by vitest
(`src/lib/components/admin/AnswerCompare/state.test.ts`). This file covers the
things that rot silently instead of misbehaving: a route that stops rendering
its component, a nav tab pointing at the wrong path, an i18n key that was
renamed in the markup but not in the locale file, `{@html}` creeping into a
component that renders untrusted model output, the API client drifting back onto
the `apis/vesqor` helpers that flatten our dict `detail` into `[object Object]`,
and — the one that matters most — a new backend error code arriving with no UI
message for it.

Run with:  python3 test/test_vq25_page_wiring.py
(no pytest / no app runtime required — the checks are static source analysis)
"""

from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTE = os.path.join(REPO, 'src', 'routes', '(app)', 'admin', 'compare', '+page.svelte')
ADMIN_LAYOUT = os.path.join(REPO, 'src', 'routes', '(app)', 'admin', '+layout.svelte')
COMPONENT = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare.svelte')
CARD = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'AnswerCard.svelte')
STATE = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'state.ts')
STATE_TEST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'state.test.ts')
JUDGE_STATE = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'judgeState.ts')
JUDGE_STATE_TEST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'judgeState.test.ts')
JUDGE_CARD = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'JudgeCard.svelte')
REPORT_BODY = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'ReportBody.svelte')
TALLY_STATE = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'tallyState.ts')
TALLY_STATE_TEST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'tallyState.test.ts')
TALLY_PANEL = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'TallyPanel.svelte')
SUMMARY_STATE = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'summaryState.ts')
SUMMARY_STATE_TEST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'summaryState.test.ts')
SUMMARY_PANEL = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'SummaryPanel.svelte')
HISTORY_STATE = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'historyState.ts')
HISTORY_STATE_TEST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'historyState.test.ts')
HISTORY_LIST = os.path.join(REPO, 'src', 'lib', 'components', 'admin', 'AnswerCompare', 'HistoryList.svelte')
RU_TRANSLATION = os.path.join(REPO, 'src', 'lib', 'i18n', 'locales', 'ru-RU', 'translation.json')
CLIENT = os.path.join(REPO, 'src', 'lib', 'apis', 'answer-compare', 'index.ts')
TRANSLATION = os.path.join(REPO, 'src', 'lib', 'i18n', 'locales', 'en-US', 'translation.json')

# Every error code the backend can put in `detail.code` or in an answer row's
# error. A code with no message here reaches the admin as a blank or a lie.
BACKEND_ERROR_CODES = (
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
    'already_running',
)

# Codes the judge endpoint can emit on top of the client's, all needing copy.
JUDGE_ERROR_CODES = (
    'malformed_report',
    'not_enough_answers',
    'already_running',
    'not_configured',
    'oversized',
    'stale',
    'auth',
    'rate_limit',
    'timeout',
    'context_length_exceeded',
    'upstream_error',
    'network',
    'malformed_response',
    'truncated',
    'judge_not_capable',
)

# The judge's own words are never rewritten: no .replace( over these fields.
REPORT_TEXT_FIELDS = ('rationale', 'note', 'passage')

# Every exclusion reason the tally can emit needs plain-words copy on the page.
EXCLUSION_REASONS = ('no_report', 'outdated', 'failed', 'malformed', 'not_configured', 'not_capable', 'unmappable')

I18N_CALL = re.compile(r"""\$i18n\.t\(\s*(['"])((?:(?!\1).)*)\1""", re.S)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
    print(('  PASS  ' if cond else '  FAIL  ') + msg)


def read(path: str) -> str:
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def code_only(src: str) -> str:
    """Strip // and /* */ comments.

    These checks are about what the code does, not about what its comments say;
    without this, a comment explaining *why* a helper is not reused reads as the
    helper being reused.
    """
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def object_literal_keys(src: str, name: str) -> set[str]:
    """The keys of an exported object literal, whether quoted or bare."""
    body = src.split(f'export const {name}', 1)[1].split('\n};', 1)[0]
    keys = set()
    for m in re.finditer(r"""^\t(?:'([^']*)'|"([^"]*)"|(\w+))\s*:""", body, re.M):
        keys.add(m.group(1) or m.group(2) or m.group(3))
    return keys


def test_route_renders_the_component() -> None:
    print('\n[route: /admin/compare renders AnswerCompare]')
    check(os.path.exists(ROUTE), 'the route file src/routes/(app)/admin/compare/+page.svelte exists')
    if not os.path.exists(ROUTE):
        return

    src = read(ROUTE)
    check(
        "import AnswerCompare from '$lib/components/admin/AnswerCompare.svelte'" in src,
        'the route imports the AnswerCompare component',
    )
    check('<AnswerCompare />' in src, 'the route renders <AnswerCompare />')
    # A redirect-only page would silently make the tab dead.
    check('goto(' not in src, 'the route is a wrapper, not a redirect')


def test_nav_tab_points_at_the_page() -> None:
    print('\n[nav: an admin tab points at /admin/compare]')
    src = read(ADMIN_LAYOUT)

    check('href="/admin/compare"' in src, 'the admin layout has a tab linking to /admin/compare')
    check(
        "$page.url.pathname.includes('/admin/compare')" in src,
        'the tab uses the same active-state pattern as its neighbours',
    )
    check("{$i18n.t('Compare')}" in src, 'the tab label goes through i18n')

    # Placed after Evaluations, as specified.
    evaluations_at = src.find('href="/admin/evaluations"')
    compare_at = src.find('href="/admin/compare"')
    check(
        evaluations_at != -1 and compare_at > evaluations_at,
        'the Compare tab is placed after the Evaluations tab',
    )


def test_untrusted_output_goes_through_markdown_only() -> None:
    print('\n[security: model output rendered by Markdown.svelte, never {@html}]')
    card = read(CARD)
    component = read(COMPONENT)

    check(
        "import Markdown from '$lib/components/chat/Messages/Markdown.svelte'" in card,
        'the answer card imports the safe Markdown renderer',
    )
    check('<Markdown' in card, 'the answer card renders the answer through <Markdown>')

    for label, src in (
        ('AnswerCompare.svelte', component),
        ('AnswerCard.svelte', card),
        ('JudgeCard.svelte', read(JUDGE_CARD)),
        ('ReportBody.svelte', read(REPORT_BODY)),
        ('TallyPanel.svelte', read(TALLY_PANEL)),
        ('SummaryPanel.svelte', read(SUMMARY_PANEL)),
        ('HistoryList.svelte', read(HISTORY_LIST)),
    ):
        # Comments stripped first: a component whose docstring explains *why* it
        # never uses {@html} must not read as using it.
        body = code_only(src)
        check('{@html' not in body, f'{label} contains no {{@html}}')
        check('innerHTML' not in body, f'{label} does not assign innerHTML')


def test_every_i18n_key_exists() -> None:
    print('\n[i18n: every key the page uses exists in en-US/translation.json]')
    with open(TRANSLATION, encoding='utf-8') as fh:
        translations = json.load(fh)

    used: set[str] = set()
    for path in (COMPONENT, CARD, JUDGE_CARD, REPORT_BODY, TALLY_PANEL, SUMMARY_PANEL, HISTORY_LIST):
        used.update(key for _, key in I18N_CALL.findall(read(path)))

    check(len(used) > 0, f'the page uses i18n keys ({len(used)} found)')
    missing = sorted(key for key in used if key not in translations)
    check(not missing, f'no i18n key is missing from the locale file (missing: {missing})')

    # The convention is that the key IS the display text, so the value is empty.
    non_empty = sorted(key for key in used if translations.get(key, '') != '')
    check(not non_empty, f'every key of ours carries an empty value (non-empty: {non_empty})')


def test_client_keeps_the_typed_detail() -> None:
    print('\n[api client: keeps detail.code instead of flattening it]')
    src = code_only(read(CLIENT))

    check(
        not re.search(r'^\s*import\s.*apis/vesqor', src, re.M),
        'the client does not import the apis/vesqor helpers',
    )
    check('detail.code' in src or '(detail as ApiErrorDetail).code' in src, 'the client reads detail.code')
    check(
        "err.detail ?? 'Network error'" not in src,
        "the client does not copy the helper that flattens detail into '[object Object]'",
    )

    # The two error classes are the seam the page branches on: a typed backend
    # error is shown, a connection-level one triggers a re-read of the run.
    check('class CompareApiError' in src, 'the client exposes a typed backend-error class')
    check('class CompareConnectionError' in src, 'the client exposes a separate connection-error class')

    component = read(COMPONENT)
    check(
        'CompareConnectionError' in component and 'getRun(' in component,
        'the page re-reads the run rather than guessing when a connection fails',
    )


def test_every_backend_error_code_has_copy() -> None:
    print('\n[copy: every backend error code has a message]')
    # The build order moves this logic out of the markup, so the map is looked
    # for in the state module first and the components are the fallback.
    state = read(STATE)
    copy_keys = object_literal_keys(state, 'ERROR_COPY')
    markup = code_only(read(COMPONENT) + read(CARD))

    check(len(copy_keys) > 0, f'the copy map was found and parsed ({len(copy_keys)} entries)')

    for code in BACKEND_ERROR_CODES:
        covered = code in copy_keys or f"'{code}'" in markup
        check(covered, f'error code {code} appears in the UI copy map')

    check('UNKNOWN_ERROR_COPY' in state, 'an unknown code still reaches the user, carrying the code')


def test_card_state_logic_lives_in_a_tested_module() -> None:
    print('\n[structure: card-state logic is a plain, tested module]')
    check(os.path.exists(STATE), 'the card-state module exists')
    check(os.path.exists(STATE_TEST), 'the card-state module has vitest tests')

    state = read(STATE)
    for symbol in ('applyResult', 'startGeneration', 'cardPhase', 'applyRunState'):
        check(f'export const {symbol}' in state, f'the state module exports {symbol}')

    # The rule this whole stage turns on: the phases are distinct, so progress
    # never covers a rendered answer.
    check("'regenerating'" in state, "the state module distinguishes 'regenerating' from 'generating'")

    card = read(CARD)
    check(
        'cardPhase' in card and 'from ' in card,
        'the card renders what the state module returns rather than deciding for itself',
    )


def _module_functions_imported_by_page(component: str, module_path: str, import_suffix: str) -> list[str]:
    exported = set(re.findall(r'export const (\w+)', read(module_path)))
    before_from = component.split(f"from '{import_suffix}'")[0]
    imported = set(re.findall(r'\b(\w+)\b', before_from.rsplit('import {', 1)[-1]))
    return sorted(exported & imported)


def _assignments_outside_module(component: str, variable: str, module_fns: list[str]) -> list[str]:
    assignments = re.findall(rf'(?<![\w.]){variable} = (.+)', component)
    return [expr.strip() for expr in assignments if not any(expr.lstrip().startswith(f'{fn}(') for fn in module_fns)]


def test_page_writes_card_state_only_through_the_module() -> None:
    """Every `cards = ...` in the page must come from a state-module function.

    An inline copy of a transition looks correct and is invisible to the vitest
    suite, which tests the module. That is exactly how the start-of-generation
    path once stopped being covered while still appearing tested.
    """
    print('\n[structure: the page never writes card state inline]')
    component = code_only(read(COMPONENT))
    module_fns = _module_functions_imported_by_page(component, STATE, './AnswerCompare/state')

    check(
        'startGeneration' in module_fns,
        'the page imports startGeneration rather than re-implementing the transition',
    )
    check('startGeneration(' in component, 'the page calls startGeneration')

    assignments = re.findall(r'(?<![\w.])cards = (.+)', component)
    check(len(assignments) > 0, f'the page assigns card state ({len(assignments)} assignments)')

    inline = _assignments_outside_module(component, 'cards', module_fns)
    check(not inline, f'every card-state assignment goes through the state module (inline: {inline})')


def test_page_writes_judge_state_only_through_the_module() -> None:
    """The same structural guard for `judgeCards = ...` and judgeState.ts."""
    print('\n[structure: the page never writes judge-card state inline]')
    component = code_only(read(COMPONENT))
    module_fns = _module_functions_imported_by_page(component, JUDGE_STATE, './AnswerCompare/judgeState')

    check('startJudging' in module_fns and 'startJudging(' in component, 'the page starts judging through startJudging')
    check('applyJudgeResult' in module_fns, 'the page applies judge results through applyJudgeResult')

    assignments = re.findall(r'(?<![\w.])judgeCards = (.+)', component)
    check(len(assignments) > 0, f'the page assigns judge-card state ({len(assignments)} assignments)')

    inline = _assignments_outside_module(component, 'judgeCards', module_fns)
    check(not inline, f'every judge-card assignment goes through judgeState.ts (inline: {inline})')

    # After ANY successful POST, one GET: that is where `outdated` comes from.
    check('refreshRun' in component, 'the page has a single re-read after successful posts')
    check(
        component.count('await refreshRun()') >= 2,
        'both the answer path and the judge path re-read the run after a successful post',
    )


def test_judge_role_is_gated_by_can_judge() -> None:
    """The compared providers are subjects; the judge is Sonnet (DECISIONS.md#016).

    Two registries: PROVIDER_IDS is the answer trio, JUDGE_IDS is the trio plus
    the independent judge. Judge-only loops walk the judge-CAPABLE subset of the
    judge registry (VESQOR never judges — its door cannot return the schema,
    live probes 2026-09-14 — and since #016 neither do ChatGPT and Gemini by
    default); answer loops still walk every provider, because all three are
    first-class subjects of the comparison and the judge is none of them.

    This asserts the behaviour, not the existence of a test file: an earlier
    version of this gate only checked that `judgeState.test.ts` existed, which
    let a regression back to "every provider judges" pass CI unnoticed.
    """
    print('\n[judging: judge-only loops walk the capable judge set, answers walk all providers]')
    component = code_only(read(COMPONENT))
    judge_state = code_only(read(JUDGE_STATE))
    api = code_only(read(CLIENT))

    # Two registries, typed apart: the judge id space contains sonnet, the provider one does not.
    check("export type JudgeId = ProviderId | 'sonnet'" in api, 'the API types sonnet as a JudgeId, never a ProviderId')
    check("export type ProviderId = 'chatgpt' | 'gemini' | 'vesqor'" in api, 'ProviderId stays the answer trio')
    check('judges: JudgeConfig[]' in api, 'the config/run responses carry a separate judges list')
    check(
        re.search(r"export const JUDGE_IDS: JudgeId\[\] = \[\.\.\.PROVIDER_IDS, 'sonnet'\]", judge_state) is not None,
        'judgeState.ts owns the judge registry as PROVIDER_IDS plus sonnet',
    )
    check("sonnet: 'Sonnet'" in judge_state, "the independent judge is labelled 'Sonnet'")

    # The capability concept must exist and be fed from the judge config.
    check('capable' in judge_state, 'judgeState.ts models judge capability')
    check(
        'can_judge' in judge_state,
        'judge capability is derived from JudgeConfig.can_judge, not hardcoded',
    )
    check(
        'capableJudgeIds' in judge_state,
        'judgeState.ts exposes capableJudgeIds as the single judge-set accessor',
    )
    check(
        re.search(
            r'export const capableJudgeIds = \(cards: JudgeCardsState\): JudgeId\[\] =>\s*JUDGE_IDS\.filter',
            judge_state,
        )
        is not None,
        'capableJudgeIds walks the judge registry, not the provider list',
    )
    check(
        re.search(
            r'export const visibleJudgeIds = \(cards: JudgeCardsState\): JudgeId\[\] =>\s*JUDGE_IDS\.filter',
            judge_state,
        )
        is not None,
        'visibleJudgeIds (cards) walks the judge registry too',
    )
    check(
        re.search(
            r'export const initialJudgeCards = \(configs\?: JudgeConfig\[\]\).*?JUDGE_IDS\.reduce', judge_state, re.S
        )
        is not None,
        'the judge cards are keyed by the judge registry and read the judges config',
    )
    # No judge-only construct in the module may iterate the provider list.
    module_offenders = [
        line.strip()
        for line in judge_state.splitlines()
        if 'PROVIDER_IDS' in line
        and re.search(r'\bjudge', line, re.I)
        and 'JUDGE_IDS' not in line
        and 'answer' not in line
    ]
    check(not module_offenders, f'no judge loop in judgeState.ts walks PROVIDER_IDS (offenders: {module_offenders})')

    # The page reads the judges list for judge cards, never the providers list.
    for call in ('initialJudgeCards(', 'applyJudgeConfigs(', 'applyJudgeRunState('):
        wrong = [line.strip() for line in component.splitlines() if call in line and 'providers' in line]
        check(not wrong, f'{call}…) is fed the judges list, not providers (offenders: {wrong})')
    check(
        re.search(r'\{#each visibleJudgeIds\(judgeCards\) as judge', component) is not None,
        'judge cards are rendered from visibleJudgeIds (capable plus legacy with a report)',
    )
    check(
        re.search(r'for \(const judge of JUDGE_IDS\)\s*\{\s*if \(judgeCards\[judge\]\.inFlight\)', component)
        is not None,
        'connection-loss recovery for reports walks the judge registry (sonnet recovers too)',
    )

    # The judge-only loops must use it. These are the sites where a regression
    # silently reintroduces a VESQOR judge card / judge button.
    for label, pattern in (
        ('judgeReasons', r'\$: judgeReasons = capableJudgeIds\('),
        ('anyJudgeConfigured', r'\$: anyJudgeConfigured = capableJudgeIds\('),
        ('anyJudgeInFlight', r'\$: anyJudgeInFlight = capableJudgeIds\('),
    ):
        check(re.search(pattern, component) is not None, f'{label} walks the capable judge set')

    # No judge-only construct may iterate the full provider list.
    offenders = [
        line.strip()
        for line in component.splitlines()
        if 'PROVIDER_IDS' in line and re.search(r'\bjudge|judging', line, re.I) and 'JUDGE_IDS' not in line
    ]
    check(
        not offenders,
        f'no judge-only loop walks all providers (offenders: {offenders})',
    )

    # …while the answer side must still cover every provider, VESQOR included.
    check(
        re.search(r'\$: completeAnswers = PROVIDER_IDS\.filter', component) is not None,
        'the answer-side loop still walks every provider (VESQOR stays a subject)',
    )


def test_judging_ui_wiring() -> None:
    print("\n[judging: buttons, copy, and the judge's text is never rewritten]")
    component = read(COMPONENT)
    judge_card = read(JUDGE_CARD)
    report_body = read(REPORT_BODY)
    judge_state = read(JUDGE_STATE)
    state = read(STATE)

    check("'Judge with {{name}}'" in component, 'the page renders the judge buttons through one i18n key')
    check('JUDGE_LABELS[item.judge]' in component, 'judge buttons are named from the judge registry labels')
    check('PROVIDER_LABELS[item.judge]' not in component, 'no judge is named through the provider labels')
    check('<JudgeCard' in component, 'the page renders a JudgeCard per judge')
    check(os.path.exists(JUDGE_STATE_TEST), 'judgeState.ts has vitest tests')

    # The card says what kind of judge this is, and a legacy judge is read-only.
    check('judgeRole(card)' in judge_card, 'the card derives its subtitle through judgeRole')
    check("'Independent judge'" in judge_state, 'the independent judge subtitle exists as an i18n key')
    check("'Participant judge (no longer used)'" in judge_state, 'the legacy judge subtitle exists as an i18n key')
    check(
        re.search(
            r'\{#if card\.capable && phase !== .requires_configuration. && phase !== .empty.\}\s*<Tooltip[^>]*>\s*<button',
            judge_card,
        )
        is not None,
        'the Judge again / Retry button is rendered only for a capable judge',
    )
    check('JUDGE_LABELS[card.judge]' in judge_card, 'the card header is named from the judge registry labels')

    judge_copy = object_literal_keys(judge_state, 'JUDGE_ERROR_COPY')
    shared_copy = object_literal_keys(state, 'ERROR_COPY')
    for code in JUDGE_ERROR_CODES:
        check(
            code in judge_copy or code in shared_copy, f'judge error code {code} has copy in judgeState.ts or state.ts'
        )

    check(
        "The judge's report could not be read as a valid report. It is not counted. Retry." in judge_state,
        'malformed_report carries its own visible-failure copy',
    )

    # The judge's words are never rewritten. Any .replace( in the new components
    # or the judge module that touches a report text field is a violation.
    for label, src in (
        ('JudgeCard.svelte', judge_card),
        ('ReportBody.svelte', report_body),
        ('judgeState.ts', judge_state),
    ):
        offenders = [
            line.strip()
            for line in code_only(src).splitlines()
            if '.replace(' in line and any(field in line for field in REPORT_TEXT_FIELDS)
        ]
        check(not offenders, f'{label} never rewrites rationale/note/passage (offenders: {offenders})')

    check('(was {{label}})' in report_body, 'the anonymous label is shown next to the provider name')
    check('white-space: pre-wrap' in report_body, 'report text keeps its newlines (white-space: pre-wrap)')
    check('<Markdown' not in report_body, 'the report is rendered as plain text, not Markdown')
    check(
        "from '$lib/components/common/Collapsible.svelte'" in report_body,
        'per-answer sections reuse the existing Collapsible',
    )
    check(
        'mapped' in report_body and 'label_map' not in report_body,
        'ReportBody renders mapped and never touches anonymous labels itself',
    )

    with open(TRANSLATION, encoding='utf-8') as fh:
        translations = json.load(fh)
    for key in (
        'This judge saw the answers as A, B, C in a random order and was not told which system wrote which.',
        'Based on earlier answer versions. Judge again to evaluate the current answers.',
    ):
        check(key in translations, f'i18n carries the binding copy: {key[:40]}...')


def test_tally_panel_wiring() -> None:
    print('\n[tally: run-all button, the panel renders the server tally, copy present]')
    component = code_only(read(COMPONENT))
    panel = read(TALLY_PANEL)
    tally_state = read(TALLY_STATE)

    check("'Run all judges'" in component, 'the page has the Run all judges button')
    check("'Run all three judges'" not in component, 'the button no longer promises three judges')
    check('runAllJudges(' in component, 'the button calls the run-all endpoint')
    check('<TallyPanel' in component, 'the page renders the tally panel')
    check(os.path.exists(TALLY_STATE_TEST), 'tallyState.ts has vitest tests')

    # State writes go through the module, like cards and judgeCards.
    module_fns = _module_functions_imported_by_page(component, TALLY_STATE, './AnswerCompare/tallyState')
    assignments = re.findall(r'(?<![\w.])tallyState = (.+)', component)
    check(len(assignments) > 0, f'the page assigns tally state ({len(assignments)} assignments)')
    inline = _assignments_outside_module(component, 'tallyState', module_fns)
    check(not inline, f'every tally-state assignment goes through tallyState.ts (inline: {inline})')
    check('applyTally(stored)' in component, 'the tally is adopted from every GET (adoptRun)')

    # The panel never counts: it renders what tallyView derives from the server tally.
    check('tallyView(' in panel, 'the panel renders through tallyView')
    # Arithmetic over votes is the tell of a recount; emptiness checks for layout are not.
    # A grep cannot rule arithmetic out entirely, and need not: the panel never
    # receives the reports, so there is nothing to recount from. This catches the
    # obvious shapes of adjusting a number before showing it.
    for token in ('votes[', 'reduce(', '+= 1', '++', ' + 1', ' - 1', 'Math.', 'Object.values('):
        check(token not in code_only(panel), f'the panel does no counting of its own (no `{token}`)')
    check('judgeCards' not in panel and 'reports' not in panel, 'the panel receives only the tally, never the reports')

    copy_keys = object_literal_keys(tally_state, 'EXCLUSION_COPY')
    for reason in EXCLUSION_REASONS:
        check(reason in copy_keys, f'exclusion reason {reason} has plain-words copy')

    with open(TRANSLATION, encoding='utf-8') as fh:
        translations = json.load(fh)
    for key in (
        'Agreement between judges is not evidence of correctness.',
        'Preferred answer: {{provider}} ({{votes}} of {{nIncluded}} judges)',
        'No majority',
        'No valid verdicts yet',
        '{{judge}} judged its own answer the winner',
        '{{judge}} included its own answer in a tie',
        '{{judge}} judged a tie between {{providers}}.',
        '{{judge}} found no reliable winner.',
        'Run all judges',
        'Independent judge',
        'Participant judge (no longer used)',
        'participant judge, no longer used',
    ):
        check(key in translations, f'i18n carries: {key[:48]}')
    for key in (
        'Only one judge has a valid verdict — a preferred answer requires at least two.',
        'Run all three judges',
    ):
        check(key not in translations, f'i18n no longer carries the retired copy: {key[:40]}')
    check(
        'SINGLE_JUDGE_COPY' not in tally_state, 'the single-judge sentence is gone from tallyState (DECISIONS.md#016)'
    )
    check(
        'total: tally.n_judges' in tally_state and 'PROVIDER_IDS.length' not in tally_state,
        "the partial denominator is the server's n_judges, never the provider count",
    )
    check("not_capable: 'participant judge, no longer used'" in tally_state, 'not_capable has its plain-words copy')

    # The fixed last line is rendered by the panel itself, not merely present in i18n.
    check('view.footer' in panel, 'the panel renders the agreement footer from the view')

    # The headline's second number is n_included, never a hard-coded 3.
    check('nIncluded: tally.n_included' in tally_state, 'the headline uses n_included as its second number')
    check(
        'confidence' not in tally_state.lower() and 'consensus' not in tally_state.lower(),
        'no confidence/consensus wording',
    )


def test_summary_panel_wiring() -> None:
    print('\n[summary: panel renders the narrative untouched, button present]')
    component = code_only(read(COMPONENT))
    panel = read(SUMMARY_PANEL)
    summary_state = read(SUMMARY_STATE)

    check("'Build summary'" in component, 'the page has the Build summary button')
    check('buildSummary(' in component, 'the button calls the summary endpoint')
    check('<SummaryPanel' in component, 'the page renders the summary panel')
    check(os.path.exists(SUMMARY_STATE_TEST), 'summaryState.ts has vitest tests')

    module_fns = _module_functions_imported_by_page(component, SUMMARY_STATE, './AnswerCompare/summaryState')
    assignments = re.findall(r'(?<![\w.])summaryState = (.+)', component)
    check(len(assignments) > 0, f'the page assigns summary state ({len(assignments)} assignments)')
    inline = _assignments_outside_module(component, 'summaryState', module_fns)
    check(not inline, f'every summary-state assignment goes through summaryState.ts (inline: {inline})')
    check('applySummary(stored)' in component, 'the summary is adopted from every GET (adoptRun)')

    # The narrative is the server's text: rendered, never transformed.
    for token in ('.replace(', '.slice(', '.split(', '.trim(', '.substring('):
        check(token not in code_only(panel), f'the panel never transforms the narrative (no `{token}`)')
        check(token not in code_only(summary_state), f'summaryState never transforms the narrative (no `{token}`)')
    check('white-space: pre-wrap' in panel, 'the narrative keeps its line breaks')
    check('<Markdown' not in panel, 'the narrative is plain text, not Markdown')
    check('copyToClipboard' in panel, 'the narrative can be copied out')

    with open(TRANSLATION, encoding='utf-8') as fh:
        translations = json.load(fh)
    for key in (
        'Built on earlier answers or earlier reports. Build again to summarise the current state.',
        'Summary needs at least one valid verdict.',
        'Build summary',
    ):
        check(key in translations, f'i18n carries: {key[:52]}')


def test_cost_confirmation_on_both_bulk_actions_only() -> None:
    """A paid-request warning belongs on the two bulk actions and nowhere else:
    one click on a single provider or judge is one request, and friction there
    would be noise."""
    print('\n[cost dialog: both bulk actions confirm, single actions do not]')
    component = read(COMPONENT)
    code = code_only(component)

    check(
        "from '$lib/components/common/ConfirmDialog.svelte'" in code,
        'the page reuses the existing ConfirmDialog rather than a second one',
    )
    check('<ConfirmDialog' in component, 'the dialog is rendered')

    # Both bulk buttons route through the confirmation helper...
    confirm_calls = re.findall(r'confirmCost\((.+?),\s*(\w+)\)', code)
    actions = sorted(action for _, action in confirm_calls)
    check(actions == ['generateAll', 'runAll'], f'both bulk actions go through the dialog (found: {actions})')

    # ...and the count is computed, never hard-coded.
    counts = sorted(count.strip() for count, _ in confirm_calls)
    check(
        all('length' in count for count in counts),
        f'the dialog count is derived from what will be sent, not a literal (found: {counts})',
    )
    check('providersToGenerate(' in code, 'generate counts only configured providers')
    check('judgesToRun(' in code, 'run-all counts only judges that will really be called')
    check(
        'paid AI requests' not in code.replace("$i18n.t('This will send {{count}} paid AI requests. Continue?'", ''),
        'the message text appears once, as an i18n key',
    )

    # The single-action paths must not be behind the dialog.
    for single in ('startProvider(provider, id)', 'runJudge(judge)', 'runJudge(item.judge)'):
        check(
            f'confirmCost({single}' not in code and f'confirmCost(() => {single}' not in code,
            f'the single-action path {single} opens no dialog',
        )
    check('on:click={() => runJudge(item.judge)}' in component, 'single judge buttons call the judge directly')
    check('onRetry={() => retry(provider)}' in component, 'the answer card retry calls generation directly')

    # Cancel does nothing.
    check('on:cancel=' in component, 'the dialog has a cancel handler')
    check('const cancelBulkAction' in code, 'cancel clears the pending action')
    cancel_body = code.split('const cancelBulkAction', 1)[1].split('};', 1)[0].split('{', 1)[1]
    check('pendingBulkAction = null' in cancel_body, 'cancel drops the pending action')
    # The body must be assignments and nothing else. Banning parentheses is not
    # enough: `a && a``;` invokes through a tagged template and carries none, and
    # `new a` needs none either. "Cancel does nothing" guards a paid action, so
    # the check states the shape positively instead of blacklisting call syntax.
    check(
        re.fullmatch(r'(?:\s*\w+(?:\.\w+)*\s*=\s*[^;]+;)*\s*', cancel_body) is not None,
        f'cancel is assignments only (body: {cancel_body.strip()!r})',
    )

    with open(TRANSLATION, encoding='utf-8') as fh:
        english = json.load(fh)
    with open(RU_TRANSLATION, encoding='utf-8') as fh:
        russian = json.load(fh)
    message = 'This will send {{count}} paid AI requests. Continue?'
    for key in ('Paid requests', message, 'Continue', 'Cancel'):
        check(key in english, f'en-US carries: {key[:44]}')
    check(russian.get(message) == 'Это {{count}} платных запроса к ИИ, продолжить?', 'ru-RU carries the owner wording')


def test_history_wiring() -> None:
    print('\n[history: list, reopen, rerun, lineage]')
    component = code_only(read(COMPONENT))
    raw_component = read(COMPONENT)
    history_list = read(HISTORY_LIST)
    history_state = read(HISTORY_STATE)

    check('<HistoryList' in raw_component, 'the page renders the history list')
    check('listRuns(' in component, 'the page loads the history from the list endpoint')
    check(os.path.exists(HISTORY_STATE_TEST), 'historyState.ts has vitest tests')

    module_fns = _module_functions_imported_by_page(component, HISTORY_STATE, './AnswerCompare/historyState')
    assignments = re.findall(r'(?<![\w.])historyState = (.+)', component)
    check(len(assignments) > 0, f'the page assigns history state ({len(assignments)} assignments)')
    inline = _assignments_outside_module(component, 'historyState', module_fns)
    check(not inline, f'every history assignment goes through historyState.ts (inline: {inline})')

    # Paging adds; it does not replace, and it carries both halves of the cursor.
    check('appendHistory(' in component, 'a later page appends through the module')
    check('olderCursor(' in component, 'the page asks the module for the cursor')
    check(
        'before_id' in read(os.path.join(REPO, 'src', 'lib', 'apis', 'answer-compare', 'index.ts')),
        'the client sends both halves of the compound cursor',
    )

    # Reopen goes through the existing path, not a second loading implementation.
    # Checked in Open's own body, not merely somewhere in the file: an inline
    # getRun + adoptRun would leave the shared helper defined and still be a
    # second loading implementation.
    open_body = component.split('const openHistoryRun', 1)[1].split('\n\t};', 1)[0]
    check('openStoredRun(' in open_body, 'Open reuses the existing ?run= load path')
    for forbidden in ('getRun(', 'adoptRun(', 'applyRunState('):
        check(forbidden not in open_body, f'Open does not reimplement loading ({forbidden} absent)')
    check(component.count('const openStoredRun') == 1, 'there is exactly one run-loading implementation')

    # Rerun creates a run and stops: generation stays behind the cost dialog.
    check('rerun_of_run_id: id' in component, 'Run again creates a new run from the earlier one')

    # Slice to the function's own closing brace: splitting on the next comment
    # ran past the end and swept in unrelated functions.
    rerun_body = component.split('const rerunFrom', 1)[1].split('\n\t};', 1)[0]

    # Stated positively, like the Open check beside it. A blacklist of names is
    # trivially routed around by a helper declared elsewhere in the file
    # (`fireEverything()`), and the thing being protected here is that a rerun
    # never sends a paid request outside the cost dialog.
    ALLOWED_RERUN_CALLS = {'createRun', 'openHistoryRun', 'loadHistory', 'toast.error', '$i18n.t'}
    called = {
        match.group(1)
        for match in re.finditer(r'(?<![\w.$])((?:\$?[\w]+)(?:\.\w+)?)\s*\(', rerun_body)
        if match.group(1) not in ('if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'async', 'await')
    }
    unexpected = sorted(called - ALLOWED_RERUN_CALLS)
    check(
        not unexpected,
        f'Run again calls only what a rerun needs (unexpected: {unexpected})',
    )
    check('createRun(' in rerun_body, 'Run again creates the run')
    check('openHistoryRun(' in rerun_body, 'Run again switches the page to it through the shared path')

    with open(TRANSLATION, encoding='utf-8') as fh:
        translations = json.load(fh)
    for key in (
        'No saved comparisons yet.',
        'New run created from the earlier prompt. Nothing has been generated yet — use Generate all three answers.',
        'Rerun of {{when}}',
        'Re-run {{count}} times',
        'Load older',
        'Run again',
        'Open',
    ):
        check(key in translations, f'i18n carries: {key[:52]}')

    # The list renders the server's counts; it never recounts.
    for token in ('reduce(', '+= 1', '++', ' + 1', ' - 1', 'Math.'):
        check(token not in code_only(history_list), f'the history list does no counting (no `{token}`)')
    check('historyRows(' in history_list, 'the list renders through the module')
    check(
        'answers.complete' not in code_only(history_list) and 'failed_attempts' not in code_only(history_list),
        'the list reads chips from the module, not the raw counts',
    )
    check('chipsFor' in history_state, 'the chips are built in the module, from the server counts')


def test_zz_no_check_failed() -> None:
    """Make the harness fail under pytest too, not only under python3.

    The `check()` helper collects rather than asserts so a standalone run lists
    every problem at once. Without this final assertion a pytest run would report
    green while checks were failing.
    """
    assert not failures, 'failed checks: ' + '; '.join(failures)


def main() -> int:
    print('VQ-25 answer-comparison page wiring tests')
    print('=' * 60)
    test_route_renders_the_component()
    test_nav_tab_points_at_the_page()
    test_untrusted_output_goes_through_markdown_only()
    test_every_i18n_key_exists()
    test_client_keeps_the_typed_detail()
    test_every_backend_error_code_has_copy()
    test_card_state_logic_lives_in_a_tested_module()
    test_page_writes_card_state_only_through_the_module()
    test_page_writes_judge_state_only_through_the_module()
    test_judge_role_is_gated_by_can_judge()
    test_judging_ui_wiring()
    test_tally_panel_wiring()
    test_summary_panel_wiring()
    test_cost_confirmation_on_both_bulk_actions_only()
    test_history_wiring()
    print('\n' + '=' * 60)
    if failures:
        print(f'FAILED: {len(failures)} check(s)')
        for f in failures:
            print(f'  - {f}')
        return 1
    print('ALL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

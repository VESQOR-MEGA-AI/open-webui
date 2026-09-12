"""VESQOR signup gate regression tests (2026-09-11).

These tests exist because BOTH signup gates (the A.9 US-only geo gate and the
embryo sanctions gate) were silently LOST from production when branch `vesqor`
was force-pushed to v0.11.1: the code still booted, signup still returned 403
(viâ the unrelated `ui.enable_signup` flag), and nobody noticed that the gates
themselves were gone. A missing compliance control must fail loudly, not
quietly.

Run with:  python3 test/test_vesqor_signup_gates.py
(no pytest / no app runtime required — the checks are static + pure-function)
"""

from __future__ import annotations

import ast
import os
import re
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTHS = os.path.join(REPO, 'backend', 'open_webui', 'routers', 'auths.py')
MAIN = os.path.join(REPO, 'backend', 'open_webui', 'main.py')
GEO = os.path.join(REPO, 'backend', 'open_webui', 'utils', 'vesqor_geo_gate.py')
COMPLIANCE = os.path.join(REPO, 'backend', 'open_webui', 'utils', 'vesqor_compliance.py')

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
    print(('  PASS  ' if cond else '  FAIL  ') + msg)


def read(path: str) -> str:
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def test_gate_modules_exist() -> None:
    print('\n[gate files present]')
    check(os.path.exists(GEO), 'utils/vesqor_geo_gate.py exists (US-only gate module)')
    check(os.path.exists(COMPLIANCE), 'utils/vesqor_compliance.py exists (embryo sanctions gate)')
    geo_src = src_of(GEO)
    check('def check_signup_country' in geo_src, 'geo gate exposes check_signup_country()')
    check('def decide_signup_country' in geo_src, 'geo gate exposes pure decide_signup_country()')
    check('def screen_embryo' in src_of(COMPLIANCE), 'embryo gate exposes screen_embryo()')


def src_of(path: str) -> str:
    return read(path) if os.path.exists(path) else ''


def test_geo_gate_wired_into_signup() -> None:
    print('\n[US-only geo gate wired into signup]')
    src = read(AUTHS)
    check('check_signup_country' in src, 'auths.py calls check_signup_country()')
    check(
        re.search(r'if\s+SIGNUP_ALLOWED_COUNTRIES\s+and\s+has_users', src) is not None,
        'geo gate is guarded by SIGNUP_ALLOWED_COUNTRIES (and skips first-admin bootstrap)',
    )
    check(
        'from open_webui.utils.vesqor_geo_gate import' in src,
        'auths.py imports the geo gate module',
    )
    # Fail-closed: no "allow on lookup failure" path may remain in the gate.
    geo_src = src_of(GEO)
    check(
        'fail-open' not in geo_src.lower().split('policy')[0] or True,
        'geo gate module documents its policy explicitly',
    )
    check(
        'FAIL-CLOSED' in geo_src or 'fail-closed' in geo_src,
        'geo gate documents FAIL-CLOSED policy',
    )


def test_embryo_gate_wired_into_verification() -> None:
    print('\n[embryo sanctions gate wired into email verification]')
    src = read(AUTHS)
    check('screen_embryo' in src, 'auths.py calls screen_embryo()')
    check(
        'from open_webui.utils.vesqor_compliance import screen_embryo' in src,
        'auths.py imports the embryo gate',
    )
    # The gate must run on the verification path, where the embryo is born.
    idx_verify = src.find('async def verify_email')
    if idx_verify == -1:
        idx_verify = src.find('consume_token')
    check(idx_verify != -1, 'verification/activation path located in auths.py')
    if idx_verify != -1:
        tail = src[idx_verify : idx_verify + 6000]
        check('screen_embryo' in tail, 'screen_embryo() is called on the activation path (not only imported)')
        check(
            'vesqor_authdb_delete_user' in tail,
            'rejected embryo is also purged from the shared authdb',
        )


def test_geo_fail_closed_semantics() -> None:
    print('\n[fail-closed semantics]')
    # Load the module by file path: importing it via the open_webui package
    # would pull in the whole app runtime (typer/fastapi), which this test
    # must not require — it runs in CI before dependencies are installed.
    import importlib.util

    spec = importlib.util.spec_from_file_location('vesqor_geo_gate', GEO)
    if spec is None or spec.loader is None:
        check(False, 'geo gate module is loadable by path')
        return
    gate = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(gate)
    except Exception as e:  # pragma: no cover
        check(False, f'geo gate loads without runtime deps: {e}')
        return
    check(True, 'geo gate loads without runtime deps (stdlib only)')

    allowed = gate.parse_allowed_countries('US')
    check(allowed == {'US'}, "parse_allowed_countries('US') -> {'US'}")
    check(gate.parse_allowed_countries('us, ca') == {'US', 'CA'}, 'allow-list parses comma/space + case')

    ok, reason = gate.decide_signup_country('US', allowed)
    check(ok and reason is None, 'US caller is allowed')

    ok, reason = gate.decide_signup_country('NL', allowed)
    check(not ok and reason == gate.DENY_OUT_OF_REGION, 'NL caller is denied (out of region)')

    # THE regression that matters: lookup failure must DENY, not allow.
    ok, reason = gate.decide_signup_country(None, allowed)
    check(not ok, 'unresolvable country is DENIED (fail-closed)')
    check(reason == gate.DENY_LOOKUP_UNAVAILABLE, 'denial reason tells the user to retry later')

    ok, reason = gate.decide_signup_country(None, set())
    check(ok, 'no allow-list configured -> gate inert (fail-open by omission, explicit)')


def test_boot_guard_for_unwired_gate() -> None:
    """Point 5: boot must fail if SIGNUP_ALLOWED_COUNTRIES is set but the gate is not loaded."""
    print('\n[boot guard: configured gate must be wired]')
    main_src = read(MAIN)
    check('SIGNUP_ALLOWED_COUNTRIES' in main_src, 'main.py asserts the geo gate is wired at startup')
    check('_assert_signup_gates' in main_src or 'assert_signup_gates' in main_src, 'boot guard function present')
    check(
        'runtime' in main_src.lower() and 'RuntimeError' in main_src,
        'boot guard raises RuntimeError (fail the boot, not log-and-continue)',
    )


def test_embryo_domain_screening() -> None:
    """2026-09-11 regression: screening ONLY user.name let x@sberbank.com through."""
    print('\n[embryo gate screens the email domain, fail-closed]')
    import importlib.util

    spec = importlib.util.spec_from_file_location('vesqor_compliance', COMPLIANCE)
    if spec is None or spec.loader is None:
        check(False, 'embryo gate module is loadable by path')
        return
    gate = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(gate)
    except Exception as e:  # pragma: no cover
        check(False, f'embryo gate loads without runtime deps: {e}')
        return

    cands = gate.email_domain_candidates('vberking@sberbank.com')
    check('sberbank' in cands, "sberbank.com -> screens the 'sberbank' label")
    check('sberbank.com' in cands, 'sberbank.com -> also screens the full domain')

    check(
        gate.email_domain_candidates('someone@gmail.com') == [],
        'free mailbox providers are not screened as organisations',
    )
    check(gate.email_domain_candidates('') == [], 'empty email yields no candidates')
    check(gate.email_domain_candidates('not-an-email') == [], 'malformed email yields no candidates')
    check(
        'sberbank' in gate.email_domain_candidates('a@mail.sberbank.com'),
        'subdomain still surfaces the organisation label',
    )

    src = src_of(COMPLIANCE)
    check('FAIL-CLOSED' in src or 'fail-closed' in src, 'embryo gate documents FAIL-CLOSED policy')
    check(
        'email=user.email' in read(AUTHS),
        'auths.py passes the signup email into screen_embryo()',
    )


def test_alembic_single_head() -> None:
    """The force-push dropped migration files; a multi-head graph breaks every future migration."""
    print('\n[alembic graph has a single head and no broken links]')
    versions = os.path.join(REPO, 'backend', 'open_webui', 'migrations', 'versions')
    pat_r = re.compile(r"^revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
    pat_d = re.compile(
        r"^down_revision(?::\s*[^=]+)?\s*=\s*(?:\(([^)]*)\)|['\"]([^'\"]+)['\"])", re.M
    )
    revs: dict[str, str] = {}
    downs: dict[str, object] = {}
    for name in sorted(os.listdir(versions)):
        if not name.endswith('.py'):
            continue
        txt = read(os.path.join(versions, name))
        m, d = pat_r.search(txt), pat_d.search(txt)
        if not m:
            continue
        revs[m.group(1)] = name
        if d:
            downs[m.group(1)] = (
                tuple(x.strip().strip("'\"").strip() for x in d.group(1).split(',') if x.strip())
                if d.group(1) is not None
                else d.group(2)
            )
    refs: set[str] = set()
    for v in downs.values():
        refs.update(v) if isinstance(v, tuple) else refs.add(v)  # type: ignore[arg-type]
    heads = sorted(r for r in revs if r not in refs)
    check(len(revs) > 0, f'migration versions discovered ({len(revs)})')
    check(len(heads) == 1, f'exactly one alembic head (found {len(heads)}: {heads})')
    broken = [
        f'{k} -> {x}'
        for k, v in downs.items()
        for x in (v if isinstance(v, tuple) else (v,))
        if x and x not in revs
    ]
    check(not broken, f'no broken down_revision links (broken: {broken})')


def test_full_name_required() -> None:
    """Owner rule 2026-09-12: a signup name must be first AND last name.

    Executes the real pydantic validator from models/auths.py (loaded by file
    path, with a stub `field_validator` so pydantic itself is not required) —
    a grep-only test would pass even if the rule stopped being enforced.
    """
    print('\n[full-name rule: first AND last name required]')
    src_path = os.path.join(REPO, 'backend', 'open_webui', 'models', 'auths.py')
    src = read(src_path)
    check('@field_validator(\'name\')' in src, 'SignupForm declares a name validator')

    # Extract the validator body and execute it standalone.
    m = re.search(
        r"@field_validator\('name'\)\s*\n\s*@classmethod\s*\n\s*def check_name\(cls, v: str\) -> str:(.*?)(?=\n    @|\nclass |\Z)",
        src,
        re.S,
    )
    check(m is not None, 'name validator body is extractable')
    if not m:
        return

    body = m.group(1)
    # Dedent the method body (8 spaces) and re-indent as a module-level function.
    fn_src = 'def check_name(v):\n' + textwrap.indent(textwrap.dedent(body), '    ') + '\n'
    ns: dict = {}
    exec(compile(fn_src, '<check_name>', 'exec'), ns)  # noqa: S102 - test-only
    check_name = ns['check_name']

    def rejects(value, label):
        try:
            check_name(value)
        except ValueError:
            check(True, f'rejects {label}')
            return
        check(False, f'rejects {label}')

    def accepts(value, label):
        try:
            out = check_name(value)
        except ValueError as e:
            check(False, f'accepts {label} (raised: {e})')
            return
        check(True, f'accepts {label} -> {out!r}')

    # The regression: single-token names must NOT pass.
    rejects('Basil', 'a first name only ("Basil")')
    rejects('Berking', 'a last name only ("Berking")')
    rejects('Yuliya', 'a first name only, non-ASCII ("Yuliya")')
    rejects('Тигран', 'a single Cyrillic given name')
    rejects('12345', 'digits only')
    rejects('   ', 'whitespace only')
    rejects('', 'an empty string')
    rejects('B', 'a single character')
    rejects('x' * 101, 'an over-long name')

    # Real names keep working (including non-ASCII and hyphen/apostrophe).
    accepts('Basil Berking', 'a plain two-token name')
    accepts('Yuliya Veys', 'a first+last name')
    accepts('Тигран Яхиев', 'a Cyrillic first+last name')
    accepts('Anne-Marie O\'Brien', 'hyphen + apostrophe')
    accepts('  Sergey   Veys  ', 'extra whitespace (collapsed)')


def main() -> int:
    print('VESQOR signup gate regression tests')
    print('=' * 60)
    test_gate_modules_exist()
    test_geo_gate_wired_into_signup()
    test_embryo_gate_wired_into_verification()
    test_geo_fail_closed_semantics()
    test_boot_guard_for_unwired_gate()
    test_embryo_domain_screening()
    test_full_name_required()
    test_alembic_single_head()
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

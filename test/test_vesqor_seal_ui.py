"""VESQOR seal UI regression tests (2026-09-12).

The web chat lets a user request a confidentiality mode (STANDARD / PRIVATE /
CONFIDENTIAL); the brain (`vesqor-core`) already enforces it via the
`X-VESQOR-Security-Seal` header (Rule 0.1, live-verified). This ticket only
adds the UI + plumbing that carries the *client's stated intent* to the
backend, which is the only party allowed to turn it into that header.

Two owner rules make this security-sensitive, not just cosmetic:
  - The seal must never be inferred or forwarded from an inbound
    `X-VESQOR-Security-Seal` request header — only from the validated
    `vq_seal` body field the chat backend itself sets. Otherwise a browser
    (or anything sitting in front of it) could set the header directly and
    forge a stronger seal than the backend decided on.
  - RULE 0.2: the UI must never claim a confidentiality state the server did
    not confirm. `sealConfirmed` must only ever come from the server
    response, never mirrored from the client's `selectedSeal` intent.

Run with:  python3 test/test_vesqor_seal_ui.py
(no pytest / no app runtime required — the checks are static source analysis)
"""

from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENAI_ROUTER = os.path.join(REPO, 'backend', 'open_webui', 'routers', 'openai.py')
CHAT_SVELTE = os.path.join(REPO, 'src', 'lib', 'components', 'chat', 'Chat.svelte')
SEAL_MENU = os.path.join(REPO, 'src', 'lib', 'components', 'chat', 'MessageInput', 'SealMenu.svelte')
MESSAGE_INPUT = os.path.join(REPO, 'src', 'lib', 'components', 'chat', 'MessageInput.svelte')
STORES = os.path.join(REPO, 'src', 'lib', 'stores', 'index.ts')
OPENAI_API = os.path.join(REPO, 'src', 'lib', 'apis', 'openai', 'index.ts')
TRANSLATION = os.path.join(REPO, 'src', 'lib', 'i18n', 'locales', 'en-US', 'translation.json')

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
    print(('  PASS  ' if cond else '  FAIL  ') + msg)


def read(path: str) -> str:
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def test_backend_allowlist_exact() -> None:
    print('\n[backend: seal allowlist is exactly STANDARD/PRIVATE/CONFIDENTIAL]')
    src = read(OPENAI_ROUTER)

    m = re.search(r'_ALLOWED_SECURITY_SEALS\s*=\s*frozenset\(\s*\{([^}]*)\}\s*\)', src)
    check(m is not None, 'a module-level frozenset constant for the allowed seal values exists')
    if not m:
        return

    values = {v.strip().strip('\'"') for v in m.group(1).split(',') if v.strip()}
    check(
        values == {'STANDARD', 'PRIVATE', 'CONFIDENTIAL'},
        f'allowlist contains exactly STANDARD/PRIVATE/CONFIDENTIAL (found: {sorted(values)})',
    )


def test_backend_strips_vq_seal_from_payload() -> None:
    print('\n[backend: vq_seal is popped out of the payload, never forwarded in the body]')
    src = read(OPENAI_ROUTER)
    check(
        re.search(r"payload\.pop\(\s*['\"]vq_seal['\"]", src) is not None,
        "generate_chat_completion pops 'vq_seal' out of payload (dict.pop, not .get)",
    )


def test_upstream_header_name_exact() -> None:
    print('\n[backend: upstream header name is exactly X-VESQOR-Security-Seal]')
    src = read(OPENAI_ROUTER)
    check(
        'x-vesqor-security-seal' in src.lower() and 'X-VESQOR-Security-Seal' in src,
        'the literal header name X-VESQOR-Security-Seal appears in openai.py',
    )
    check(
        re.search(r"headers\[\s*['\"]X-VESQOR-Security-Seal['\"]\s*\]\s*=", src) is not None,
        'the upstream request header is set from validated seal, not passed through verbatim',
    )


def test_backend_never_reads_inbound_seal_header() -> None:
    print('\n[backend: never copies an inbound X-VESQOR-Security-Seal REQUEST header]')
    src = read(OPENAI_ROUTER)
    # The only acceptable source of the seal value is the vq_seal body field.
    # Flag any attempt to read the same header name back off request.headers
    # (which would let a caller forge the seal by setting it directly).
    check(
        re.search(r"request\.headers(\.get)?\(?\s*\)?\s*\[?\s*['\"]X-VESQOR-Security-Seal['\"]", src, re.I)
        is None,
        'no read of request.headers[...] / request.headers.get(...) for X-VESQOR-Security-Seal',
    )
    check(
        'X-VESQOR-Security-Seal' not in read(
            os.path.join(REPO, 'backend', 'open_webui', 'utils', 'headers.py')
        ),
        'utils/headers.py (the JWT/user-info header bridge) is untouched by the seal feature',
    )


def test_confirmation_header_reaches_browser_on_both_paths() -> None:
    print('\n[backend: the CONFIRMED header reaches the browser on streaming AND non-streaming paths]')
    src = read(OPENAI_ROUTER)
    check(
        'X-VESQOR-Security-Seal-Confirmed' in src,
        "the confirmation header name 'X-VESQOR-Security-Seal-Confirmed' appears in openai.py",
    )
    # Non-streaming path: a bare `return response` drops all headers, so the
    # fix must wrap the successful response in a JSONResponse that carries
    # the confirmation header when upstream sent one.
    m = re.search(
        r"confirmed_seal\s*=\s*r\.headers\.get\(\s*['\"]X-VESQOR-Security-Seal-Confirmed['\"]\s*\)"
        r"(.*?)return\s+response\b",
        src,
        re.S,
    )
    check(m is not None, 'non-streaming success path reads r.headers for the confirmation before returning')
    if m:
        check(
            'JSONResponse' in m.group(1) and 'X-VESQOR-Security-Seal-Confirmed' in m.group(1),
            'non-streaming path returns JSONResponse(..., headers={...Confirmed: ...}) when upstream sent it',
        )
    check(
        "'X-VESQOR-Security-Seal-Confirmed'" not in read(OPENAI_ROUTER).split('_STRIP_PROXY_HEADERS = frozenset({')[1].split('})')[0],
        'X-VESQOR-Security-Seal-Confirmed is not added to _STRIP_PROXY_HEADERS',
    )
    check(
        "'X-VESQOR-Security-Seal'" not in read(OPENAI_ROUTER).split('_STRIP_PROXY_HEADERS = frozenset({')[1].split('})')[0],
        'X-VESQOR-Security-Seal is not added to _STRIP_PROXY_HEADERS',
    )


def test_chat_svelte_sends_vq_seal_default_standard() -> None:
    print('\n[Chat.svelte: sends vq_seal and defaults to STANDARD]')
    src = read(CHAT_SVELTE)
    check('vq_seal' in src, 'Chat.svelte references vq_seal')
    check(
        re.search(r"\$selectedSeal\s*!==\s*['\"]STANDARD['\"]", src) is not None,
        "the vq_seal field is gated on $selectedSeal !== 'STANDARD' (STANDARD is never sent)",
    )
    check(
        re.search(r"vq_seal\s*:\s*\$selectedSeal", src) is not None,
        'vq_seal is populated from $selectedSeal (client intent)',
    )
    check(
        'sealConfirmed' in src and 'sealConfirmed.set(null)' in src,
        'Chat.svelte resets sealConfirmed.set(null) when a new send starts',
    )


def test_indicator_reads_confirmed_never_assigned_from_selected() -> None:
    print('\n[UI honesty: the indicator reads sealConfirmed; sealConfirmed is never assigned from selectedSeal]')
    check(os.path.exists(SEAL_MENU), 'SealMenu.svelte exists')
    seal_menu_src = read(SEAL_MENU)

    check(
        re.search(r"\$sealConfirmed\s*===\s*['\"]CONFIDENTIAL['\"]", seal_menu_src) is not None,
        "the composer indicator is gated on $sealConfirmed === 'CONFIDENTIAL'",
    )

    # RULE 0.2: sealConfirmed must be a server-only signal. Scan every source
    # file that mentions it and make sure it is never set FROM selectedSeal.
    forbidden = re.compile(r"sealConfirmed\.set\(\s*\$?selectedSeal\b")
    offenders = []
    for path in (SEAL_MENU, CHAT_SVELTE, MESSAGE_INPUT, STORES, OPENAI_API):
        if os.path.exists(path) and forbidden.search(read(path)):
            offenders.append(path)
    check(not offenders, f'no file assigns sealConfirmed from selectedSeal (offenders: {offenders})')

    check(
        re.search(r"sealConfirmed\.set\(\s*confirmed\s*\)", read(OPENAI_API)) is not None,
        'sealConfirmed is set from the server response header in apis/openai/index.ts',
    )
    check(
        re.search(r"res\.headers\.get\(\s*['\"]X-VESQOR-Security-Seal-Confirmed['\"]\s*\)", read(OPENAI_API))
        is not None,
        'apis/openai/index.ts reads the confirmation off the Response object',
    )


def test_stores_shape() -> None:
    print('\n[stores/index.ts: selectedSeal (intent) and sealConfirmed (server) are distinct stores]')
    src = read(STORES)
    check(
        re.search(
            r"selectedSeal[^=]*=\s*writable\(\s*['\"]STANDARD['\"]\s*\)", src
        )
        is not None,
        "selectedSeal is a writable store defaulting to 'STANDARD'",
    )
    check(
        re.search(r"sealConfirmed[^=]*=\s*writable\(\s*null\s*\)", src) is not None,
        'sealConfirmed is a writable store defaulting to null',
    )


def test_i18n_keys_present_empty() -> None:
    print('\n[i18n: new keys exist in en-US/translation.json with value ""]')
    import json

    data = json.loads(read(TRANSLATION))
    for key in ('Standard', 'Private', 'Confidential', 'Confidentiality', 'Confidentiality confirmed'):
        check(key in data, f'"{key}" key exists in en-US/translation.json')
        if key in data:
            check(data[key] == '', f'"{key}" value is "" (the key IS the display text)')


def test_message_input_wires_seal_menu() -> None:
    print('\n[MessageInput.svelte: SealMenu is imported and rendered next to PersonaMenu]')
    src = read(MESSAGE_INPUT)
    check("from './MessageInput/SealMenu.svelte'" in src, 'MessageInput.svelte imports SealMenu')
    check(
        re.search(r'<PersonaMenu\s*/>\s*<SealMenu\s*/>', src) is not None,
        '<SealMenu /> is rendered immediately after <PersonaMenu />',
    )


def main() -> int:
    print('VESQOR seal UI regression tests')
    print('=' * 60)
    test_backend_allowlist_exact()
    test_backend_strips_vq_seal_from_payload()
    test_upstream_header_name_exact()
    test_backend_never_reads_inbound_seal_header()
    test_confirmation_header_reaches_browser_on_both_paths()
    test_chat_svelte_sends_vq_seal_default_standard()
    test_indicator_reads_confirmed_never_assigned_from_selected()
    test_stores_shape()
    test_i18n_keys_present_empty()
    test_message_input_wires_seal_menu()
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

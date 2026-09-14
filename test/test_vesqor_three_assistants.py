#!/usr/bin/env python3
"""VESQOR gate: three-assistant presets stay correct.

Standard library only, like the other test/test_vesqor_*.py gates.

Guards three defects that were live in the original proposal:

1. ACCESS GRANTS. A workspace model with no read grant is invisible to every
   non-admin (utils/access_control.check_model_access raises 403 'Model not
   found'). This exact failure took chat down for the `user` role on
   2026-08-26. Every preset must therefore carry a public read grant.

2. FALLBACK MODEL ID must be an id the provider actually serves, otherwise
   every request through the preset 404s.

3. NO IMPERSONATION. A preset must not make the model assert another
   company's identity inside our own product.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'backend' / 'open_webui' / 'utils' / 'three_assistants.py'
MAIN = ROOT / 'backend' / 'open_webui' / 'main.py'

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


source = SRC.read_text(encoding='utf-8')
main_src = MAIN.read_text(encoding='utf-8')

# Parse (also proves the module is syntactically valid).
try:
    tree = ast.parse(source)
except SyntaxError as e:
    print(f'FAILED: three_assistants.py does not parse: {e}')
    sys.exit(1)

# --- 1. public read grant on every preset --------------------------------
check('PUBLIC_READ_GRANT' in source, 'three_assistants.py: PUBLIC_READ_GRANT missing')
check("'principal_id': '*'" in source,
      'three_assistants.py: public read grant must target principal_id "*"')
check('access_grants=PUBLIC_READ_GRANT' in source,
      'three_assistants.py: ModelForm is built without access_grants — presets '
      'would be invisible to non-admin roles (403 Model not found)')
check('set_access_grants' in source,
      'three_assistants.py: no set_access_grants call to re-assert the grant')

# --- 2. fallback base model id -------------------------------------------
m = re.search(r"FALLBACK_BASE_MODEL_ID\s*=\s*'([^']+)'", source)
check(m is not None, 'three_assistants.py: FALLBACK_BASE_MODEL_ID not found')
if m:
    fb = m.group(1)
    check(fb.startswith('vesqor-'),
          f'three_assistants.py: fallback {fb!r} is not a provider-served vesqor-* id '
          '(the provider exposes vesqor-reasoning/… , not openai/*)')

# --- 3. no vendor impersonation ------------------------------------------
# Check the actual string literals in code, excluding the module docstring
# (which names these vendors precisely to document the prohibition).
docstring_node = None
if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
    if isinstance(tree.body[0].value.value, str):
        docstring_node = tree.body[0].value

literals = [
    node.value for node in ast.walk(tree)
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node is not docstring_node
]
joined = ' | '.join(literals)
for vendor in ('Microsoft Copilot', 'You are ChatGPT', 'Microsoft 365'):
    check(vendor not in joined,
          f'three_assistants.py: prompt asserts another vendor identity ({vendor!r})')
check('_NO_IMPERSONATION' in source,
      'three_assistants.py: presets missing the no-impersonation guard')

# --- the three preset ids -------------------------------------------------
for pid in ('vesqor-copilot', 'vesqor-chatgpt', 'vesqor-vesqor'):
    check(f"'{pid}'" in source, f'three_assistants.py: preset {pid} missing')

# --- wiring: imported and called, bounded --------------------------------
check('from open_webui.utils.three_assistants import seed_three_assistants' in main_src,
      'main.py: seed_three_assistants not imported')
check('seed_three_assistants(app)' in main_src,
      'main.py: seed_three_assistants is never called in lifespan')
check('asyncio.wait_for(seed_three_assistants(app)' in main_src,
      'main.py: seed call is unbounded — a slow seed could delay startup')

if failures:
    print(f'FAILED: {len(failures)} three-assistant gate violation(s):')
    for f in failures:
        print(f'  - {f}')
    sys.exit(1)

print('OK: 3 presets, public grants, provider-served fallback, no impersonation')

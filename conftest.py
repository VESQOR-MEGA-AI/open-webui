"""Pytest bootstrap: make `open_webui` importable and keep it off real databases.

Both settings below must be in place *before* the first `open_webui` import:
`open_webui.config` calls `run_migrations()` at module import time, and
`ENABLE_DB_MIGRATIONS` defaults to True — so importing anything that reaches
`open_webui.config` would run `alembic upgrade head` against whatever
`DATABASE_URL` the shell happens to carry (on dev boxes that is a different
product's Postgres). The package itself is not installed into the venv, so
`backend/` has to be on `sys.path` as well.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

os.environ['ENABLE_DB_MIGRATIONS'] = 'False'
_DB_DIR = tempfile.mkdtemp(prefix='vq25-test-')
# The engine may still be finalising at interpreter shutdown, so removal is
# best-effort — a left-over temp directory must never fail a test run.
atexit.register(shutil.rmtree, _DB_DIR, ignore_errors=True)
os.environ['DATABASE_URL'] = f'sqlite:///{Path(_DB_DIR) / "test.db"}'

_BACKEND = str(_REPO_ROOT / 'backend')
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

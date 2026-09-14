#!/usr/bin/env python3
"""VESQOR gate: consent banner wiring + static asset shipping.

Standard library only (runs in CI without a Python environment), mirroring the
other test/test_vesqor_*.py gates.

Guards two regressions that both shipped silently:

1. GA4 must be consent-gated. src/app.html must set Consent Mode v2 defaults
   to 'denied' BEFORE gtag.js loads, and the banner must let the visitor
   accept/decline. Without this, analytics fire on first visit for EU traffic.

2. Static assets referenced by src/app.html must live where the Docker image
   actually picks them up. The Dockerfile does:

       COPY build /app/build
       COPY static/static /app/build/static

   and at import config.py copies FRONTEND_BUILD_DIR/static/** into STATIC_DIR
   (backend/open_webui/static), but it WIPES STATIC_DIR's top-level files
   first. So a stylesheet placed only under backend/open_webui/static/ is
   served from the build layer... never. vq25.css hit exactly this: the page
   referenced /static/vq25.css and got 404 in production.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_HTML = ROOT / 'src' / 'app.html'
SHIPPED_STATIC = ROOT / 'static' / 'static'

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


# --- 1. Consent Mode v2 -------------------------------------------------
html = APP_HTML.read_text(encoding='utf-8')

check("gtag('consent', 'default', {" in html,
      "src/app.html: missing gtag('consent','default',{...}) — GA4 would fire without consent")
for flag in ('ad_storage: \'denied\'', 'analytics_storage: \'denied\''):
    check(flag in html, f"src/app.html: consent default missing {flag}")
check('consent-banner' in html, 'src/app.html: consent banner markup missing')
check("localStorage.setItem(KEY, 'accepted')" in html,
      'src/app.html: accept handler does not persist consent')
check("localStorage.setItem(KEY, 'declined')" in html,
      'src/app.html: decline handler does not persist consent')
check("pushConsent('granted')" in html, 'src/app.html: analytics never granted on accept')

# Consent default must be declared before the loader script is injected.
consent_idx = html.find("gtag('consent', 'default'")
gtag_load_idx = html.find("s.src = 'https://www.googletagmanager.com/gtag/js?id='")
check(consent_idx != -1 and gtag_load_idx != -1 and consent_idx < gtag_load_idx,
      'src/app.html: consent default must be set BEFORE gtag.js is loaded')

# --- 2. Static assets must ship ------------------------------------------
# Every /static/<file> referenced by app.html must exist under the directory
# the Dockerfile copies into the image (static/static).
referenced = set(re.findall(r'/static/([A-Za-z0-9._-]+\.[A-Za-z0-9]+)', html))
for name in sorted(referenced):
    shipped = (SHIPPED_STATIC / name).exists()
    backend = (ROOT / 'backend' / 'open_webui' / 'static' / name).exists()
    if not shipped:
        failures.append(
            f'src/app.html references /static/{name} but static/static/{name} is missing '
            f'(backend copy present: {backend}) — the Docker image will 404 it')

# --- 3. Dockerfile still copies static/static -----------------------------
dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
# re.search (not re.match): the COPY line is not at position 0, and re.match
# only anchors at the start of the whole string even with re.M.
check(re.search(r'^COPY\s+static/static\s+/app/build/static\s*$', dockerfile, re.M) is not None,
      'Dockerfile: COPY static/static /app/build/static missing — shipped assets lost')

# --- report ---------------------------------------------------------------
if failures:
    print(f'FAILED: {len(failures)} consent/static gate violation(s):')
    for f in failures:
        print(f'  - {f}')
    sys.exit(1)

print(f'OK: consent gate wired, {len(referenced)} referenced static assets ship')

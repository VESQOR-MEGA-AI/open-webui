#!/bin/bash
# Deploy VESQOR MEGA AI frontend from GitHub Actions artifact
# Usage: ./deploy-frontend.sh
set -euo pipefail

REPO="sergeyveys/open-webui"
BRANCH="vesqor"
ARTIFACT_DIR="/var/www/vesqor-webui"
TMP="/tmp/vesqor-deploy"
TOKEN=$(cd /home/claudeproxy/projects/aifittingroom_ext_branch && git remote get-url origin | grep -oP 'ghp_[A-Za-z0-9]+')

mkdir -p "$TMP"

# 1. Get latest successful run
RUN_ID=$(curl -s -m 15 "https://api.github.com/repos/$REPO/actions/runs?branch=$BRANCH&per_page=5" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import json,sys
runs = json.load(sys.stdin).get('workflow_runs', [])
for r in runs:
    if r.get('conclusion') == 'success':
        print(r['id']); break
")
echo "Run ID: $RUN_ID"
[ -n "$RUN_ID" ] || { echo "No successful run found"; exit 1; }

# 2. Get artifact URL
ART_URL=$(curl -s -m 15 "https://api.github.com/repos/$REPO/actions/runs/$RUN_ID/artifacts" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import json,sys
arts = json.load(sys.stdin).get('artifacts', [])
for a in arts:
    if a.get('name') == 'vesqor-webui-build':
        print(a['archive_download_url']); break
")
echo "Artifact: $ART_URL"
[ -n "$ART_URL" ] || { echo "No frontend-build artifact"; exit 1; }

# 3. Download + extract
curl -sL -m 300 -H "Authorization: Bearer $TOKEN" "$ART_URL" -o "$TMP/frontend.zip"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR"
unzip -q "$TMP/frontend.zip" -d "$ARTIFACT_DIR"
# artifact may contain top-level dir
if [ -d "$ARTIFACT_DIR/build" ]; then
  mv "$ARTIFACT_DIR/build"/* "$ARTIFACT_DIR/" && rmdir "$ARTIFACT_DIR/build"
fi

# 4. Copy static (favicon/splash) if present
[ -d /home/claudeproxy/projects/open-webui/backend/open_webui/static ] && \
  cp -r /home/claudeproxy/projects/open-webui/backend/open_webui/static "$ARTIFACT_DIR/static" 2>/dev/null || true

chown -R www-data:www-data "$ARTIFACT_DIR" 2>/dev/null || chown -R root:root "$ARTIFACT_DIR"
nginx -t && systemctl reload nginx
echo "Deployed to $ARTIFACT_DIR"
du -sh "$ARTIFACT_DIR"

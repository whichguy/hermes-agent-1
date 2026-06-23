#!/usr/bin/env bash
# GitHub OAuth device-flow login — reliable headless alternative to
# `gh auth login --web` (which needs a TTY and fails with
# "tcsetattr: Inappropriate ioctl for device" in non-interactive shells).
#
# Requests BOTH scopes repo,read:org because `gh auth login --with-token`
# REJECTS a token missing read:org. Persists the token into gh's hosts.yml
# so future pushes work without re-auth (don't rely on the GH_TOKEN env var).
#
# Usage: bash gh-device-login.sh /path/to/gh   (defaults to `gh` on PATH)
set -uo pipefail
GH="${1:-gh}"
CLIENT_ID=178c6fc778ccc68e1d6a   # official GitHub CLI OAuth app client id

# 1. Request a device + user code
resp=$(curl -fsSL -X POST https://github.com/login/device/code \
  -H "Accept: application/json" \
  -d "client_id=${CLIENT_ID}&scope=repo,read:org")
echo "$resp" > /tmp/ghdev.json
USER_CODE=$(python3 -c "import json;print(json.load(open('/tmp/ghdev.json'))['user_code'])")
DEVICE_CODE=$(python3 -c "import json;print(json.load(open('/tmp/ghdev.json'))['device_code'])")
echo "==> Open https://github.com/login/device and enter code: ${USER_CODE}"
echo "    (waiting for you to authorize...)"

# 2. Poll for the access token
TOKEN=""
for _ in $(seq 1 60); do
  sleep 5
  r=$(curl -fsSL -X POST https://github.com/login/oauth/access_token \
    -H "Accept: application/json" \
    -d "client_id=${CLIENT_ID}&device_code=${DEVICE_CODE}&grant_type=urn:ietf:params:oauth:grant-type:device_code")
  TOKEN=$(python3 -c "import json,sys;print(json.loads(sys.argv[1]).get('access_token',''))" "$r")
  [ -n "$TOKEN" ] && break
done
[ -z "$TOKEN" ] && { echo "Timed out waiting for authorization."; exit 1; }

# 3. Persist into gh's config (NOT just env) + wire git credential helper
echo "$TOKEN" | "$GH" auth login --hostname github.com --git-protocol https --with-token
"$GH" auth setup-git
"$GH" auth status
rm -f /tmp/ghdev.json
echo "==> Authenticated and persisted to hosts.yml."

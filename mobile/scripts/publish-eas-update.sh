#!/usr/bin/env bash
# Publish a JS-only Estate-EZ Capture update to the TestFlight / production channel.
# Auth is EXPO_TOKEN (Expo PAT for vtz1900). Never print the token.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_BASE="${EXPO_PUBLIC_API_BASE:-https://caringtransitions-ai-production.up.railway.app}"
CHANNEL="${EAS_UPDATE_CHANNEL:-production}"
MESSAGE="${1:-JS update from Cloud Agent}"
EXPECTED_ACCOUNT="${EAS_EXPECTED_ACCOUNT:-vtz1900}"

if [[ -z "${EXPO_TOKEN:-}" ]]; then
  echo "EXPO_TOKEN is not set. Cloud Agents need the Cursor secret named EXPO_TOKEN." >&2
  echo "Create a PAT at https://expo.dev/settings/access-tokens as ${EXPECTED_ACCOUNT}." >&2
  exit 2
fi

case "$API_BASE" in
  *localhost*|*127.0.0.1*|*10.0.2.2*)
    echo "Refusing to publish with local API base: $API_BASE" >&2
    exit 3
    ;;
esac

export PATH="${HOME}/.local/bin:${PATH}"
export EXPO_PUBLIC_API_BASE="$API_BASE"

if ! command -v eas >/dev/null 2>&1; then
  echo "Installing eas-cli into ${HOME}/.local (global npm is not writable on Cloud Agents)..."
  mkdir -p "${HOME}/.local"
  npm install -g eas-cli --prefix "${HOME}/.local"
fi

echo "eas $(eas --version 2>/dev/null || true)"
echo "channel=${CHANNEL} api=${API_BASE}"

whoami_out="$(eas whoami --non-interactive 2>&1)" || {
  echo "$whoami_out" >&2
  echo "Expo rejected EXPO_TOKEN. If you just replaced the Cursor secret, start a new Cloud Agent so it boots with the new value." >&2
  exit 4
}
echo "expo account: ${whoami_out}"
if [[ "$whoami_out" != *"$EXPECTED_ACCOUNT"* ]]; then
  echo "Expected Expo account ${EXPECTED_ACCOUNT}, got: ${whoami_out}" >&2
  exit 5
fi

exec eas update --channel "$CHANNEL" --non-interactive --message "$MESSAGE"

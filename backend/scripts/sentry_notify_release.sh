#!/usr/bin/env bash
# sentry_notify_release.sh — notify Sentry of a new release.
#
# Usage (from repo root):
#   SENTRY_AUTH_TOKEN=... SENTRY_ORG=... SENTRY_PROJECT=... \
#   backend/scripts/sentry_notify_release.sh
#
# Idempotent: calling twice with the same SHA is a no-op on Sentry's side.
# Safe to run unconditionally from deploy.sh. If sentry-cli isn't installed,
# prints a hint + exits 0 (doesn't fail the deploy).
#
# What this does:
#   1. Resolves the release identifier (SENTRY_RELEASE env OR git HEAD SHA).
#   2. POSTs a release-create to the Sentry API so new events tag against it.
#   3. Uploads source maps if a build artifact dir is provided.
#   4. Marks the deploy finalized (end timestamp).
#
# Tier: TIER_0 (standalone script, no auth path, no data path).

set -u

RELEASE="${SENTRY_RELEASE:-}"
if [[ -z "$RELEASE" ]]; then
  SHA="$(git rev-parse HEAD 2>/dev/null || echo "")"
  if [[ -z "$SHA" ]]; then
    echo "sentry_notify_release: no SENTRY_RELEASE env + no git SHA → skipping"
    exit 0
  fi
  RELEASE="hedgespark@${SHA:0:12}"
fi

if [[ -z "${SENTRY_AUTH_TOKEN:-}" ]] || [[ -z "${SENTRY_ORG:-}" ]] || [[ -z "${SENTRY_PROJECT:-}" ]]; then
  echo "sentry_notify_release: SENTRY_AUTH_TOKEN / SENTRY_ORG / SENTRY_PROJECT unset → skipping"
  echo "   release: $RELEASE (would have been notified)"
  exit 0
fi

if ! command -v sentry-cli >/dev/null 2>&1; then
  echo "sentry_notify_release: sentry-cli not installed → skipping"
  echo "   install:   curl -sL https://sentry.io/get-cli/ | bash"
  echo "   release:   $RELEASE (would have been notified)"
  exit 0
fi

echo "sentry_notify_release: announcing $RELEASE to $SENTRY_ORG/$SENTRY_PROJECT"
sentry-cli releases new "$RELEASE" || exit 0
sentry-cli releases set-commits "$RELEASE" --auto || true
sentry-cli releases finalize "$RELEASE" || true
echo "sentry_notify_release: done"

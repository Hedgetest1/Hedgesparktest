#!/usr/bin/env python3
"""Shopify extension api_version + SDK alignment preventer.

Born 2026-04-30 after the post-purchase-survey extension shipped 4
deploys (v7-v11) without rendering on a real Thank-You page. Root
cause: `shopify.extension.toml` declared `api_version = "2026-04"` —
a NON-EXISTENT Shopify API version (npm latest dist-tag is
"2025-07") — while `package.json` pinned
`@shopify/ui-extensions{,-react} = "2024.10.x"`. Shopify CLI
deploy did NOT validate the version against published API tags;
the runtime threw `Cannot read properties of undefined (reading
'channel')` in `_evalExtensionSource` because the SDK 2024.10
bundle did not know about API surfaces declared by the bogus
2026-04 version.

This audit blocks commits where any
`shopify/extensions/*/shopify.extension.toml` has either:
  (a) an `api_version` that doesn't match the `yyyy-MM` calendar
      pattern Shopify uses (must be in form like "2025-07")
  (b) an `api_version` whose corresponding npm dist-tag does NOT
      exist for `@shopify/ui-extensions-react`
  (c) a sibling `package.json` that pins
      `@shopify/ui-extensions{,-react}` to a version family that
      does NOT match the toml's `api_version`

Match rule: `api_version = "2025-07"` MUST pair with package.json
spec `"2025.7.x"` (or `"2025.7.0"`, `"^2025.7.0"`, etc.) — the
SDK numeric prefix `YYYY.M` must equal the toml's `YYYY-MM` after
normalisation (M is unpadded in the SDK, padded in the toml).

Bypass: this audit is offline (no network call) and uses a frozen
allowlist of valid versions. To add a newer Shopify quarterly,
edit `KNOWN_VALID_API_VERSIONS` in this file. The list is
intentionally explicit — speculative versions like "2026-04"
(when no such version has shipped) get caught.

Trade-off accepted: the allowlist drifts. When a new Shopify
quarterly ships, this audit will fail until updated. That's
better than silently accepting a non-existent version that ships
broken to merchants.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

REPO_ROOT = Path(__file__).resolve().parents[2]
EXT_GLOB = "shopify/extensions/*/shopify.extension.toml"

# Allowlist of Shopify API versions that have actually been published
# to npm as @shopify/ui-extensions-react dist-tags. Update when a new
# quarterly ships AND the SDK is published. Verify before adding:
#
#   npm view @shopify/ui-extensions-react dist-tags
#
# A version listed here MUST have a corresponding SDK 2025.7.x type
# release on npm.
KNOWN_VALID_API_VERSIONS = {
    "2024-10",
    "2025-01",
    "2025-04",
    "2025-07",
    # Add 2025-10 / 2026-01 / 2026-04 ONLY after `npm view dist-tags`
    # confirms the corresponding SDK is published.
}

API_VERSION_RE = re.compile(r'^\s*api_version\s*=\s*"([^"]+)"', re.MULTILINE)
SDK_DEP_RE = re.compile(
    r'"@shopify/ui-extensions(?:-react)?"\s*:\s*"([^"]+)"'
)


def _normalise_sdk_to_toml(sdk_spec: str) -> str | None:
    """Convert an SDK pin like `2025.7.x` or `^2025.7.3` to `2025-07`.

    Returns None if the spec doesn't match the YYYY.M[.X] pattern
    (e.g. "latest", "unstable", "*").
    """
    m = re.match(r'^[\^~=]?(\d{4})\.(\d{1,2})(?:\.[xX0-9]+)?$', sdk_spec.strip())
    if not m:
        return None
    year, month = m.group(1), m.group(2)
    return f"{year}-{int(month):02d}"


def _check_one_extension(toml_path: Path) -> list[str]:
    """Return a list of error strings for this extension. Empty = OK."""
    errors: list[str] = []
    text = safe_read_text(toml_path)
    if text is None:
        return [f"{toml_path}: file disappeared mid-scan"]
    m = API_VERSION_RE.search(text)
    if not m:
        return [f"{toml_path}: missing `api_version =` line"]
    api_version = m.group(1).strip()

    # Calendar-pattern check
    if not re.match(r'^\d{4}-(?:01|04|07|10)$', api_version):
        errors.append(
            f"{toml_path}: api_version = \"{api_version}\" is not a Shopify "
            f"calendar quarterly (yyyy-01/04/07/10)"
        )
        return errors

    # Allowlist check
    if api_version not in KNOWN_VALID_API_VERSIONS:
        errors.append(
            f"{toml_path}: api_version = \"{api_version}\" is not in the "
            f"published-SDK allowlist {sorted(KNOWN_VALID_API_VERSIONS)}. "
            f"If a new Shopify quarterly shipped, run `npm view "
            f"@shopify/ui-extensions-react dist-tags` to confirm the SDK is "
            f"published, then add the version to KNOWN_VALID_API_VERSIONS in "
            f"this audit."
        )
        return errors

    # SDK alignment check
    pkg_path = toml_path.parent / "package.json"
    if not pkg_path.exists():
        errors.append(f"{pkg_path}: missing — extension must declare an SDK pin")
        return errors
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    for sdk_name in ("@shopify/ui-extensions", "@shopify/ui-extensions-react"):
        spec = deps.get(sdk_name)
        if not spec:
            errors.append(
                f"{pkg_path}: missing dependency {sdk_name} "
                f"(extension api_version is {api_version}, requires SDK)"
            )
            continue
        normalised = _normalise_sdk_to_toml(spec)
        if normalised is None:
            errors.append(
                f"{pkg_path}: {sdk_name} = \"{spec}\" is not a calendar pin "
                f"(yyyy.m.x) — toml api_version is {api_version}, SDK should "
                f"match"
            )
            continue
        if normalised != api_version:
            errors.append(
                f"{pkg_path}: {sdk_name} = \"{spec}\" (= API {normalised}) "
                f"does NOT match toml api_version = \"{api_version}\". "
                f"Mismatch causes runtime ExtensionUsageError on deploy."
            )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="compat shim for invariant_monitor — accepted but no-op")
    ap.parse_args()

    tomls = sorted(REPO_ROOT.glob(EXT_GLOB))
    if not tomls:
        print("audit_shopify_api_version_pinned: no extensions found — skip")
        return 0

    all_errors: list[str] = []
    for toml in tomls:
        all_errors.extend(_check_one_extension(toml))

    if all_errors:
        print(
            f"audit_shopify_api_version_pinned: FAIL — "
            f"{len(all_errors)} issue(s) across {len(tomls)} extension(s)"
        )
        for err in all_errors:
            print(f"  ✗ {err}")
        return 1

    print(
        f"audit_shopify_api_version_pinned: OK — "
        f"{len(tomls)} extension(s), all api_version + SDK pins aligned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

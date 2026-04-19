"""Regression tests for `scripts/audit_commit_devils_advocate.py`.

The audit is a commit-msg hook that enforces `feedback_no_accettabile_
per_beta.md` by blocking silent deferrals in §19 devil's-advocate
sections. If a refactor silently breaks the audit, the discipline
wall vanishes without warning. These tests are the wall's self-test.

Table-driven: each case is a (name, message, should_block) triple.
should_block=True means the audit MUST exit non-zero on that message.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT = REPO_ROOT / "backend" / "scripts" / "audit_commit_devils_advocate.py"


def _run_audit(message: str, tmp_path: Path) -> int:
    msg_file = tmp_path / "commit_msg.txt"
    msg_file.write_text(message)
    out = subprocess.run(
        [sys.executable, str(AUDIT), str(msg_file)],
        capture_output=True,
        text=True,
    )
    return out.returncode


CASES: list[tuple[str, str, bool]] = [
    (
        "negative_silent_deferral_blocks",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- the key may collide; acceptable transient, will revisit later.\n"
        ),
        True,
    ),
    (
        "negative_per_beta_blocks",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- tolerable for beta, follow-up commit pending.\n"
        ),
        True,
    ),
    (
        "negative_italian_blocks",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- accettabile per beta, per ora va bene.\n"
        ),
        True,
    ),
    (
        "negative_founder_wont_notice_blocks",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- minor edge case — TODO if founder notices.\n"
        ),
        True,
    ),
    (
        "positive_cat5_tag_passes",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- LLM budget tight at 100 merchants. Cat 5 — tracked in\n"
            "  project_elite_auto_deploy_phase_2_0.md.\n"
        ),
        False,
    ),
    (
        "positive_cat4_no_traffic_passes",
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- holdout measurement unavailable. Cat 4 — no traffic yet.\n"
        ),
        False,
    ),
    (
        "positive_clean_message_passes",
        "feat(x): change\n\nAdds a thing. No deferrals.\n",
        False,
    ),
    (
        "positive_red_flag_outside_da_section_passes",
        (
            "feat(x): perf fix\n\n"
            "This is acceptable because the query uses an index.\n"
            "## Summary\n- added index\n"
        ),
        False,
    ),
    (
        "positive_axis5_header_tagged_passes",
        (
            "feat(x): change\n\n"
            "## AXIS 5 — Devil's advocate\n"
            "- tolerable for now. Cat 5 — tracked in project_future_work.md\n"
        ),
        False,
    ),
    (
        "positive_da_in_title_not_treated_as_section_header",
        # The phrase "devil's advocate" in the commit title must NOT
        # turn the entire message into a DA section. If it did, any
        # body text containing "acceptable" would block.
        (
            "feat(meta): commit-msg hook enforces devil's advocate 10/10\n\n"
            "Acceptable scope: commits only; PRs/Slack not covered.\n"
        ),
        False,
    ),
    (
        "negative_phase_tag_NOT_adjacent_still_blocks",
        # Tag is 5 lines away — outside the 3-line pairing window.
        (
            "feat(x): change\n\n"
            "## Devil's advocate\n"
            "- cache key may collide — acceptable transient.\n"
            "\n"
            "\n"
            "\n"
            "Unrelated: Phase 2.0 backlog item.\n"
        ),
        True,
    ),
]


@pytest.mark.parametrize("name,message,should_block", CASES, ids=[c[0] for c in CASES])
def test_audit_commit_devils_advocate(name, message, should_block, tmp_path):
    code = _run_audit(message, tmp_path)
    if should_block:
        assert code == 1, f"{name}: expected block (exit 1), got exit {code}"
    else:
        assert code == 0, f"{name}: expected pass (exit 0), got exit {code}"

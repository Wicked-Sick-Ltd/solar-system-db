"""Post the nightly publish summary to the fleet coordination board.

Reads the JSON summary written by `scripts/publish_artifact.py --summary-out`
(see build/Dockerfile) and posts a short subject/body notification via
coordctl, so the fleet board reflects the outcome of each nightly build
without anyone having to tail the builder container's logs.

Run by build/systemd/solar-build.service as an ExecStartPost step on the
host (llm1), after the builder container has exited:

    ExecStartPost=/bin/sh -c 'python3 ~/deploy/solar-system-db/build/mcp_notify.py || true'

Env overrides (mainly for tests):
    SOLAR_SUMMARY_PATH  path to the summary JSON (default $SOLAR_DATA_DIR/last-publish.json,
                        where SOLAR_DATA_DIR defaults to /data/solar)
    COORDCTL_PATH       path to coordctl.py (default /home/wizzo/wizzo-digital-twin/mcp/coordctl.py)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = os.path.expanduser("~/deploy/solar-data")
DEFAULT_COORDCTL_PATH = "/home/wizzo/wizzo-digital-twin/mcp/coordctl.py"


def format_board_message(summary: dict[str, Any]) -> tuple[str, str]:
    counts = summary.get("counts_by_type") or {}
    total = sum(counts.values())
    size_mb = summary.get("size_bytes", 0) // 1_000_000
    date = (summary.get("built_at") or "")[:10]
    subject = f"solar-system-db nightly: {total:,} objects, {size_mb:,} MB, {date}"

    lines = [f"Artefact: {summary.get('artefact', '(unknown)')}", f"Size: {size_mb:,} MB", f"Built at: {summary.get('built_at', '(unknown)')}", ""]
    lines.append("Counts by type:")
    for k, v in counts.items():
        lines.append(f"  {k}: {v:,}")
    coverage = summary.get("enrichment_coverage") or {}
    if coverage:
        lines.append("")
        lines.append("Enrichment coverage:")
        for k, v in coverage.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k.endswith("_fraction"):
                    lines.append(f"  {k}={round(v * 100)}%")
                else:
                    lines.append(f"  {k}={int(v):,}")
            else:
                lines.append(f"  {k}={v}")
    if summary.get("url"):
        lines.append("")
        lines.append(f"URL: {summary['url']}")
    body = "\n".join(lines)
    return subject, body


def _coordctl_step(coordctl_path: str, step: str, args: list[str], *, input_text: str | None = None) -> None:
    """Run one coordctl subcommand, logging any failure to stderr without raising.

    OSError (not just subprocess.TimeoutExpired) is caught because a missing or
    non-executable `python3` / coordctl_path raises FileNotFoundError, and this
    notifier must never fail the build — it's invoked as
    `ExecStartPost=... || true`, but main() should still return 0 and keep
    attempting the remaining steps on its own.
    """
    kwargs: dict[str, Any] = {"check": False, "timeout": 30}
    if input_text is not None:
        kwargs["input"] = input_text
        kwargs["text"] = True
    try:
        result = subprocess.run(["python3", coordctl_path, *args], **kwargs)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"mcp_notify: coordctl {step} failed: {e.__class__.__name__}", file=sys.stderr)
        return
    if result.returncode != 0:
        print(f"mcp_notify: coordctl {step} exited {result.returncode}", file=sys.stderr)


def main() -> int:
    summary_path = Path(
        os.environ.get("SOLAR_SUMMARY_PATH")
        or os.path.join(os.environ.get("SOLAR_DATA_DIR", DEFAULT_DATA_DIR), "last-publish.json")
    )
    coordctl_path = os.environ.get("COORDCTL_PATH", DEFAULT_COORDCTL_PATH)

    if not summary_path.exists():
        print(f"mcp_notify: no summary at {summary_path}, nothing to report", file=sys.stderr)
        return 0

    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"mcp_notify: unreadable summary {summary_path}: {e.__class__.__name__}", file=sys.stderr)
        return 0

    subject, body = format_board_message(summary)

    if Path(coordctl_path).exists():
        # The board rejects a send from a session it doesn't know about
        # ("unknown sender session '…' — register first"), so register this
        # one-shot process as a coordctl session before sending, and end it
        # afterwards so it doesn't linger on the board as a phantom session.
        _coordctl_step(coordctl_path, "register", ["register"])
        _coordctl_step(
            coordctl_path, "send",
            ["send", "--to", "*", "--type", "info", "--subject", subject, "--body", "-"],
            input_text=body,
        )
        _coordctl_step(coordctl_path, "end", ["end"])
    else:
        print(subject)
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())

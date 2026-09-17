"""Fleet-board notification for the nightly builder (build/mcp_notify.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build"))

import mcp_notify


def test_format_board_message():
    s = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
        "enrichment_coverage": {
            "tier1_total": 39000, "tier1_fresh_within_7d": 4680, "tier1_fresh_fraction": 0.12,
            "tier2_total": 1529429, "tier2_enriched": 0, "tier2_enriched_fraction": 0.0,
        },
        "sha256": "deadbeef",
        "url": "https://s3.wickedsick.com/solar-system-db/solar_system-20260917.sqlite.zst",
    }
    subject, body = mcp_notify.format_board_message(s)
    assert subject == "solar-system-db nightly: 1,568,429 objects, 658 MB, 2026-09-17"
    assert "tier1_fresh_fraction=12%" in body
    assert "tier1_fresh_within_7d=4,680" in body
    assert "tier2_enriched_fraction=0%" in body
    assert "solar_system-20260917.sqlite.zst" in body
    assert "asteroid" in body and "1,564,353" in body
    assert "comet" in body and "4,076" in body
    assert s["url"] in body


def test_main_prints_when_coordctl_missing(tmp_path, monkeypatch, capsys):
    summary = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
        "enrichment_coverage": {"tier1_fresh_fraction": 0.12},
    }
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text(json.dumps(summary))
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(tmp_path / "no-such-coordctl.py"))
    rc = mcp_notify.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "solar-system-db nightly: 1,568,429 objects, 658 MB, 2026-09-17" in out


def test_main_registers_send_end_in_order_with_coordctl(tmp_path, monkeypatch):
    summary = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
    }
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text(json.dumps(summary))
    coordctl_path = tmp_path / "coordctl.py"
    coordctl_path.write_text("# stub coordctl for the test\n")
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(coordctl_path))

    calls: list[list[str]] = []
    call_kwargs: list[dict] = []

    class FakeResult:
        returncode = 0

    def fake_run(argv, *args, **kwargs):
        calls.append(argv)
        call_kwargs.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(mcp_notify.subprocess, "run", fake_run)

    rc = mcp_notify.main()

    assert rc == 0
    assert len(calls) == 3
    assert [c[2] for c in calls] == ["register", "send", "end"]
    assert str(coordctl_path) in calls[0]
    assert str(coordctl_path) in calls[1]
    assert str(coordctl_path) in calls[2]
    # All three calls use the same fail-open contract: never block the build,
    # never raise past this notifier.
    for kwargs in call_kwargs:
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 30
    # Only the "send" step carries the body, and it's on stdin (input=), not argv.
    register_kwargs, send_kwargs, end_kwargs = call_kwargs
    assert "input" not in register_kwargs
    assert "input" not in end_kwargs
    assert send_kwargs["input"] == mcp_notify.format_board_message(summary)[1]
    assert send_kwargs["text"] is True


def test_main_tolerates_missing_python3_or_coordctl(tmp_path, monkeypatch, capsys):
    """A missing python3/coordctl raises FileNotFoundError (an OSError subclass);
    main() must still return 0 and keep going rather than crash the build."""
    summary = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
    }
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text(json.dumps(summary))
    coordctl_path = tmp_path / "coordctl.py"
    coordctl_path.write_text("# stub coordctl for the test\n")
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(coordctl_path))

    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append(argv)
        raise FileNotFoundError("python3 not found")

    monkeypatch.setattr(mcp_notify.subprocess, "run", fake_run)

    rc = mcp_notify.main()

    assert rc == 0
    # register, send, and end were each still attempted despite the previous
    # step raising — no early exit on OSError.
    assert len(calls) == 3
    err = capsys.readouterr().err
    assert "coordctl register failed" in err
    assert "coordctl send failed" in err
    assert "coordctl end failed" in err


def test_main_returns_ok_on_unreadable_summary(tmp_path, monkeypatch, capsys):
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text("{not valid json,,,")
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(tmp_path / "no-such-coordctl.py"))
    rc = mcp_notify.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""

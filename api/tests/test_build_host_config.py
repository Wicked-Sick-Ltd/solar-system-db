"""The build-host files must reference secrets only through op:// and never carry ACL flags."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_env_op_has_only_references():
    text = (ROOT / "build" / ".env.op").read_text()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        assert k in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET", "S3_ENDPOINT", "S3_PUBLIC_BASE", "S3_REGION", "S3_NO_ACL", "CRAWLER_RPS"}, k
        if k.startswith("AWS_"):
            assert v.startswith("op://Shared-Secrets/CLOUDFLARE_SOL_R2_API_TOKEN/"), v

def test_compose_builder_passes_r2_env():
    text = (ROOT / "docker-compose.yml").read_text()
    for var in ("S3_REGION", "S3_NO_ACL"):
        assert re.search(rf"^\s*-\s*{var}\b", text, re.MULTILINE), var

def test_timer_is_0300_utc_and_persistent():
    t = (ROOT / "build" / "systemd" / "solar-build.timer").read_text()
    assert "OnCalendar=*-*-* 03:00:00 UTC" in t and "Persistent=true" in t


R2_ENDPOINT = "https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com"
R2_PUBLIC_BASE = "https://download.publicuniverse.net"
R2_PUBLIC_BASE_ALIAS = "https://download.sol.wickedsick.com"


def test_compose_builder_defaults_to_r2_not_ceph_rgw():
    text = (ROOT / "docker-compose.yml").read_text()
    # Anchored whole-line matches on the compose defaults (not URL substring checks).
    assert re.search(rf"^\s*-\s*S3_ENDPOINT=\$\{{S3_ENDPOINT:-{re.escape(R2_ENDPOINT)}\}}$", text, re.MULTILINE)
    assert re.search(rf"^\s*-\s*S3_PUBLIC_BASE=\$\{{S3_PUBLIC_BASE:-{re.escape(R2_PUBLIC_BASE)}\}}$", text, re.MULTILINE)
    assert re.search(r"^\s*-\s*S3_NO_ACL=\$\{S3_NO_ACL:-1\}$", text, re.MULTILINE)
    assert not re.search(r"s3\.wickedsick\.com", text), "Ceph RGW host must not appear anywhere in compose"
    assert "$$CRAWLER_RPS" in text


def test_publish_defaults_use_publicuniverse_download_host():
    op = (ROOT / "build" / ".env.op").read_text()
    example = (ROOT / ".env.example").read_text()
    pull = (ROOT / "deploy" / "php01" / "solar-pull.env.example").read_text()
    assert f"S3_PUBLIC_BASE={R2_PUBLIC_BASE}" in op
    assert f"MANIFEST_URL={R2_PUBLIC_BASE}/latest.json" in example
    assert f"S3_PUBLIC_BASE={R2_PUBLIC_BASE}" in example
    assert f"MANIFEST_URL={R2_PUBLIC_BASE}/latest.json" in pull
    # The previous custom domain is documented as still serving the same keys.
    assert "download.sol.wickedsick.com" in example


def test_nginx_mcp_advertises_publicuniverse_and_keeps_alias():
    text = (ROOT / "deploy" / "php01" / "nginx-mcp.conf").read_text()
    assert re.search(
        r"^server_name\s+api\.publicuniverse\.net\s+api\.sol\.wickedsick\.com\s*;",
        text,
        re.MULTILINE,
    )


def test_published_manifest_snapshot_keeps_original_download_host():
    text = (ROOT / "docs/performance/full-catalogue-manifest-20261002.json").read_text()
    assert f"{R2_PUBLIC_BASE_ALIAS}/solar_system-20261002.sqlite.zst" in text
    assert "download.publicuniverse.net" not in text

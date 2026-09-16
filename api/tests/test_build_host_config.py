"""The build-host files must reference secrets only through op:// and never carry ACL flags."""
from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[2]

def test_env_op_has_only_references():
    text = (ROOT / "build" / ".env.op").read_text()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        assert k in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET", "S3_ENDPOINT", "S3_PUBLIC_BASE", "S3_REGION", "S3_NO_ACL", "CRAWLER_RPS"}, k
        if k.startswith("AWS_"):
            assert v.startswith("op://Shared-Secrets/solar-system-db-r2-publisher/"), v

def test_compose_builder_passes_r2_env():
    text = (ROOT / "docker-compose.yml").read_text()
    for var in ("S3_REGION", "S3_NO_ACL"):
        assert re.search(rf"^\s*-\s*{var}\b", text, re.M), var

def test_timer_is_0300_utc_and_persistent():
    t = (ROOT / "build" / "systemd" / "solar-build.timer").read_text()
    assert "OnCalendar=*-*-* 03:00:00 UTC" in t and "Persistent=true" in t


def test_compose_builder_defaults_to_r2_not_ceph_rgw():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "s3.wickedsick.com" not in text
    assert "https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com" in text
    assert "https://download.sol.wickedsick.com" in text
    assert "$$CRAWLER_RPS" in text

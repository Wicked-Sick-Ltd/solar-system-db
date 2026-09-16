# Full Catalogue Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the already-merged 1.56M-object pipeline running nightly on llm1, publishing to Cloudflare R2, pulled by the live API host php01, with the crawler on, and the committed database retired from git.

**Architecture:** No new pipeline code. The publisher learns to skip ACLs (R2), the builder image gets an `op`-injected env file and a systemd user timer on llm1, `pull_latest.sh` is configured (not rewritten) for php01's systemd unit, and PR #11 is re-landed once the live host is on the published artefact. Every host-side step is verified by reading state back, never by exit code alone.

**Tech Stack:** Python 3.12, boto3 (extra `[publish]`), Docker Compose v5, systemd user units, 1Password `op run`, Cloudflare R2 (S3 API, region `auto`), zstd, cron.

**Spec:** `docs/superpowers/specs/2026-09-16-full-catalogue-rollout-and-extensions-design.md` §2 (and `2026-09-15-full-catalogue-design.md` §5–§6, §9).

## Global Constraints
- No secrets in git or in chat. R2 keys live only in 1Password item `solar-system-db-r2-publisher` and reach processes via `op run`.
- R2 rejects object ACLs: never send `ACL`/`x-amz-acl` to the R2 endpoint.
- Never work in a deploy checkout: llm1 builder uses `/opt/solar-system-db` (a clean clone), php01 uses `/home/wizzo/solar-system-db` (pull only, never edit).
- Preserve before disruptive change: on php01 copy `data/solar_system.sqlite` to `data/solar_system.sqlite.pre-v2-YYYYMMDD` before the first pull.
- `git push` and merges follow the fleet protocol: feature branch, PR, Craig says "merge".
- Tests never touch the network (`--offline`).

---

### Task 1: Craig-side prerequisites (R2 bucket, token, DNS)

**Files:** none in repo. Output is a 1Password item and a Cloudflare bucket.

**Interfaces:**
- Produces: bucket `solar-system-db` in account `4ce32b0dd5d81195ffdef6d24d1a8297`; custom domain `download.sol.wickedsick.com`; op item `Shared-Secrets/solar-system-db-r2-publisher` with fields `access_key_id`, `secret_access_key`.

- [ ] **Step 1: Create the bucket** (Cloudflare dashboard → R2 → Create bucket → name `solar-system-db`, location hint Western Europe). Or from this repo with the Cloudflare MCP tool `r2_bucket_create` if the connected token allows it.
- [ ] **Step 2: Public access via custom domain.** Bucket → Settings → Custom Domains → Connect `download.sol.wickedsick.com`. This requires the `wickedsick.com` zone to be in the same account; if the dashboard refuses, use the bucket's `r2.dev` public URL instead and set `S3_PUBLIC_BASE` to it in Task 3.
- [ ] **Step 3: Create an R2 API token** (R2 → Manage R2 API Tokens → Create): permission **Object Read & Write**, scope **Apply to specific buckets: solar-system-db**, TTL none. Copy the Access Key ID and Secret Access Key.
- [ ] **Step 4: Store in 1Password**: vault Shared-Secrets, item `solar-system-db-r2-publisher`, fields `access_key_id`, `secret_access_key`, notes "R2, bucket solar-system-db, created YYYY-MM-DD, Object Read & Write".
- [ ] **Step 5: Verify from llm1** (read-only):
```bash
op read "op://Shared-Secrets/solar-system-db-r2-publisher/access_key_id" | wc -c
curl -sI https://download.sol.wickedsick.com/ | head -1
```
Expected: a non-zero character count; an HTTP response (404 is fine, the bucket is empty) rather than a Cloudflare challenge page.

### Task 2: Publisher works against R2 (no ACLs, `auto` region)

**Files:**
- Modify: `scripts/publish_artifact.py:128-160` (class `S3Dest`), `:192-210` (`main`)
- Test: `api/tests/test_publish_pull.py`

**Interfaces:**
- Produces: `S3Dest(bucket, endpoint, public_base, *, acl: str | None = "public-read", region: str | None = None)`; CLI flags `--no-acl` and `--region`; env `S3_NO_ACL=1`, `S3_REGION`.

- [ ] **Step 1: Write the failing test** (append to `api/tests/test_publish_pull.py`):
```python
def test_s3dest_no_acl_omits_acl_from_extra_args(monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_artifact as pa

    calls = {}
    class FakeS3:
        def upload_file(self, path, bucket, key, ExtraArgs=None):
            calls["upload"] = ExtraArgs
        def put_object(self, **kw):
            calls["put"] = kw
        def list_objects_v2(self, **kw):
            return {"Contents": []}
        def delete_object(self, **kw):
            calls["delete"] = kw
    class FakeBoto:
        @staticmethod
        def client(name, **kw):
            calls["client_kw"] = kw
            return FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto)

    dest = pa.S3Dest("b", "https://acct.r2.cloudflarestorage.com", "https://download.example", acl=None, region="auto")
    dest.put(ROOT / "README.md", "x.zst", "application/zstd")
    dest.put_text("{}", "latest.json")
    assert "ACL" not in calls["upload"] and "ACL" not in calls["put"]
    assert calls["client_kw"]["region_name"] == "auto"
    # ACL/region change must not drop prune(); publish() always calls list_keys then delete.
    assert dest.list_keys() == []
    dest.delete("x.zst")
    assert calls["delete"]["Key"] == "x.zst"
```
(`ROOT` is already defined at the top of that test module.)
- [ ] **Step 2: Run it**: `uv run pytest api/tests/test_publish_pull.py -k no_acl -q` — expected FAIL: `TypeError: __init__() got an unexpected keyword argument 'acl'`.
- [ ] **Step 3: Implement** in `S3Dest`:
```python
class S3Dest:
    def __init__(self, bucket: str, endpoint: str, public_base: str, *,
                 acl: str | None = "public-read", region: str | None = None) -> None:
        import boto3  # optional extra [publish]
        self.bucket, self.public_base, self.acl = bucket, public_base.rstrip("/"), acl
        kw = {"endpoint_url": endpoint}
        if region:
            kw["region_name"] = region
        self.s3 = boto3.client("s3", **kw)

    def _extra(self, **base):
        if self.acl:
            base["ACL"] = self.acl
        return base

    def put(self, path: Path, key: str, content_type: str) -> str:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs=self._extra(ContentType=content_type))
        return f"{self.public_base}/{key}"

    def put_text(self, text: str, key: str) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="application/json",
                           CacheControl="max-age=300", **self._extra())
        return f"{self.public_base}/{key}"

    def list_keys(self) -> list[str]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": "solar_system-"}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            out += [o["Key"] for o in r.get("Contents", [])]
            token = r.get("NextContinuationToken")
            if not token:
                return out

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)
```
Keep `list_keys` and `delete` (shown above, unchanged from the live class). `publish()` always calls `prune()` after upload; dropping them raises `AttributeError` on the first R2 run.
and in `main()`:
```python
    p.add_argument("--no-acl", action="store_true", default=os.environ.get("S3_NO_ACL") == "1",
                   help="do not send object ACLs (required for Cloudflare R2)")
    p.add_argument("--region", default=os.environ.get("S3_REGION"), help="S3 region name ('auto' for R2)")
    ...
    elif args.bucket and args.endpoint and args.public_base:
        dest = S3Dest(args.bucket, args.endpoint, args.public_base,
                      acl=None if args.no_acl else "public-read", region=args.region)
```
- [ ] **Step 4: Run the whole file**: `uv run pytest api/tests/test_publish_pull.py -q` — expected all PASS.
- [ ] **Step 5: Commit**: `git commit -am "feat(publish): --no-acl and --region so the publisher works against Cloudflare R2"`.

### Task 3: Build-host configuration for llm1 (compose env, op env file, timer units)

**Files:**
- Create: `build/.env.op` (op references only), `build/systemd/solar-build.service`, `build/systemd/solar-build.timer`, `build/systemd/solar-crawler.service`
- Modify: `docker-compose.yml` (builder/crawler services), `docs/BUILD-HOST.md`
- Test: `api/tests/test_build_host_config.py` (new)

**Interfaces:**
- Produces: `docker compose --profile build run --rm builder` publishes to R2 when run under `op run --env-file build/.env.op`; user units that a human installs with the commands in `docs/BUILD-HOST.md`.

- [ ] **Step 1: Write the failing test**:
```python
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

def test_service_injects_op_service_account_token_not_a_file_var():
    # `op` honours OP_SERVICE_ACCOUNT_TOKEN (the token value), not *_FILE.
    for name in ("solar-build.service", "solar-crawler.service"):
        t = (ROOT / "build" / "systemd" / name).read_text()
        assert "OP_SERVICE_ACCOUNT_TOKEN_FILE" not in t
        assert "OP_SERVICE_ACCOUNT_TOKEN" in t
```
- [ ] **Step 2: Run**: `uv run pytest api/tests/test_build_host_config.py -q` — expected FAIL (files missing).
- [ ] **Step 3: Create `build/.env.op`**:
```
# op run --env-file build/.env.op -- <command>   (references only; nothing secret is in this file)
AWS_ACCESS_KEY_ID=op://Shared-Secrets/solar-system-db-r2-publisher/access_key_id
AWS_SECRET_ACCESS_KEY=op://Shared-Secrets/solar-system-db-r2-publisher/secret_access_key
S3_BUCKET=solar-system-db
S3_ENDPOINT=https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com
S3_PUBLIC_BASE=https://download.sol.wickedsick.com
S3_REGION=auto
S3_NO_ACL=1
CRAWLER_RPS=1.0
```
- [ ] **Step 4: Compose**: in `docker-compose.yml` builder `environment`, add `- S3_REGION` and `- S3_NO_ACL` (pass-through, no defaults) and change `AWS_DEFAULT_REGION=default` to `- AWS_DEFAULT_REGION=${S3_REGION:-auto}`. Change both `./data:/data` mounts in `builder` and `crawler` to `${SOLAR_DATA_DIR:-./data}:/data` so llm1 can point at `/data/solar`.
- [ ] **Step 5: Units** (`build/systemd/`):
`solar-build.service`
```
[Unit]
Description=solar-system-db nightly full build and publish
After=docker.service
[Service]
Type=oneshot
WorkingDirectory=/opt/solar-system-db
Environment=SOLAR_DATA_DIR=/data/solar
# `op` only honours OP_SERVICE_ACCOUNT_TOKEN (the token itself), not a *_FILE variant.
ExecStart=/bin/sh -c 'export OP_SERVICE_ACCOUNT_TOKEN="$(cat %h/.config/op/service-account-token)" && exec /usr/bin/op run --env-file=/opt/solar-system-db/build/.env.op -- /usr/bin/docker compose --profile build run --rm builder'
# Host path: container `/data` is `${SOLAR_DATA_DIR}` (`/data/solar`). Do not use `/data/solar/…` inside the container.
ExecStartPost=/bin/sh -c 'python3 /opt/solar-system-db/build/mcp_notify.py /data/solar/last-publish.json || true'
```
`solar-build.timer`
```
[Unit]
Description=Nightly solar-system-db build
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
```
`solar-crawler.service`
```
[Unit]
Description=solar-system-db enrichment crawler (Forth Road Bridge)
After=docker.service
[Service]
WorkingDirectory=/opt/solar-system-db
Environment=SOLAR_DATA_DIR=/data/solar
ExecStart=/bin/sh -c 'export OP_SERVICE_ACCOUNT_TOKEN="$(cat %h/.config/op/service-account-token)" && exec /usr/bin/op run --env-file=/opt/solar-system-db/build/.env.op -- /usr/bin/docker compose --profile build up crawler'
ExecStop=/usr/bin/docker compose --profile build stop crawler
Restart=always
RestartSec=30
[Install]
WantedBy=default.target
```
(`ExecStartPost` posts to the fleet board once Task 6 writes `build/mcp_notify.py` and the builder CMD writes `/data/last-publish.json`; until then `|| true` keeps a missing script from failing the oneshot. Do not hide stderr with `2>/dev/null` — a missing summary should show in the journal.)
- [ ] **Step 6: Docs**: replace the "Box" and "Nightly build" sections of `docs/BUILD-HOST.md` with the llm1 procedure:
```bash
sudo mkdir -p /opt/solar-system-db /data/solar && sudo chown wizzo:wizzo /opt/solar-system-db /data/solar
git clone https://github.com/Wicked-Sick-Ltd/solar-system-db /opt/solar-system-db
mkdir -p ~/.config/systemd/user && cp /opt/solar-system-db/build/systemd/*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now solar-build.timer solar-crawler.service
loginctl enable-linger wizzo     # timers survive logout
systemctl --user list-timers solar-build.timer
```
and note the R2 facts from spec §2 (bucket, endpoint, custom domain, no ACLs, token scope).
- [ ] **Step 7: Run tests**: `uv run pytest -q` — expected all PASS (105 + 4).
- [ ] **Step 8: Commit**: `git commit -am "feat(build-host): llm1 systemd user units, op env file, R2 compose env"`.

### Task 4: php01 pull configuration

**Files:**
- Create: `deploy/php01/solar-pull.env.example`, `deploy/php01/README.md`
- Modify: `scripts/pull_latest.sh:16` (default `RESTART_CMD` unchanged; add `--dry-run`)
- Test: `api/tests/test_publish_pull.py` (extend)

**Interfaces:**
- Produces: `pull_latest.sh --dry-run` prints the manifest decision and exits 0 without downloading; env file for php01's cron.

- [ ] **Step 1: Failing test** (append to `api/tests/test_publish_pull.py`; the file already builds a local publish into a temp dir — reuse that fixture's manifest URL as `file://`):
```python
def test_pull_dry_run_downloads_nothing(tmp_path, published_local):
    # published_local: (dest_dir, manifest_path) fixture already used by the pull tests in this module
    dest_dir, manifest = published_local
    data = tmp_path / "data"; data.mkdir()
    env = {**os.environ, "MANIFEST_URL": manifest.as_uri(), "DATA_DIR": str(data), "RESTART_CMD": "true"}
    out = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh"), "--dry-run"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "would download" in out.stdout
    assert not (data / "solar_system.sqlite").exists()
```
If the module's fixture has a different name, use that name; do not create a second publish.
- [ ] **Step 2: Run**: expected FAIL `unknown arg --dry-run`.
- [ ] **Step 3: Implement** in `pull_latest.sh`: add `DRY=0` and `--dry-run) DRY=1; shift ;;` to the arg loop; after the "already at" check insert
```bash
if [[ "$DRY" -eq 1 ]]; then log "would download $art_url ($want_sha)"; exit 0; fi
```
- [ ] **Step 4: php01 files**. `deploy/php01/solar-pull.env.example`:
```
MANIFEST_URL=https://download.sol.wickedsick.com/latest.json
DATA_DIR=/home/wizzo/solar-system-db/data
RESTART_CMD=sudo systemctl stop solar-api && sudo systemctl start solar-api
```
`deploy/php01/README.md`: the one-time steps (run as `wizzo` on php01):
```bash
cd ~/solar-system-db && git pull --ff-only origin main && .venv/bin/pip install -q -e .[api]
cp data/solar_system.sqlite data/solar_system.sqlite.pre-v2-$(date +%Y%m%d)
cp deploy/php01/solar-pull.env.example ~/.config/solar-pull.env   # edit if the hostname differs
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --dry-run
( crontab -l 2>/dev/null; echo '*/15 * * * * cd /home/wizzo/solar-system-db && set -a && . /home/wizzo/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1' ) | crontab -
```
and the note: the sudoers grant permits `systemctl stop solar-api` and `start solar-api` but its `restart` line is malformed, hence stop+start.
- [ ] **Step 5: Run tests**, expected PASS. **Commit**: `git commit -am "feat(pull): --dry-run; php01 deploy notes and env example"`.

### Task 5: First real build on llm1, publish, and php01 pull (operations, verified)

**Files:** none in repo (host state). Record the outcome in `docs/BUILD-HOST.md` "First run" section.

- [ ] **Step 1: Install on llm1** per Task 3 Step 6 (clone to `/opt/solar-system-db` at the merged commit; `mkdir -p /data/solar`).
- [ ] **Step 2: Prove the image and volumes offline first**: `SOLAR_DATA_DIR=/data/solar docker compose --profile build run --rm builder python scripts/build_full.py --fresh --offline --no-vacuum` and check exit 0 and a `/build/solar_system.sqlite` in the `build_scratch` volume. Then a dry publish to a local directory inside the container: `... run --rm builder python scripts/publish_artifact.py --db /build/solar_system.sqlite --dest /build/dry --public-base https://download.sol.wickedsick.com` and check `/build/dry/latest.json` exists. No credentials are needed for either.
- [ ] **Step 3: One-off online build**: `systemctl --user start solar-build.service` then `journalctl --user -u solar-build -f`. Expected within 45 min: stage lines, `verify.py` OK, publisher JSON with `"artefact": "solar_system-YYYYMMDD.sqlite.zst"`, `"size_bytes"` around 650–700 MB.
- [ ] **Step 4: Verify R2 from a third place** (php01 or your laptop, not llm1):
```bash
curl -fsSL https://download.sol.wickedsick.com/latest.json | python3 -m json.tool | head -20
```
Expected: `counts_by_type.asteroid` ≥ 1,500,000 and `schema_version` 2.
- [ ] **Step 5: php01 one-time steps** from Task 4's README, then `./scripts/pull_latest.sh` (not dry). Expected log: `verified sha256`, `integrity ok, 15xxxxx objects`, `installed`, `done`; `systemctl is-active solar-api` → `active`.
- [ ] **Step 6: Live checks**:
```bash
curl -s https://api.sol.wickedsick.com/api/v1/stats | python3 -m json.tool | head
curl -s "https://api.sol.wickedsick.com/api/v1/objects/2024%20YR4" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['orbital_elements']['orbit_class_code'], len(d.get('close_approaches',[])))"
curl -s https://api.sol.wickedsick.com/api/v1/download | python3 -m json.tool | head -5
```
Expected: object count > 1.5M; `APO` and a non-zero close-approach count; the manifest served.
- [ ] **Step 7: Crawler**: `systemctl --user enable --now solar-crawler.service`; after 10 minutes `sqlite3 /data/solar/enrichment.sqlite "select status,count(*) from lookups group by 1"` shows `ok` rows growing at roughly 1 per second.
- [ ] **Step 8: Record** the first-run numbers (duration, bytes, counts, php01 pull time) in `docs/BUILD-HOST.md` and commit: `git commit -am "docs(build-host): first live build and pull on 2026-09-XX"`.

### Task 6: Fleet-board notification from the builder

**Files:**
- Create: `build/mcp_notify.py`
- Modify: `scripts/publish_artifact.py` (`--summary-out`), `build/Dockerfile` (builder `CMD`), `build/systemd/solar-build.service` (`ExecStartPost` path)
- Test: `api/tests/test_mcp_notify.py`

**Interfaces:**
- Consumes: the publisher's JSON summary. Add `--summary-out` to `publish_artifact.py` `main()` (`Path(args.summary_out).write_text(json.dumps(result))` when set). The builder **container** writes `--summary-out /data/last-publish.json`; compose mounts `${SOLAR_DATA_DIR:-./data}:/data`, and on llm1 `SOLAR_DATA_DIR=/data/solar`, so that file is `/data/solar/last-publish.json` on the **host**. Do not pass `/data/solar/last-publish.json` into the container (that would land at `/data/solar/solar/last-publish.json` on llm1). `ExecStartPost` runs on the host and reads the host path.
- Produces: `format_board_message(summary: dict) -> tuple[str, str]` (subject, body) and a `main()` that posts via `coordctl mesh-send --type info --subject ... --body ...` when `~/.config/wizzo-coordination/.env` exists, otherwise prints.

- [ ] **Step 1: Failing test**:
```python
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build"))
import importlib
notify = importlib.import_module("mcp-notify") if False else __import__("mcp_notify")  # module named mcp_notify.py

def test_format_board_message():
    # Live shape from enrich_crawler.coverage() / build_manifest: *_within_7d is a count, *_fraction is 0–1.
    s = {"artefact": "solar_system-20260917.sqlite.zst", "size_bytes": 690000000, "built_at": "2026-09-17T03:31:00Z",
         "counts_by_type": {"asteroid": 1564353, "comet": 4076},
         "enrichment_coverage": {"tier1_fresh_within_7d": 187722, "tier1_fresh_fraction": 0.12}}
    subject, body = notify.format_board_message(s)
    assert subject == "solar-system-db nightly: 1,568,429 objects, 658 MB, 2026-09-17"
    assert "tier1_fresh_fraction=12%" in body and "solar_system-20260917.sqlite.zst" in body
    assert "18772200%" not in body  # do not treat the integer count as a fraction

def test_builder_writes_summary_where_the_host_oneshot_reads_it():
    df = (ROOT / "build" / "Dockerfile").read_text()
    assert "--summary-out /data/last-publish.json" in df
    unit = (ROOT / "build" / "systemd" / "solar-build.service").read_text()
    assert "/data/solar/last-publish.json" in unit
    assert "/data/solar/solar" not in unit
```
(Name the file `build/mcp_notify.py`; Task 3's unit already uses that name and the host path.)
- [ ] **Step 2: Run** → FAIL (module missing / CMD lacks `--summary-out`). **Step 3: Implement** `format_board_message` (sum counts with thousands separators, MB = bytes // 1_000_000, date = built_at[:10]; format coverage keys ending in `_fraction` as percentages (`0.12` → `12%`); print integer coverage fields such as `tier1_fresh_within_7d` as counts) and `main(argv)` reading `Path(argv[1] if argv[1:] else "/data/solar/last-publish.json")`, calling `subprocess.run(["python3", "/home/wizzo/wizzo-digital-twin/mcp/coordctl.py", "mesh-send", "--type", "info", "--subject", subject, "--body", body], check=False)` if that path exists, else `print(subject); print(body)`. Append `--summary-out /data/last-publish.json` to the `publish_artifact.py` invocation in `build/Dockerfile`'s `CMD` (container path `/data`, not `/data/solar`). Keep `ExecStartPost` pointing at the **host** file `/data/solar/last-publish.json` (`|| true` so a notify miss never fails the oneshot; do not redirect stderr to `/dev/null`).
- [ ] **Step 4: Tests PASS. Commit**: `git commit -am "feat(build): post the nightly publish summary to the fleet board"`.

### Task 7: Re-land PR #11 — retire the committed DB, rewrite the README

**Files:**
- Modify: `.gitignore`, `README.md`, `scripts/verify.py`, `solar_db/data_access.py` (the three files #11 touched; recover with `git show 7a647c6 -- .gitignore README.md scripts/verify.py solar_db/data_access.py`)
- Delete: `data/solar_system.sqlite` (`git rm --cached` then add to `.gitignore`)
- Test: `api/tests/test_schema.py` (no change), the full suite, **and** `python scripts/verify.py` with no catalogue file present (this is CI's independent "Verify DB" step in `.github/workflows/test.yml`; pytest's `conftest.py` fixture is not enough)

- [ ] **Step 1: Precondition**: Task 5 Step 6 passed within the last 24 h (`curl -s https://api.sol.wickedsick.com/api/v1/stats` shows > 1.5M). Do not start otherwise.
- [ ] **Step 2: Cherry-pick the content of #11**: `git checkout 7a647c6 -- .gitignore README.md scripts/verify.py solar_db/data_access.py && git rm --cached data/solar_system.sqlite`. Keep both of these from that commit — do not "simplify" them away:
  - `scripts/verify.py` missing-file fallback (846e046): when `DB_PATH` does not exist, build the offline fixture catalogue (`build_full.py --fresh --offline --no-vacuum` into a temp `SSDB_BUILD_PATH`) and re-invoke verify against it. After `git rm`, a fresh clone has no sqlite; `.github/workflows/test.yml`'s Verify DB step runs `python scripts/verify.py` on the default path and will exit 2 without this fallback. Do not change the workflow to skip that step.
  - `SolarDB`'s missing-file error must keep pointing at `scripts/pull_latest.sh`, `scripts/build_full.py`, or `SOLAR_DB_PATH` — not `populate_initial.py`.
- [ ] **Step 3: README edits on top**: in "What's in the box" describe `data/` as "populated by `scripts/pull_latest.sh` from the published artefact; not in git"; replace "Out of scope for v1" with "Non-goals" copied from spec 2026-09-15 §2 minus artificial satellites and meteor showers, and add "Coming: meteor showers (IAU MDC) and artificial satellites (CelesTrak) — see `docs/superpowers/specs/2026-09-16-…`"; update the counts sentence to read from `/api/v1/stats` ("about 1.57 million objects at the last build; live figure at /api/v1/stats"); update "Hosting" minimum box to 2 CPU, 4 GB RAM, 10 GB disk; add a "Download the whole database" section with the `download.sol.wickedsick.com/latest.json` URL and the `zstd -d` + `sqlite3` one-liner; update the sources table to add IAU MPC as a first-class source with attribution text.
- [ ] **Step 4: Run** `uv run pytest -q` (the suite builds its own offline DB) **and** `python scripts/verify.py` with `data/solar_system.sqlite` absent — expected pytest PASS and verify building the fixture then `VERIFY OK`. Pytest alone is not sufficient: CI's Verify DB step is independent of `conftest.py`.
- [ ] **Step 5: Retire the Actions nightly**: `.github/workflows/nightly-refresh.yml` needs a `workflow`-scoped token, so **Craig deletes the file** in the PR via the GitHub UI, or disables the workflow in Actions settings. Note this in the PR body.
- [ ] **Step 6: Commit**: `git commit -am "chore: retire the committed database and the Actions nightly; README for the full catalogue"`. Open the PR; Craig merges; then on php01 `git pull --ff-only` (the pulled DB is untracked and untouched).

## Self-review
- Spec §2 rows → Tasks: llm1 host (3, 5), R2 + no ACLs (1, 2, 3), credentials (1, 3), php01 RESTART_CMD and code pull (4, 5), re-land #11 (7), crawler (5), monitoring (6). Every row has a task.
- Placeholders: none; `mcp-notify.py` naming inconsistency fixed by standardising on `build/mcp_notify.py` (Task 3's unit must use that name).
- Types: `S3Dest(..., acl, region)` used identically in Tasks 2 and 3; `format_board_message(summary) -> (subject, body)` used in Task 6 only.
- Ordering risk: Task 7 has an explicit precondition on Task 5.

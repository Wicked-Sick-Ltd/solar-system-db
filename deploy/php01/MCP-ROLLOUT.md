# Proposed public MCP rollout on php01

These are reviewed deployment inputs, **not a record of a live rollout**.
On 2026-10-02 the public REST health/OpenAPI routes responded successfully but
POST requests to `/mcp` and `/mcp/` returned 404. The repository's php01 templates
only describe REST. The host's current nginx/unit configuration has not been
inspected; the 404 alone does not establish the live root cause. Inspect it
before applying this proposal. Production deployment and any new sudo grants
require separate authorization.

## Domain/WAF decision — 2026-10-03

Craig identified Cloudflare WAF rules as the public MCP issue and deferred the
fix to the move to the new domain. This supersedes the earlier suspicion that
missing origin routing caused the 404. Do not activate these service/proxy
templates as a remedy for that response alone.

At cutover, verify the intended hostname and MCP traffic through Cloudflare,
then repeat initialization, tool discovery and a read-only call. Inspect the
existing origin only if a problem remains; use the proposed templates below
only if that inspection establishes an origin gap. Preserve any existing MCP
service. Live client acceptance follows the domain/WAF correction; no immediate
WAF, service or proxy changes are requested by this plan update.

## Review before any conditional origin rollout

- Confirm the active API TLS virtual host, existing location blocks, upstream
  ports, systemd units, service user and exact release commit. Preserve Forge
  includes, REST routing, TLS, logging and existing request limits.
- Confirm `solar-api` and the proposed `solar-mcp` use the same live catalogue:
  `/home/wizzo/solar-data/solar_system.sqlite`. Both open it read-only. Do not
  point MCP at the obsolete checkout `data/` directory or run a fixture build
  on the host.
- Record backup copies of the active nginx configuration, units and pull env
  before changes. Use the existing coordinated release process to deploy a
  reviewed commit. Do not blindly pull a moving `main` during installation.
- Install both interfaces into the deployed virtual environment:
  `.venv/bin/pip install -e '.[api]' -e ./mcp-server`. MCP now requires SDK
  `>=1.27.1,<2` for the explicit transport-security configuration. The minimum
  and current installed SDK are exercised by the HTTP regression test.

## Proposed service and reverse proxy

Install `solar-mcp.service` as `/etc/systemd/system/solar-mcp.service`, then
validate with `systemd-analyze verify /etc/systemd/system/solar-mcp.service`
using the real deployed paths. The service runs Streamable HTTP on
`127.0.0.1:8002`; it does not expose that port publicly. After authorized
installation, daemon-reload, enable and start `solar-mcp`.

Include `nginx-mcp.conf` **inside the existing API TLS server block**, resolving
any conflicting `/mcp` locations first. It preserves the request URI, Host,
Origin and MCP session/protocol headers, and disables response/request buffering
for streaming. Validate with `nginx -t` before an authorized nginx reload. The
canonical client endpoint is `https://api.sol.wickedsick.com/mcp` without a
trailing slash; the prefix location also reaches the MCP application rather
than falling through to REST. Do not strip `/mcp` in `proxy_pass`.

`MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` are comma-separated allowlists.
The service permits only the existing public hostname/HTTPS origin in addition
to loopback. DNS-rebinding checks remain on. Server-to-server clients normally
omit Origin; the SDK accepts that. Add any genuinely required browser origin
only after review; do not replace the allowlist with `*` or strip Origin at
nginx. Changing the public domain requires coordinated proxy, service and
client changes. This proposal does not move the endpoint to publicuniverse.net.

The Docker alternative supplies the same allowlists from `PUBLIC_HOSTNAME`
and passes that value to Caddy. This is a separate deployment option, not a
reason to install Docker on php01.

## Catalogue refresh and code updates

Keep the existing single pull cron entry. After both services are installed
and start/stop authorization has been verified for both, update only
`RESTART_CMD` in the existing pull env to:

```bash
RESTART_CMD='sudo systemctl stop solar-api && sudo systemctl start solar-api && sudo systemctl stop solar-mcp && sudo systemctl start solar-mcp'
```

Do **not** copy this into a REST-only installation: its documented historical
sudo grant covers `solar-api`, not `solar-mcp`. Do not install a broad new sudoers
grant. Review the narrow required operations with the operator separately.
REST restarts first so a missing MCP permission does not prevent REST from
being brought back up. `pull_latest.sh` only updates `latest.json` after the
entire command succeeds; a failure is logged and retried on the next cron run.
Read-only database connections are short-lived, but recycle both processes to
clear process-level caches. Code deployments also need both processes recycled.
Check both are active and catalogue identities agree after a refresh.

## Acceptance and rollback

1. On loopback, perform MCP `initialize`, `notifications/initialized`,
   `tools/list`, and a single read-only `get_stats` call. Confirm session IDs
   work across requests and the catalogue identity/count matches REST.
2. Repeat once through public HTTPS with an actual MCP client. A GET 405/406
   alone is not a failed service check, and REST health alone does not test MCP.
   Use the canonical `/mcp` URL; no OAuth or API key is required.
3. Confirm a non-allowlisted Host or Origin is rejected. Validate clients with
   and without an Origin header. Confirm REST remains healthy.
4. Check `journalctl -u solar-mcp`, the existing pull log and nginx logs without
   publishing private client request data. Expect client sessions to reconnect
   following a service restart.

If acceptance fails, restore the previous nginx block and validate before
reloading it; restore the previous REST-only `RESTART_CMD`, then stop/disable
only the newly introduced MCP service. Preserve the current live catalogue and
working REST service. If runtime code must be rolled back, use the recorded
previous release and its dependencies through the release process.

## Local evidence and references

`mcp-server/tests/test_mcp_http.py` launches the real HTTP CLI against an isolated
offline catalogue and exercises initialization, session headers, discovery,
`get_stats`, session deletion and rejection of untrusted hosts/origins. It does
not claim production activation or acceptance in every vendor UI.

- [MCP Python SDK transport security](https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.1/src/mcp/server/transport_security.py)
- [nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

Local validation on 2026-10-02: the five HTTP scenarios passed with MCP SDK
1.27.1 and 1.30.0. `systemd-analyze verify` and `nginx -t` passed in an isolated
Debian container, using a stub at the unit's executable path and a minimal
nginx server containing the committed snippet. These validate template syntax;
they do not validate php01's installed dependencies, permissions or configuration.
`docker compose config` also resolved the configured public hostname into both
Caddy and MCP's allowlists.

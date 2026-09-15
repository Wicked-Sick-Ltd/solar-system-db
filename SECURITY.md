# Security policy

## Supported versions

Only the `main` branch is supported. It is what runs the public instance at
<https://api.sol.wickedsick.com>; there are no tagged releases.

## Reporting a vulnerability

Please report security issues **privately** by email to
**hello@wickedsick.com** (this will move to `hello@sol.wickedsick.com` once
that mailbox is live; both will keep working). Do not open a public issue.

Include what you found, how to reproduce it, and what you think the impact is.
You will get an acknowledgement within **5 working days**, and we'll keep you
updated as we fix it. We're happy to credit you in the fix commit if you'd
like.

There is no bug bounty programme.

## A few requests

- Please don't run load, fuzzing or scanning tools against the public API or
  MCP endpoint. Everything here runs locally in a couple of minutes (see
  [CONTRIBUTING.md](CONTRIBUTING.md)) — test against that instead.
- Both interfaces are read-only by design and the SQLite file is opened
  `mode=ro&immutable=1`, so the things we care about most are: anything that
  reaches arbitrary SQL through the REST or MCP layers, path/SSRF issues in the
  refresh scripts, poisoning of the nightly-refresh PR, and rate-limit bypass.

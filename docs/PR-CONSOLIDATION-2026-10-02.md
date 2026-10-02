# Backend PR consolidation - 2 October 2026

## Recommended merge route

All 15 open PR heads (#31 through #45) are ancestors of #45 at `89e90de`.
Current main (`12af96b`) is also an ancestor. There are no missing topic commits
or conflicts with main. The replacement branch preserves that history and adds
the small review cleanup below. Merge the replacement once; do not independently
squash every checkpoint. Keep the old PRs as review history until the replacement
lands, then close any remaining ones as superseded.

| PR | Audited head | Included |
| --- | --- | --- |
| #31 | `9de6001` | Data integrity |
| #32 | `1a3fd38` | Sky/time correctness |
| #33 | `e853260` | Observing engine |
| #34 | `524e4bf` | Starter catalogues |
| #35 | `d837bf7` | Optional JPL provider |
| #36 | `97ad0f4` | Starter coordinate frames |
| #37 | `00ccb78` | Selected hours and horizon constraints |
| #38 | `7faf41c` | Indexed query performance |
| #39 | `c5af956` | Catalogue observing planner |
| #40 | `3f1e797` | Catalogue identity |
| #41 | `31879a8` | Observing repeatability |
| #42 | `17583c9` | Exoplanet response snapshots |
| #43 | `a4ef9bb` | Target discovery |
| #44 | `2dfc478` | Interval constraint coverage |
| #45 | `89e90de` | Full-catalogue benchmark |

## Review adjudication

- #31: use one module import for the integrity test's exception and patched
  solar-longitude function. No runtime change or dynamic module lookup needed.
- #37: the integer horizon-input correction is already in `00ccb78`; retain
  actual MCP-dispatch tests for integer acceptance and boolean rejection.
- #38: retain the indexed query. SQL fragments are fixed allowlisted constants;
  all caller values are bound separately. Existing v1/v2 tests exercise
  injection-like inputs and verify index seeks without temporary sorting.
  The unmerged Cursor proposal `49a5c7c` restores OR-guarded predicates and CASE
  ordering and deletes the query-plan regression test. It would undo the measured
  improvement in [QUERY-PERFORMANCE.md](QUERY-PERFORMANCE.md), without fixing an
  input interpolation vulnerability. No applicable repository instruction requires
  that regression. The proposal is deliberately excluded.

These are code dispositions; existing GitHub threads have not been marked
resolved or replied to by this audit.

## Coordinated release

This backend supplies the contracts required by
[frontend #98](https://github.com/Wicked-Sick-Ltd/solar-system-web/pull/98).
Merge the backend replacement first, deploy and verify that revision through
the site's existing backend release process, then merge the frontend replacement.
Merging backend source alone does not establish which revision the API serves.

The default observing provider remains `builtin`. JPL is optional: Docker and
the checked-in systemd example do not provision its dependency, pinned kernel,
or environment variables. If JPL is selected for production, follow
[OBSERVING.md](OBSERVING.md#optional-checksum-pinned-jpl-provider) and verify the
reported provider/kernel identity before frontend acceptance. Missing JPL
configuration returns an explicit unavailable response, with no silent fallback.

Before frontend main (which deploys automatically), verify Forge's saved script
uses its exact-commit deployment contract and account backup hook, and verify
persistent account storage, APP_KEY continuity and recoverable backups. A passing
repository test does not establish the saved Forge configuration. No production
configuration, deployment or main merge is performed by this audit.

## Validation

Validation results and the paired frontend contract check are recorded in the
replacement PR. CI provisions the checksum-pinned DE440s kernel and runs the
complete Linux API/MCP suite. The existing full-catalogue benchmark is retained;
this consolidation does not rerun a large catalogue build or load-test live APIs.

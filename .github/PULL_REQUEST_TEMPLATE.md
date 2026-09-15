## Summary

<!-- What changes and why. Link the issue if there is one. -->

## Data source

<!-- For any value change: the primary source URL, and whether the value comes
     from the curated seed (scripts/seed_major.py / seed_moons.py) or an SBDB
     stage. Delete this section if no catalogue values change. -->

## Test plan

- [ ] `python scripts/verify.py`
- [ ] `pytest mcp-server/tests/ api/tests/ --import-mode=importlib`
- [ ] Regenerated `data/solar_system.sqlite`, or noted above that a rebuild is needed

<!-- Anything you couldn't verify locally, say so here. -->

# Catalogue filter integrity

The Public Universe frontend exposes existing asteroid and meteor filters.
Deploy the matching API corrections before enabling these filters against an
older backend. No schema migration or production catalogue rebuild is required
for the query corrections.

- Schemas without `designations` (pre-v2) cannot apply `orbit_class`,
  `max_moid_au` or `max_condition_code`. The data layer raises
  `UnsupportedCatalogueFilter`, REST returns 503, and the MCP tool receives an
  error instead of an unfiltered result. Original supported queries still work.
- Meteor date matching uses the shortest circular distance from approximate
  solar longitude to the reported peak, within 15 degrees. SQL preserves
  fractional degrees; SQLite `%` would truncate them. A 1e-10-degree tolerance
  handles binary floating-point rounding at inclusive boundaries. This does
  not turn a seasonal selection into an observing or meteor-rate forecast.

Regression coverage: `api/tests/test_catalogue_filter_integrity.py` uses
isolated temporary catalogues, REST requests and direct MCP tool function calls.

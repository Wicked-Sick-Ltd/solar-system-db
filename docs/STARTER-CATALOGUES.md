# Bounded observing starter catalogues

This change adds three browsable families, separate from solar-system objects.
It does not add stellar propagation, companion positions or observing predictions.

| Family | Selection | Records |
| --- | --- | ---: |
| Bright stars | HEASARC BSC5P `vmag <= 2.0`; exclude the 14 documented nonstellar HR IDs | 50 |
| Double stars | Those bright records with a component identifier and a positive recorded separation | 23 |
| Deep sky | OpenNGC rows with a Messier cross-reference; exclude `Dup` and `**` types | 108 |

Double-star membership overlaps bright stars. The 23 entries are catalogue records,
not 23 unique physical binaries: for example, two HR components may describe the
same pair. Optical association is not proof of gravitational binding. No fuzzy
cross-source matching or synthetic pair identities are performed. Namespaced IDs
(`bsc5p:hr2491`, `openngc:NGC0224`) preserve upstream identity. Searchable aliases
are source Bayer/Flamsteed strings and Messier numbers; common names are retained
in `source_data`, without guessing aliases.

## Sources, units and limitations

[BSC5P](https://heasarc.gsfc.nasa.gov/W3Browse/star-catalog/bsc5p.html) derives from
Hoffleit and Warren's 1991 fifth revised preliminary catalogue. The downloaded TDAT
header reports its last modification as 2022-02-03. The reviewed sample and full
upstream decompressed-byte SHA256 are recorded in `solar_db/data/starter/bsc5p.json`.
The original HEASARC fields remain strings in `source_data`. RA/Dec are J2000
catalogue coordinates in degrees. Exact CDS VizieR matches verify FK5, equinox
J2000.0 and reference epoch 2000.0; individual observation epochs remain unknown.
Proper motion is retained in original fields and normalized as explicit cosine-
adjusted RA/Dec components, but is **not applied**. Magnitude codes and uncertainty flags accompany the numerical value;
photometry is not a promise of present brightness, especially for variable stars.

The [original catalogue field definitions](https://cdsarc.cds.unistra.fr/viz-bin/ReadMe/V/50?format=html&tex=true)
specify component separation in arcseconds. The BSC5P pair selection requires a
nonempty `m_id` and positive `m_sep`. Zero and unidentified-component values remain
in `source_data` but do not qualify for the double-star view. Separation date and
position angle are unavailable and return null. Never use these historical
separations to plot a present companion position or promise instrument resolution.

[OpenNGC](https://github.com/mattiaverga/OpenNGC/tree/75ca7ff090e1d0081a5b08be70eb3bc45ccd9e06)
is pinned to that exact commit. Both `database_files/NGC.csv` and `addendum.csv`
contribute; their hashes are retained. The [author's guide](https://github.com/mattiaverga/OpenNGC/blob/75ca7ff090e1d0081a5b08be70eb3bc45ccd9e06/NGC_guide.txt)
labels RA/Dec as epoch J2000. Exact matches in the author-linked GAVO publication
verify ICRS at reference epoch J2000.0 for 107 rows. Mel022 (M45) lacks a matching
row and remains unsupported for frame-dependent planning.
Sexagesimal conversion preserves source precision without adding accuracy. Axes
are arcminutes, apparent V magnitude is V-band, and missing values stay null.
Dimensions can originate in different bands/surveys; they are not guaranteed visual
extents. Per-field `Sources` codes remain in every original row: 1 NED, 2 SIMBAD,
3 HyperLEDA, 4 Corwin, 5 HEASARC mwsc, 6 smcclustrs, 7 lmcextobj, 8 plnebulae,
9 lbn, 10 messier, 11 lyngaclust, 99 OpenNGC revisions. The original object type is
retained, including NGC6994's `Other`. M040 lacks pair measurements and is excluded;
M102 is an upstream duplicate pointing to Messier101 and is excluded. This is not
a claim to contain all 110 distinct Messier objects.

The additive `astrometry` contract, pinned machine evidence, matching tolerances,
and reference-versus-observation epoch distinction are documented in
[COORDINATE-FRAME-EVIDENCE.md](COORDINATE-FRAME-EVIDENCE.md). These additions do not
propagate positions or establish current observing accuracy.

## Redistribution decisions

See the packaged [data notice](../solar_db/data/starter/NOTICE.md) and complete
CC BY-SA licence. Software remains MIT; OpenNGC-derived data does not become MIT.
API list responses include the source licence, attribution, source release URL,
retrieval time and snapshot hash. Detail responses include the matching provenance.
Consumers and future exports must retain these notices and derivative-data terms.

[ESA Hipparcos terms](https://www.cosmos.esa.int/web/hipparcos/catalogues) specify
CC BY-NC 3.0 IGO; that dataset was not incorporated. WDS is scientifically useful,
but its [description and acknowledgement guidance](https://crf.usno.navy.mil/wdstext)
do not establish unambiguous redistribution terms for all contributed material.
WDS ingestion remains deferred pending clearer permission; we do not relabel it as
unrestricted government data solely because USNO hosts it.

## Reproduction and interfaces

Both online and offline `scripts/build_full.py` install the **same pinned sample**.
There are no new network calls in build or query paths. The importer validates
snapshot hashes and all records before a transaction replaces the two starter
tables. Existing old catalogues without tables return `available: false`.

`GET /api/v1/starter-targets?family=double_star&limit=50&offset=0` and MCP
`list_starter_targets` share the read layer. `family` accepts `bright_star`,
`double_star`, `deep_sky`; omission includes all unique records. `q` is a literal
case-insensitive substring search, not SQL wildcards. Results sort by exact ID;
limit is 1–200, offset 0–1000. Exact detail IDs are used by
`GET /api/v1/starter-targets/{id}` and `get_starter_target`. An unknown detail is
REST404/MCP null. These endpoints intentionally expose sample coverage.

To independently reproduce the subset from the original downloaded bytes, place
the decompressed HEASARC `bsc5p.tdat` plus pinned OpenNGC `NGC.csv` and `addendum.csv`
in a temporary directory, then run `python scripts/verify_starter_sources.py DIR`.
The verifier checks upstream hashes and repeats selection, comparing every raw
row with the shipped snapshot. Updating a source is a reviewed data change:
retain new input hashes/retrieval metadata, review changed identities/measurements,
update snapshot hashes and counts, and rerun tests. Do not silently refresh these
versioned samples from moving upstream branches.

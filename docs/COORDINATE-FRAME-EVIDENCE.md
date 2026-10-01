# Starter coordinate frames and reference epochs

This change adds verified coordinate interpretations to the bounded starter
sample. It does **not** propagate coordinates, predict visibility, or infer a
companion's position. Existing numerical coordinates and `source_data` strings
are unchanged. Build and query paths make no network requests for this evidence.

## Published evidence and exact matching

The raw VOTables and complete queries are pinned in
[`solar_db/data/starter/astrometry`](../solar_db/data/starter/astrometry).
The manifest records the publisher, query URL, response hash and coverage.

| Source | Machine declaration | Exact matches | Unsupported |
| --- | --- | ---: | --- |
| CDS VizieR V/50/catalog | `eq_FK5`, equinox `J2000`, epoch `2000.000` | 50 HR identifiers | None |
| Author-linked GAVO OpenNGC | `ICRS`, epoch `J2000.0` | 107 OpenNGC identifiers | `Mel022` (M45 addendum) |

The VizieR coordinate fields reference the FK5 `COOSYS`; proper-motion fields
reference the same system and declare arcseconds/year. The
[CDS V/50 ReadMe](https://cdsarc.cds.unistra.fr/viz-bin/ReadMe/V/50?format=html&tex=true)
also specifies coordinate reference epoch 2000.0 and defines the RA proper-motion
component with the cosine of declination included. This is `pm_ra_cosdec`, not
bare RA angular rate. Both components match the pinned HEASARC strings numerically
for all 50 records. We do not infer a position frame merely from a PM label:
the VOTable assigns the frame to the coordinate fields themselves.

The selected VizieR rows match HEASARC by **exact HR identifier**. Maximum absolute
coordinate differences are 0.000050000000016 degrees in RA and
0.000044444444448 degrees in declination, consistent with the HEASARC sample's
four-decimal degree rounding. The check accepts at most 0.000050001 degrees per
component. It preserves the HEASARC values, rather than silently replacing them
with extra digits. No name matching or proximity-based identities are used.

The [OpenNGC author's README](https://github.com/mattiaverga/OpenNGC/tree/75ca7ff090e1d0081a5b08be70eb3bc45ccd9e06)
links the GAVO TAP publication. Its
[table metadata](https://dc.g-vo.org/tableinfo/openngc.data) explicitly describes
ICRS coordinates at epoch J2000.0; the downloaded VOTable independently carries
that declaration on both fields. GAVO's
[resource record](https://dc.g-vo.org/browse/openngc/q) identifies the author,
upstream repository and CC BY-SA 4.0 terms (DOI: 10.21938/y.1ejWUD_MQ6b_eDFoVbbw).
All 107 returned identifiers match the pinned OpenNGC rows with coordinate
agreement within 1e-10 degrees after sexagesimal conversion. This records the
publisher's interpretation of those exact rows; it does not assert a common
original observing instrument or measurement epoch for this heterogeneous data.

The query explicitly requested all 108 sample names. GAVO did not return `Mel022`.
That addendum record remains browsable with `astrometry.status = unsupported`;
no ICRS interpretation is extrapolated to it. OpenNGC proper-motion values remain
raw source fields: this contract does not establish their convention or apply them.

Pinned response SHA256 values:

- VizieR: `a3f83273ccaecf913a0d8e077413062c20ce1fc01ce890c80615a9572d5f1be9`
- GAVO: `bb872b3b8759508d0bf714eabcd1bfed1796be95046506dd0802819a846535dd`

## Additive API and MCP contract

List and detail records gain an `astrometry` object. Sources and detail provenance
gain `astrometry_evidence`. Existing fields and original snapshot hashes retain
their meaning. An old catalogue without these additions must be treated as lacking
verified frame metadata by a planning consumer.

| Field | Meaning |
| --- | --- |
| `status` | `verified` for a matched row; otherwise `unsupported` |
| `frame` | `FK5`, `ICRS`, or null |
| `equinox` | `J2000.0` for FK5; null for ICRS (which has no equinox parameter) |
| `reference_epoch_jyear` | Julian year 2000.0 to which published coordinates refer; null when unsupported |
| `observation_epoch_jyear` | Always null: individual observing dates are not established |
| `pm_ra_cosdec_arcsec_per_year`, `pm_dec_arcsec_per_year` | Verified BSC components; zero is valid, null is missing; null for OpenNGC |
| `motion_model` | Available interpretation: `linear_angular_proper_motion`, `static_catalogue_direction`, or null |
| `coordinates_propagated` | Always false in this catalogue |
| `evidence_source`, `matched_identifier` | Source metadata key and exact matched upstream ID; matched ID null if unsupported |
| `unsupported_reason` | Explanation for unsupported records; otherwise null |

A **reference epoch** identifies when a catalogue position is expressed. An
**observation epoch** identifies when a measurement was taken; multiple observations
may underpin one catalogue position. Knowing the former does not establish the
latter. An **equinox** defines the orientation of the relevant coordinate axes and
is a separate concept, even when its year happens to coincide.

`motion_model` is an available interpretation for a future bounded calculation,
not a claim that a calculation has occurred. BSC supports angular-only proper
motion where both components exist. No distance, parallax, radial velocity,
perspective acceleration or physical binary orbit is inferred. Static directions
apply no motion; epoch-dependent accuracy must not be implied. A planner must
explicitly handle frames, time range, available motion, and unsupported rows.

## Reproducing and reviewing the evidence

Offline semantic verification (including raw response hashes, identities, frame
references, units, coordinates and proper motions):

```bash
python -m pytest api/tests/test_starter_astrometry.py api/tests/test_starter_catalogues.py -q
python -c 'from solar_db.starter_catalogues import load_starter_catalogues; from collections import Counter; rows, _ = load_starter_catalogues(); print(Counter(r["astrometry"]["frame"] for r in rows))'
```

Expected coverage is FK5 50, ICRS 107, unsupported 1. Ingestion validates every
binding before replacing database rows; changed or incomplete evidence fails.
The new XML/manifest files are package data and must be present in built wheels.

For a deliberate source review, the following optional command repeats the saved
read-only public queries into a **new temporary directory**, without updating the
pinned files or any catalogue:

```bash
python - <<'PY'
import hashlib, json, pathlib, tempfile, urllib.request
manifest = json.loads(pathlib.Path('solar_db/data/starter/astrometry/manifest.json').read_text())
out = pathlib.Path(tempfile.mkdtemp(prefix='starter-frame-review-'))
for source, item in manifest.items():
    with urllib.request.urlopen(item['query_url'], timeout=60) as response:
        data = response.read()
    (out / item['file']).write_bytes(data)
    print(source, hashlib.sha256(data).hexdigest(), out / item['file'])
PY
```

Server timestamps or formatting can change response hashes without scientific
changes. Re-run exact identifier/value comparisons and inspect the `COOSYS` and
field references before accepting a replacement; do not simply re-pin its hash.
The original catalogue subset reproduction remains documented in
[STARTER-CATALOGUES.md](STARTER-CATALOGUES.md).

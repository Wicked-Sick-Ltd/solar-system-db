# Starter snapshot data notices

The repository's MIT licence covers software. It does not replace these data terms.

- `bsc5p.json`: selected records from Hoffleit and Warren (1991), Bright Star
  Catalog, fifth revised preliminary edition, distributed by NASA/GSFC HEASARC.
  NASA Open Data identifies its licence as US government works:
  https://data.nasa.gov/dataset/bright-star-catalog
  HEASARC permits free use: https://heasarc.gsfc.nasa.gov/docs/heasarc/data_policy.html
  Credit the authors and NASA/GSFC HEASARC when using this sample.
- `openngc.json`: OpenNGC, copyright 2023 Mattia Verga, CC BY-SA 4.0:
  https://github.com/mattiaverga/OpenNGC/tree/75ca7ff090e1d0081a5b08be70eb3bc45ccd9e06
  Public Universe changes: selected Messier rows; normalized coordinates and units
  are generated separately while original row strings are retained. Redistributed
  OpenNGC data and adaptations remain under CC BY-SA 4.0. Include attribution,
  indicate changes, link the licence and preserve the same licence. Full licence:
  `CC-BY-SA-4.0.txt`; https://creativecommons.org/licenses/by-sa/4.0/

These are bounded historical catalogue samples, not a complete catalogue or current
astrometric solution. Per-source metadata, source-file hashes and retrieval time are
inside each JSON file. `manifest.json` pins the reviewed subset bytes.

Coordinate evidence in `astrometry/vizier-v50.xml` is a selected V/50 publication
from CDS VizieR, crediting Hoffleit and Warren and CDS.
`astrometry/gavo-openngc.xml` is selected OpenNGC data and metadata distributed by
the author-linked GAVO Data Center, crediting Mattia Verga and GAVO under the same
CC BY-SA 4.0 data terms. The response bytes are preserved; separate normalized
metadata binds exact identifiers and coordinates to the published frames. Query
URLs and hashes are in `astrometry/manifest.json`; no catalogue coordinates have
been propagated. See `docs/COORDINATE-FRAME-EVIDENCE.md` for interpretation limits.

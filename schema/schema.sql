-- solar-system-db schema
-- SQLite. Designed for model-building: orbital + physical + visual properties,
-- normalised across object types (planet / moon / dwarf_planet / asteroid /
-- comet / tno / centaur), with rings as a sibling table.
--
-- Notes:
--   * `objects.id` is a stable string PK (e.g. "planet-earth", "moon-io",
--     "ast-2-pallas") so the rows survive re-imports.
--   * `parent_id` is null for sun-orbiters and set to the planet for moons.
--   * `sources` keeps provenance per (object_id, field).
--   * Everything is normalised so model code can pull just the slice it needs.

PRAGMA foreign_keys = ON;
-- Note: we intentionally don't set journal_mode=WAL here — the DB is published
-- as a single committed file and WAL files are a deployment headache. Default
-- rollback journal is fine; performance is more than adequate for read-mostly
-- workloads at this scale.

----------------------------------------------------------------------
-- Core
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS objects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    designation     TEXT,                -- e.g. "(2) Pallas", "1P/Halley"
    object_type     TEXT NOT NULL CHECK (object_type IN (
                        'star','planet','moon','dwarf_planet','dwarf_planet_candidate',
                        'asteroid','comet','tno','centaur','trojan','hilda','neo','pha'
                    )),
    parent_id       TEXT REFERENCES objects(id) ON DELETE SET NULL,
    discoverer      TEXT,
    discovery_date  TEXT,                -- ISO 8601 (date or year)
    wikipedia_url   TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_objects_type      ON objects(object_type);
CREATE INDEX IF NOT EXISTS idx_objects_parent    ON objects(parent_id);
CREATE INDEX IF NOT EXISTS idx_objects_name      ON objects(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_objects_design    ON objects(designation);

----------------------------------------------------------------------
-- Classifications (multi-label: an object can be NEO + PHA + Apollo, etc.)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS classifications (
    object_id   TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,           -- 'NEO','PHA','Trojan','Hilda','MBA','Centaur','KBO','SDO','Inner','Outer'
    PRIMARY KEY (object_id, label)
);
CREATE INDEX IF NOT EXISTS idx_class_label ON classifications(label);

----------------------------------------------------------------------
-- Orbital elements (heliocentric for sun-orbiters; planet-centric where noted)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orbital_elements (
    object_id                       TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    epoch                           TEXT,        -- JD or ISO 8601
    epoch_jd                        REAL,        -- Julian Date for the elements
    frame                           TEXT,        -- 'J2000' usually
    centre                          TEXT,        -- 'Sun','Earth','Jupiter',...
    semi_major_axis_au              REAL,
    eccentricity                    REAL,
    inclination_deg                 REAL,
    longitude_ascending_node_deg    REAL,
    argument_periapsis_deg          REAL,
    mean_anomaly_deg                REAL,
    orbital_period_days             REAL,
    perihelion_au                   REAL,
    aphelion_au                     REAL,
    mean_motion_deg_per_day         REAL,
    -- v2: everything else the SBDB query API publishes about the orbit
    perihelion_time_jd              REAL,        -- tp
    moid_au                         REAL,        -- Earth MOID
    moid_jupiter_au                 REAL,
    tisserand_jupiter               REAL,        -- t_jup
    condition_code                  INTEGER,     -- MPC "U" 0 (best) … 9
    data_arc_days                   REAL,
    first_obs                       TEXT,
    last_obs                        TEXT,
    n_obs_used                      INTEGER,
    n_delay_obs_used                INTEGER,     -- radar delay
    n_doppler_obs_used              INTEGER,     -- radar Doppler
    rms_arcsec                      REAL,
    solution_date                   TEXT,
    orbit_id                        TEXT,
    producer                        TEXT,
    orbit_source                    TEXT,        -- ORB / JPL / MPC …
    equinox                         TEXT,
    two_body                        TEXT,
    pe_used                         TEXT,        -- planetary ephemeris
    sb_used                         TEXT,        -- small-body perturber set
    orbit_class_code                TEXT,        -- MBA, APO, TNO, JFc …
    orbit_class_name                TEXT,
    sigma_e REAL, sigma_a REAL, sigma_q REAL, sigma_i REAL, sigma_om REAL,
    sigma_w REAL, sigma_ma REAL, sigma_tp REAL, sigma_per REAL, sigma_n REAL, sigma_ad REAL,
    updated_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_oe_a      ON orbital_elements(semi_major_axis_au);
CREATE INDEX IF NOT EXISTS idx_oe_class  ON orbital_elements(orbit_class_code);
CREATE INDEX IF NOT EXISTS idx_oe_moid   ON orbital_elements(moid_au);

----------------------------------------------------------------------
-- Physical properties
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS physical_properties (
    object_id                   TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    radius_km                   REAL,         -- mean / volumetric radius
    equatorial_radius_km        REAL,
    polar_radius_km             REAL,
    mass_kg                     REAL,
    density_g_cm3               REAL,
    rotation_period_hours       REAL,         -- sidereal
    axial_tilt_deg              REAL,
    surface_gravity_m_s2        REAL,
    escape_velocity_km_s        REAL,
    -- v2: SBDB physical parameters + the rest of the NASA fact-sheet bulk block
    gm_km3_s2                   REAL,
    slope_g                     REAL,         -- H-G phase slope
    extent_km                   TEXT,         -- tri-axial "a x b x c"
    pole_ra_dec                 TEXT,         -- "RA/Dec" of the spin pole, deg
    diameter_sigma_km           REAL,
    volume_km3                  REAL,
    ellipticity                 REAL,
    moment_of_inertia           REAL,         -- I/MR²
    j2                          REAL,         -- ×10⁻⁶
    magnetic_field              TEXT,         -- Yes / No / Unknown
    length_of_day_hours         REAL,
    synodic_period_days         REAL,
    mean_orbital_velocity_km_s  REAL,
    min_orbital_velocity_km_s   REAL,
    max_orbital_velocity_km_s   REAL,
    tropical_period_days        REAL,
    mean_temperature_k          REAL,
    black_body_temperature_k    REAL,
    solar_irradiance_w_m2       REAL,
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_pp_radius ON physical_properties(radius_km);

----------------------------------------------------------------------
-- Visual properties (albedo, magnitude, colour)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS visual_properties (
    object_id              TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    geometric_albedo       REAL,
    bond_albedo            REAL,
    absolute_magnitude_h   REAL,
    colour_b_v             REAL,
    spectral_type          TEXT,            -- SMASSII (Bus) taxonomy — NOT the orbit class (v1 bug)
    dominant_colour_hex    TEXT,            -- best-effort, for rendering models
    -- v2
    colour_u_b             REAL,
    colour_i_r             REAL,
    spectral_type_tholen   TEXT,
    magnitude_v10          REAL,            -- V(1,0), planets
    comet_m1               REAL,            -- total magnitude parameters
    comet_k1               REAL,
    comet_m2               REAL,            -- nuclear
    comet_k2               REAL,
    nongrav_a1             REAL,            -- non-gravitational accelerations
    nongrav_a2             REAL,
    nongrav_a3             REAL,
    nongrav_dt             REAL,
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_vp_h ON visual_properties(absolute_magnitude_h);

----------------------------------------------------------------------
-- Rings (planet-level)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    inner_radius_km REAL,
    outer_radius_km REAL,
    width_km        REAL,
    thickness_km    REAL,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_rings_parent ON rings(parent_id);

----------------------------------------------------------------------
-- Provenance: which upstream source each fact came from
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id       TEXT REFERENCES objects(id) ON DELETE CASCADE,
    table_name      TEXT NOT NULL,                 -- 'orbital_elements','physical_properties',...
    field_name      TEXT,                          -- nullable: whole-row provenance
    source_name     TEXT NOT NULL,                 -- 'JPL Horizons','JPL SBDB','MPC','NASA Fact Sheet'
    source_url      TEXT,
    retrieved_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_sources_object ON sources(object_id);
CREATE INDEX IF NOT EXISTS idx_sources_name   ON sources(source_name);


----------------------------------------------------------------------
-- v2: per-object detail tables
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS designations (
    object_id       TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    designation     TEXT NOT NULL,                 -- '1', 'A801 AA', '2004 MN4', '1P'
    kind            TEXT NOT NULL CHECK (kind IN ('number','provisional','alternate','name')),
    source          TEXT,
    PRIMARY KEY (object_id, designation)
);
CREATE INDEX IF NOT EXISTS idx_designations_des ON designations(designation);

CREATE TABLE IF NOT EXISTS discoveries (
    object_id       TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    discovered_on   TEXT,                          -- ISO date
    discoverer      TEXT,
    site            TEXT,
    location        TEXT,
    citation        TEXT,
    reference       TEXT,
    source          TEXT NOT NULL,                 -- 'MPC', 'JPL SBDB', 'seed'
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS close_approaches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id       TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    body            TEXT NOT NULL,                 -- 'Earth', 'Moon', 'Venus', …
    cd_jd           REAL NOT NULL,
    cd_iso          TEXT NOT NULL,
    dist_au         REAL,
    dist_min_au     REAL,
    dist_max_au     REAL,
    v_rel_km_s      REAL,
    v_inf_km_s      REAL,
    t_sigma         TEXT,                          -- formatted uncertainty, e.g. '04:08'
    orbit_ref       TEXT,
    source          TEXT NOT NULL DEFAULT 'JPL CAD',
    UNIQUE (object_id, body, cd_iso)                -- minute resolution: CAD and SBDB-lookup rows dedupe
);
CREATE INDEX IF NOT EXISTS idx_ca_object ON close_approaches(object_id, cd_jd);
CREATE INDEX IF NOT EXISTS idx_ca_date   ON close_approaches(cd_jd);
CREATE INDEX IF NOT EXISTS idx_ca_body   ON close_approaches(body, cd_jd);

CREATE TABLE IF NOT EXISTS atmospheres (
    object_id               TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    surface_pressure_bar    REAL,
    pressure_note           TEXT,
    temperature_k           REAL,
    temperature_note        TEXT,
    density_kg_m3           REAL,
    scale_height_km         REAL,
    mean_molecular_weight   REAL,
    wind_note               TEXT,
    composition_json        TEXT,                  -- [{"species":"N2","fraction":0.78,"unit":"%"},…]
    source                  TEXT NOT NULL DEFAULT 'NASA Planetary Fact Sheet'
);

CREATE TABLE IF NOT EXISTS radar_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id       TEXT NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    epoch           TEXT,
    obs_type        TEXT,
    reference       TEXT,
    source          TEXT NOT NULL DEFAULT 'JPL SBDB'
);
CREATE INDEX IF NOT EXISTS idx_radar_object ON radar_observations(object_id);

CREATE TABLE IF NOT EXISTS impact_monitoring (
    object_id       TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    flagged         INTEGER NOT NULL DEFAULT 0,    -- listed by Sentry / NEODyS
    source          TEXT NOT NULL DEFAULT 'JPL SBDB',
    retrieved_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Crawler bookkeeping (merged from enrichment.sqlite at build time)
CREATE TABLE IF NOT EXISTS enrichment_state (
    object_id       TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    tier            INTEGER NOT NULL DEFAULT 2,    -- 1 = shown on the site, 2 = everything else
    lookup_at       TEXT,
    lookup_status   TEXT                           -- 'ok', 'not_found', 'error'
);
CREATE INDEX IF NOT EXISTS idx_enrich_tier ON enrichment_state(tier, lookup_at);

-- Full-text search over names and every designation (rebuilt each build)
CREATE VIRTUAL TABLE IF NOT EXISTS objects_fts USING fts5(
    id UNINDEXED, name, designation, alt, tokenize = 'unicode61 remove_diacritics 2'
);

----------------------------------------------------------------------
-- v3: Meteor showers (IAU Meteor Data Center)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meteor_showers (
    iau_no                  INTEGER NOT NULL,
    ad_no                   INTEGER NOT NULL,
    code                    TEXT NOT NULL,
    name                    TEXT NOT NULL,
    status_code             INTEGER,
    status_label            TEXT,
    activity                TEXT,
    solar_longitude_deg     REAL,
    ra_deg                  REAL,
    dec_deg                 REAL,
    dra_deg_per_day         REAL,
    ddec_deg_per_day        REAL,
    vg_km_s                 REAL,
    a_au                    REAL,
    q_au                    REAL,
    e                       REAL,
    peri_deg                REAL,
    node_deg                REAL,
    incl_deg                REAL,
    n_members               INTEGER,
    shower_group            TEXT,
    parent_body             TEXT,
    parent_object_id        TEXT REFERENCES objects(id),
    technique               TEXT,
    reference               TEXT,
    submitted_on            TEXT,
    source                  TEXT NOT NULL DEFAULT 'IAU MDC',
    PRIMARY KEY (iau_no, ad_no)
);

CREATE INDEX IF NOT EXISTS idx_showers_code   ON meteor_showers(code);
CREATE INDEX IF NOT EXISTS idx_showers_parent ON meteor_showers(parent_object_id);
CREATE INDEX IF NOT EXISTS idx_showers_status ON meteor_showers(status_code);

PRAGMA user_version = 3;

----------------------------------------------------------------------
-- Build metadata (one row per refresh run)
----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS build_meta (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,              -- 'full','nightly'
    row_count       INTEGER,
    notes           TEXT
);

----------------------------------------------------------------------
-- Convenience views
----------------------------------------------------------------------
DROP VIEW IF EXISTS v_planets;
CREATE VIEW v_planets AS
SELECT o.id, o.name, o.designation,
       p.radius_km, p.mass_kg, p.density_g_cm3, p.rotation_period_hours,
       p.axial_tilt_deg, p.surface_gravity_m_s2, p.escape_velocity_km_s,
       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
       oe.orbital_period_days, oe.perihelion_au, oe.aphelion_au,
       vp.geometric_albedo, vp.dominant_colour_hex
FROM objects o
LEFT JOIN physical_properties p  ON p.object_id  = o.id
LEFT JOIN orbital_elements    oe ON oe.object_id = o.id
LEFT JOIN visual_properties   vp ON vp.object_id = o.id
WHERE o.object_type = 'planet'
ORDER BY oe.semi_major_axis_au;

DROP VIEW IF EXISTS v_moons_by_planet;
CREATE VIEW v_moons_by_planet AS
SELECT parent.name AS planet,
       o.id, o.name, o.designation, o.discoverer, o.discovery_date,
       p.radius_km, p.mass_kg,
       oe.semi_major_axis_au, oe.orbital_period_days, oe.eccentricity, oe.inclination_deg
FROM objects o
JOIN objects parent ON parent.id = o.parent_id
LEFT JOIN physical_properties p ON p.object_id = o.id
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
WHERE o.object_type = 'moon'
ORDER BY planet, oe.semi_major_axis_au;

DROP VIEW IF EXISTS v_dwarf_planets;
CREATE VIEW v_dwarf_planets AS
SELECT o.id, o.name, o.designation, o.object_type,
       p.radius_km, p.mass_kg,
       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
       oe.orbital_period_days
FROM objects o
LEFT JOIN physical_properties p ON p.object_id = o.id
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
WHERE o.object_type IN ('dwarf_planet','dwarf_planet_candidate')
ORDER BY oe.semi_major_axis_au;

DROP VIEW IF EXISTS v_neos;
CREATE VIEW v_neos AS
SELECT o.id, o.name, o.designation,
       oe.semi_major_axis_au, oe.perihelion_au, oe.eccentricity, oe.inclination_deg,
       vp.absolute_magnitude_h
FROM objects o
JOIN classifications c ON c.object_id = o.id AND c.label = 'NEO'
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
LEFT JOIN visual_properties vp ON vp.object_id = o.id
ORDER BY vp.absolute_magnitude_h;

DROP VIEW IF EXISTS v_phas;
CREATE VIEW v_phas AS
SELECT o.id, o.name, o.designation,
       oe.semi_major_axis_au, oe.perihelion_au, oe.eccentricity,
       vp.absolute_magnitude_h
FROM objects o
JOIN classifications c ON c.object_id = o.id AND c.label = 'PHA'
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
LEFT JOIN visual_properties vp ON vp.object_id = o.id
ORDER BY vp.absolute_magnitude_h;

DROP VIEW IF EXISTS v_comets;
CREATE VIEW v_comets AS
SELECT o.id, o.name, o.designation, o.discoverer, o.discovery_date,
       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
       oe.orbital_period_days, oe.perihelion_au, oe.aphelion_au
FROM objects o
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
WHERE o.object_type = 'comet'
ORDER BY oe.orbital_period_days;

DROP VIEW IF EXISTS v_tnos;
CREATE VIEW v_tnos AS
SELECT o.id, o.name, o.designation,
       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
       oe.orbital_period_days,
       vp.absolute_magnitude_h
FROM objects o
LEFT JOIN orbital_elements oe ON oe.object_id = o.id
LEFT JOIN visual_properties vp ON vp.object_id = o.id
WHERE o.object_type IN ('tno','centaur')
ORDER BY oe.semi_major_axis_au;

DROP VIEW IF EXISTS v_object_counts;
CREATE VIEW v_object_counts AS
SELECT object_type, COUNT(*) AS n
FROM objects
GROUP BY object_type
ORDER BY n DESC;

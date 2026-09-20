"""Shared helpers for the populate / update / verify scripts."""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "schema.sql"
# DB lives at ROOT/data/solar_system.sqlite. Builds that need to write a lot
# can opt into using SSDB_BUILD_PATH to point at a fast local path (e.g. /tmp)
# and the publish step copies the finished file into ROOT/data.
DB_PATH = Path(os.environ.get(
    "SSDB_BUILD_PATH",
    str(ROOT / "data" / "solar_system.sqlite"),
))
PUBLISH_PATH = ROOT / "data" / "solar_system.sqlite"

SBDB_QUERY_URL = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
SBDB_LOOKUP_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"

USER_AGENT = "solar-system-db/0.1 (+https://github.com/Wicked-Sick-Ltd/solar-system-db)"

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def canonicalize_confined(root: str | Path, candidate: str | Path) -> Path:
    """Resolve candidate under root, rejecting traversal and symlink escapes."""
    canonical_root = Path(root).resolve()
    canonical_candidate = (canonical_root / candidate).resolve()
    try:
        canonical_candidate.relative_to(canonical_root)
    except ValueError as exc:
        raise ValueError(f"path is outside destination root: {candidate}") from exc
    return canonical_candidate


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
# Property-table upserts use one literal statement per table so values stay in
# bound parameters. Omitted fields are NULL on insert and COALESCE'd on update.
_ORBITAL_COLUMNS = (
    "epoch", "epoch_jd", "frame", "centre", "semi_major_axis_au", "eccentricity",
    "inclination_deg", "longitude_ascending_node_deg", "argument_periapsis_deg",
    "mean_anomaly_deg", "orbital_period_days", "perihelion_au", "aphelion_au",
    "mean_motion_deg_per_day", "perihelion_time_jd", "moid_au", "moid_jupiter_au",
    "tisserand_jupiter", "condition_code", "data_arc_days", "first_obs", "last_obs",
    "n_obs_used", "n_delay_obs_used", "n_doppler_obs_used", "rms_arcsec",
    "solution_date", "orbit_id", "producer", "orbit_source", "equinox", "two_body",
    "pe_used", "sb_used", "orbit_class_code", "orbit_class_name", "sigma_e",
    "sigma_a", "sigma_q", "sigma_i", "sigma_om", "sigma_w", "sigma_ma", "sigma_tp",
    "sigma_per", "sigma_n", "sigma_ad",
)
_PHYSICAL_COLUMNS = (
    "radius_km", "equatorial_radius_km", "polar_radius_km", "mass_kg",
    "density_g_cm3", "rotation_period_hours", "axial_tilt_deg",
    "surface_gravity_m_s2", "escape_velocity_km_s", "gm_km3_s2", "slope_g",
    "extent_km", "pole_ra_dec", "diameter_sigma_km", "volume_km3", "ellipticity",
    "moment_of_inertia", "j2", "magnetic_field", "length_of_day_hours",
    "synodic_period_days", "mean_orbital_velocity_km_s", "min_orbital_velocity_km_s",
    "max_orbital_velocity_km_s", "tropical_period_days", "mean_temperature_k",
    "black_body_temperature_k", "solar_irradiance_w_m2",
)
_VISUAL_COLUMNS = (
    "geometric_albedo", "bond_albedo", "absolute_magnitude_h", "colour_b_v",
    "spectral_type", "dominant_colour_hex", "colour_u_b", "colour_i_r",
    "spectral_type_tholen", "magnitude_v10", "comet_m1", "comet_k1", "comet_m2",
    "comet_k2", "nongrav_a1", "nongrav_a2", "nongrav_a3", "nongrav_dt",
)
_PROPERTY_COLUMNS = {
    "orbital_elements": _ORBITAL_COLUMNS,
    "physical_properties": _PHYSICAL_COLUMNS,
    "visual_properties": _VISUAL_COLUMNS,
}


def _property_params(table: str, object_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        columns = _PROPERTY_COLUMNS[table]
    except KeyError as exc:
        raise ValueError(f"Unsupported property table: {table}") from exc
    params = {column: None for column in columns}
    params["object_id"] = object_id
    for key, value in fields.items():
        if key in params and value is not None:
            params[key] = value
    return params


def connect(create: bool = False) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    if create:
        with SCHEMA_PATH.open() as f:
            conn.executescript(f.read())
    return conn


def publish() -> Path:
    """Copy the working DB to its published location (a no-op if already in place).

    SSDB_NO_PUBLISH=1 leaves the build where it is (tests, scratch builds)."""
    if DB_PATH.resolve() == PUBLISH_PATH.resolve() or os.environ.get("SSDB_NO_PUBLISH") == "1":
        return DB_PATH
    PUBLISH_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DB_PATH, PUBLISH_PATH)
    return PUBLISH_PATH


def upsert_object(conn, *, id: str, name: str, object_type: str,
                  designation: str | None = None, parent_id: str | None = None,
                  discoverer: str | None = None, discovery_date: str | None = None,
                  wikipedia_url: str | None = None, notes: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO objects (id, name, designation, object_type, parent_id,
                             discoverer, discovery_date, wikipedia_url, notes)
        VALUES (:id, :name, :designation, :object_type, :parent_id,
                :discoverer, :discovery_date, :wikipedia_url, :notes)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            designation = COALESCE(excluded.designation, objects.designation),
            object_type = excluded.object_type,
            parent_id = COALESCE(excluded.parent_id, objects.parent_id),
            discoverer = COALESCE(excluded.discoverer, objects.discoverer),
            discovery_date = COALESCE(excluded.discovery_date, objects.discovery_date),
            wikipedia_url = COALESCE(excluded.wikipedia_url, objects.wikipedia_url),
            notes = COALESCE(excluded.notes, objects.notes),
            updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        dict(id=id, name=name, designation=designation, object_type=object_type,
             parent_id=parent_id, discoverer=discoverer, discovery_date=discovery_date,
             wikipedia_url=wikipedia_url, notes=notes),
    )


def upsert_row(conn, table: str, object_id: str, fields: dict[str, Any]) -> None:
    """Generic upsert keyed on object_id for the *_properties / orbital_elements tables."""
    params = _property_params(table, object_id, fields)
    if table == "orbital_elements":
        conn.execute(
            """
            INSERT INTO orbital_elements (
                object_id, epoch, epoch_jd, frame, centre, semi_major_axis_au, eccentricity,
                inclination_deg, longitude_ascending_node_deg, argument_periapsis_deg,
                mean_anomaly_deg, orbital_period_days, perihelion_au, aphelion_au,
                mean_motion_deg_per_day, perihelion_time_jd, moid_au, moid_jupiter_au,
                tisserand_jupiter, condition_code, data_arc_days, first_obs, last_obs,
                n_obs_used, n_delay_obs_used, n_doppler_obs_used, rms_arcsec, solution_date,
                orbit_id, producer, orbit_source, equinox, two_body, pe_used, sb_used,
                orbit_class_code, orbit_class_name, sigma_e, sigma_a, sigma_q, sigma_i,
                sigma_om, sigma_w, sigma_ma, sigma_tp, sigma_per, sigma_n, sigma_ad
            ) VALUES (
                :object_id, :epoch, :epoch_jd, :frame, :centre, :semi_major_axis_au, :eccentricity,
                :inclination_deg, :longitude_ascending_node_deg, :argument_periapsis_deg,
                :mean_anomaly_deg, :orbital_period_days, :perihelion_au, :aphelion_au,
                :mean_motion_deg_per_day, :perihelion_time_jd, :moid_au, :moid_jupiter_au,
                :tisserand_jupiter, :condition_code, :data_arc_days, :first_obs, :last_obs,
                :n_obs_used, :n_delay_obs_used, :n_doppler_obs_used, :rms_arcsec, :solution_date,
                :orbit_id, :producer, :orbit_source, :equinox, :two_body, :pe_used, :sb_used,
                :orbit_class_code, :orbit_class_name, :sigma_e, :sigma_a, :sigma_q, :sigma_i,
                :sigma_om, :sigma_w, :sigma_ma, :sigma_tp, :sigma_per, :sigma_n, :sigma_ad
            )
            ON CONFLICT(object_id) DO UPDATE SET
                epoch = COALESCE(excluded.epoch, orbital_elements.epoch),
                epoch_jd = COALESCE(excluded.epoch_jd, orbital_elements.epoch_jd),
                frame = COALESCE(excluded.frame, orbital_elements.frame),
                centre = COALESCE(excluded.centre, orbital_elements.centre),
                semi_major_axis_au = COALESCE(excluded.semi_major_axis_au, orbital_elements.semi_major_axis_au),
                eccentricity = COALESCE(excluded.eccentricity, orbital_elements.eccentricity),
                inclination_deg = COALESCE(excluded.inclination_deg, orbital_elements.inclination_deg),
                longitude_ascending_node_deg = COALESCE(excluded.longitude_ascending_node_deg, orbital_elements.longitude_ascending_node_deg),
                argument_periapsis_deg = COALESCE(excluded.argument_periapsis_deg, orbital_elements.argument_periapsis_deg),
                mean_anomaly_deg = COALESCE(excluded.mean_anomaly_deg, orbital_elements.mean_anomaly_deg),
                orbital_period_days = COALESCE(excluded.orbital_period_days, orbital_elements.orbital_period_days),
                perihelion_au = COALESCE(excluded.perihelion_au, orbital_elements.perihelion_au),
                aphelion_au = COALESCE(excluded.aphelion_au, orbital_elements.aphelion_au),
                mean_motion_deg_per_day = COALESCE(excluded.mean_motion_deg_per_day, orbital_elements.mean_motion_deg_per_day),
                perihelion_time_jd = COALESCE(excluded.perihelion_time_jd, orbital_elements.perihelion_time_jd),
                moid_au = COALESCE(excluded.moid_au, orbital_elements.moid_au),
                moid_jupiter_au = COALESCE(excluded.moid_jupiter_au, orbital_elements.moid_jupiter_au),
                tisserand_jupiter = COALESCE(excluded.tisserand_jupiter, orbital_elements.tisserand_jupiter),
                condition_code = COALESCE(excluded.condition_code, orbital_elements.condition_code),
                data_arc_days = COALESCE(excluded.data_arc_days, orbital_elements.data_arc_days),
                first_obs = COALESCE(excluded.first_obs, orbital_elements.first_obs),
                last_obs = COALESCE(excluded.last_obs, orbital_elements.last_obs),
                n_obs_used = COALESCE(excluded.n_obs_used, orbital_elements.n_obs_used),
                n_delay_obs_used = COALESCE(excluded.n_delay_obs_used, orbital_elements.n_delay_obs_used),
                n_doppler_obs_used = COALESCE(excluded.n_doppler_obs_used, orbital_elements.n_doppler_obs_used),
                rms_arcsec = COALESCE(excluded.rms_arcsec, orbital_elements.rms_arcsec),
                solution_date = COALESCE(excluded.solution_date, orbital_elements.solution_date),
                orbit_id = COALESCE(excluded.orbit_id, orbital_elements.orbit_id),
                producer = COALESCE(excluded.producer, orbital_elements.producer),
                orbit_source = COALESCE(excluded.orbit_source, orbital_elements.orbit_source),
                equinox = COALESCE(excluded.equinox, orbital_elements.equinox),
                two_body = COALESCE(excluded.two_body, orbital_elements.two_body),
                pe_used = COALESCE(excluded.pe_used, orbital_elements.pe_used),
                sb_used = COALESCE(excluded.sb_used, orbital_elements.sb_used),
                orbit_class_code = COALESCE(excluded.orbit_class_code, orbital_elements.orbit_class_code),
                orbit_class_name = COALESCE(excluded.orbit_class_name, orbital_elements.orbit_class_name),
                sigma_e = COALESCE(excluded.sigma_e, orbital_elements.sigma_e),
                sigma_a = COALESCE(excluded.sigma_a, orbital_elements.sigma_a),
                sigma_q = COALESCE(excluded.sigma_q, orbital_elements.sigma_q),
                sigma_i = COALESCE(excluded.sigma_i, orbital_elements.sigma_i),
                sigma_om = COALESCE(excluded.sigma_om, orbital_elements.sigma_om),
                sigma_w = COALESCE(excluded.sigma_w, orbital_elements.sigma_w),
                sigma_ma = COALESCE(excluded.sigma_ma, orbital_elements.sigma_ma),
                sigma_tp = COALESCE(excluded.sigma_tp, orbital_elements.sigma_tp),
                sigma_per = COALESCE(excluded.sigma_per, orbital_elements.sigma_per),
                sigma_n = COALESCE(excluded.sigma_n, orbital_elements.sigma_n),
                sigma_ad = COALESCE(excluded.sigma_ad, orbital_elements.sigma_ad),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            params,
        )
        return
    if table == "physical_properties":
        conn.execute(
            """
            INSERT INTO physical_properties (
                object_id, radius_km, equatorial_radius_km, polar_radius_km, mass_kg,
                density_g_cm3, rotation_period_hours, axial_tilt_deg, surface_gravity_m_s2,
                escape_velocity_km_s, gm_km3_s2, slope_g, extent_km, pole_ra_dec,
                diameter_sigma_km, volume_km3, ellipticity, moment_of_inertia, j2,
                magnetic_field, length_of_day_hours, synodic_period_days,
                mean_orbital_velocity_km_s, min_orbital_velocity_km_s,
                max_orbital_velocity_km_s, tropical_period_days, mean_temperature_k,
                black_body_temperature_k, solar_irradiance_w_m2
            ) VALUES (
                :object_id, :radius_km, :equatorial_radius_km, :polar_radius_km, :mass_kg,
                :density_g_cm3, :rotation_period_hours, :axial_tilt_deg, :surface_gravity_m_s2,
                :escape_velocity_km_s, :gm_km3_s2, :slope_g, :extent_km, :pole_ra_dec,
                :diameter_sigma_km, :volume_km3, :ellipticity, :moment_of_inertia, :j2,
                :magnetic_field, :length_of_day_hours, :synodic_period_days,
                :mean_orbital_velocity_km_s, :min_orbital_velocity_km_s,
                :max_orbital_velocity_km_s, :tropical_period_days, :mean_temperature_k,
                :black_body_temperature_k, :solar_irradiance_w_m2
            )
            ON CONFLICT(object_id) DO UPDATE SET
                radius_km = COALESCE(excluded.radius_km, physical_properties.radius_km),
                equatorial_radius_km = COALESCE(excluded.equatorial_radius_km, physical_properties.equatorial_radius_km),
                polar_radius_km = COALESCE(excluded.polar_radius_km, physical_properties.polar_radius_km),
                mass_kg = COALESCE(excluded.mass_kg, physical_properties.mass_kg),
                density_g_cm3 = COALESCE(excluded.density_g_cm3, physical_properties.density_g_cm3),
                rotation_period_hours = COALESCE(excluded.rotation_period_hours, physical_properties.rotation_period_hours),
                axial_tilt_deg = COALESCE(excluded.axial_tilt_deg, physical_properties.axial_tilt_deg),
                surface_gravity_m_s2 = COALESCE(excluded.surface_gravity_m_s2, physical_properties.surface_gravity_m_s2),
                escape_velocity_km_s = COALESCE(excluded.escape_velocity_km_s, physical_properties.escape_velocity_km_s),
                gm_km3_s2 = COALESCE(excluded.gm_km3_s2, physical_properties.gm_km3_s2),
                slope_g = COALESCE(excluded.slope_g, physical_properties.slope_g),
                extent_km = COALESCE(excluded.extent_km, physical_properties.extent_km),
                pole_ra_dec = COALESCE(excluded.pole_ra_dec, physical_properties.pole_ra_dec),
                diameter_sigma_km = COALESCE(excluded.diameter_sigma_km, physical_properties.diameter_sigma_km),
                volume_km3 = COALESCE(excluded.volume_km3, physical_properties.volume_km3),
                ellipticity = COALESCE(excluded.ellipticity, physical_properties.ellipticity),
                moment_of_inertia = COALESCE(excluded.moment_of_inertia, physical_properties.moment_of_inertia),
                j2 = COALESCE(excluded.j2, physical_properties.j2),
                magnetic_field = COALESCE(excluded.magnetic_field, physical_properties.magnetic_field),
                length_of_day_hours = COALESCE(excluded.length_of_day_hours, physical_properties.length_of_day_hours),
                synodic_period_days = COALESCE(excluded.synodic_period_days, physical_properties.synodic_period_days),
                mean_orbital_velocity_km_s = COALESCE(excluded.mean_orbital_velocity_km_s, physical_properties.mean_orbital_velocity_km_s),
                min_orbital_velocity_km_s = COALESCE(excluded.min_orbital_velocity_km_s, physical_properties.min_orbital_velocity_km_s),
                max_orbital_velocity_km_s = COALESCE(excluded.max_orbital_velocity_km_s, physical_properties.max_orbital_velocity_km_s),
                tropical_period_days = COALESCE(excluded.tropical_period_days, physical_properties.tropical_period_days),
                mean_temperature_k = COALESCE(excluded.mean_temperature_k, physical_properties.mean_temperature_k),
                black_body_temperature_k = COALESCE(excluded.black_body_temperature_k, physical_properties.black_body_temperature_k),
                solar_irradiance_w_m2 = COALESCE(excluded.solar_irradiance_w_m2, physical_properties.solar_irradiance_w_m2),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            params,
        )
        return
    conn.execute(
        """
        INSERT INTO visual_properties (
            object_id, geometric_albedo, bond_albedo, absolute_magnitude_h, colour_b_v,
            spectral_type, dominant_colour_hex, colour_u_b, colour_i_r, spectral_type_tholen,
            magnitude_v10, comet_m1, comet_k1, comet_m2, comet_k2, nongrav_a1, nongrav_a2,
            nongrav_a3, nongrav_dt
        ) VALUES (
            :object_id, :geometric_albedo, :bond_albedo, :absolute_magnitude_h, :colour_b_v,
            :spectral_type, :dominant_colour_hex, :colour_u_b, :colour_i_r, :spectral_type_tholen,
            :magnitude_v10, :comet_m1, :comet_k1, :comet_m2, :comet_k2, :nongrav_a1, :nongrav_a2,
            :nongrav_a3, :nongrav_dt
        )
        ON CONFLICT(object_id) DO UPDATE SET
            geometric_albedo = COALESCE(excluded.geometric_albedo, visual_properties.geometric_albedo),
            bond_albedo = COALESCE(excluded.bond_albedo, visual_properties.bond_albedo),
            absolute_magnitude_h = COALESCE(excluded.absolute_magnitude_h, visual_properties.absolute_magnitude_h),
            colour_b_v = COALESCE(excluded.colour_b_v, visual_properties.colour_b_v),
            spectral_type = COALESCE(excluded.spectral_type, visual_properties.spectral_type),
            dominant_colour_hex = COALESCE(excluded.dominant_colour_hex, visual_properties.dominant_colour_hex),
            colour_u_b = COALESCE(excluded.colour_u_b, visual_properties.colour_u_b),
            colour_i_r = COALESCE(excluded.colour_i_r, visual_properties.colour_i_r),
            spectral_type_tholen = COALESCE(excluded.spectral_type_tholen, visual_properties.spectral_type_tholen),
            magnitude_v10 = COALESCE(excluded.magnitude_v10, visual_properties.magnitude_v10),
            comet_m1 = COALESCE(excluded.comet_m1, visual_properties.comet_m1),
            comet_k1 = COALESCE(excluded.comet_k1, visual_properties.comet_k1),
            comet_m2 = COALESCE(excluded.comet_m2, visual_properties.comet_m2),
            comet_k2 = COALESCE(excluded.comet_k2, visual_properties.comet_k2),
            nongrav_a1 = COALESCE(excluded.nongrav_a1, visual_properties.nongrav_a1),
            nongrav_a2 = COALESCE(excluded.nongrav_a2, visual_properties.nongrav_a2),
            nongrav_a3 = COALESCE(excluded.nongrav_a3, visual_properties.nongrav_a3),
            nongrav_dt = COALESCE(excluded.nongrav_dt, visual_properties.nongrav_dt),
            updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        params,
    )


def upsert_row_fill(conn, table: str, object_id: str, fields: dict[str, Any]) -> None:
    """Like upsert_row, but existing non-null values win: bulk sources may only
    fill gaps in curated rows, never overwrite fact-sheet values."""
    params = _property_params(table, object_id, fields)
    if table == "orbital_elements":
        conn.execute(
            """
            INSERT INTO orbital_elements (
                object_id, epoch, epoch_jd, frame, centre, semi_major_axis_au, eccentricity,
                inclination_deg, longitude_ascending_node_deg, argument_periapsis_deg,
                mean_anomaly_deg, orbital_period_days, perihelion_au, aphelion_au,
                mean_motion_deg_per_day, perihelion_time_jd, moid_au, moid_jupiter_au,
                tisserand_jupiter, condition_code, data_arc_days, first_obs, last_obs,
                n_obs_used, n_delay_obs_used, n_doppler_obs_used, rms_arcsec, solution_date,
                orbit_id, producer, orbit_source, equinox, two_body, pe_used, sb_used,
                orbit_class_code, orbit_class_name, sigma_e, sigma_a, sigma_q, sigma_i,
                sigma_om, sigma_w, sigma_ma, sigma_tp, sigma_per, sigma_n, sigma_ad
            ) VALUES (
                :object_id, :epoch, :epoch_jd, :frame, :centre, :semi_major_axis_au, :eccentricity,
                :inclination_deg, :longitude_ascending_node_deg, :argument_periapsis_deg,
                :mean_anomaly_deg, :orbital_period_days, :perihelion_au, :aphelion_au,
                :mean_motion_deg_per_day, :perihelion_time_jd, :moid_au, :moid_jupiter_au,
                :tisserand_jupiter, :condition_code, :data_arc_days, :first_obs, :last_obs,
                :n_obs_used, :n_delay_obs_used, :n_doppler_obs_used, :rms_arcsec, :solution_date,
                :orbit_id, :producer, :orbit_source, :equinox, :two_body, :pe_used, :sb_used,
                :orbit_class_code, :orbit_class_name, :sigma_e, :sigma_a, :sigma_q, :sigma_i,
                :sigma_om, :sigma_w, :sigma_ma, :sigma_tp, :sigma_per, :sigma_n, :sigma_ad
            )
            ON CONFLICT(object_id) DO UPDATE SET
                epoch = COALESCE(orbital_elements.epoch, excluded.epoch),
                epoch_jd = COALESCE(orbital_elements.epoch_jd, excluded.epoch_jd),
                frame = COALESCE(orbital_elements.frame, excluded.frame),
                centre = COALESCE(orbital_elements.centre, excluded.centre),
                semi_major_axis_au = COALESCE(orbital_elements.semi_major_axis_au, excluded.semi_major_axis_au),
                eccentricity = COALESCE(orbital_elements.eccentricity, excluded.eccentricity),
                inclination_deg = COALESCE(orbital_elements.inclination_deg, excluded.inclination_deg),
                longitude_ascending_node_deg = COALESCE(orbital_elements.longitude_ascending_node_deg, excluded.longitude_ascending_node_deg),
                argument_periapsis_deg = COALESCE(orbital_elements.argument_periapsis_deg, excluded.argument_periapsis_deg),
                mean_anomaly_deg = COALESCE(orbital_elements.mean_anomaly_deg, excluded.mean_anomaly_deg),
                orbital_period_days = COALESCE(orbital_elements.orbital_period_days, excluded.orbital_period_days),
                perihelion_au = COALESCE(orbital_elements.perihelion_au, excluded.perihelion_au),
                aphelion_au = COALESCE(orbital_elements.aphelion_au, excluded.aphelion_au),
                mean_motion_deg_per_day = COALESCE(orbital_elements.mean_motion_deg_per_day, excluded.mean_motion_deg_per_day),
                perihelion_time_jd = COALESCE(orbital_elements.perihelion_time_jd, excluded.perihelion_time_jd),
                moid_au = COALESCE(orbital_elements.moid_au, excluded.moid_au),
                moid_jupiter_au = COALESCE(orbital_elements.moid_jupiter_au, excluded.moid_jupiter_au),
                tisserand_jupiter = COALESCE(orbital_elements.tisserand_jupiter, excluded.tisserand_jupiter),
                condition_code = COALESCE(orbital_elements.condition_code, excluded.condition_code),
                data_arc_days = COALESCE(orbital_elements.data_arc_days, excluded.data_arc_days),
                first_obs = COALESCE(orbital_elements.first_obs, excluded.first_obs),
                last_obs = COALESCE(orbital_elements.last_obs, excluded.last_obs),
                n_obs_used = COALESCE(orbital_elements.n_obs_used, excluded.n_obs_used),
                n_delay_obs_used = COALESCE(orbital_elements.n_delay_obs_used, excluded.n_delay_obs_used),
                n_doppler_obs_used = COALESCE(orbital_elements.n_doppler_obs_used, excluded.n_doppler_obs_used),
                rms_arcsec = COALESCE(orbital_elements.rms_arcsec, excluded.rms_arcsec),
                solution_date = COALESCE(orbital_elements.solution_date, excluded.solution_date),
                orbit_id = COALESCE(orbital_elements.orbit_id, excluded.orbit_id),
                producer = COALESCE(orbital_elements.producer, excluded.producer),
                orbit_source = COALESCE(orbital_elements.orbit_source, excluded.orbit_source),
                equinox = COALESCE(orbital_elements.equinox, excluded.equinox),
                two_body = COALESCE(orbital_elements.two_body, excluded.two_body),
                pe_used = COALESCE(orbital_elements.pe_used, excluded.pe_used),
                sb_used = COALESCE(orbital_elements.sb_used, excluded.sb_used),
                orbit_class_code = COALESCE(orbital_elements.orbit_class_code, excluded.orbit_class_code),
                orbit_class_name = COALESCE(orbital_elements.orbit_class_name, excluded.orbit_class_name),
                sigma_e = COALESCE(orbital_elements.sigma_e, excluded.sigma_e),
                sigma_a = COALESCE(orbital_elements.sigma_a, excluded.sigma_a),
                sigma_q = COALESCE(orbital_elements.sigma_q, excluded.sigma_q),
                sigma_i = COALESCE(orbital_elements.sigma_i, excluded.sigma_i),
                sigma_om = COALESCE(orbital_elements.sigma_om, excluded.sigma_om),
                sigma_w = COALESCE(orbital_elements.sigma_w, excluded.sigma_w),
                sigma_ma = COALESCE(orbital_elements.sigma_ma, excluded.sigma_ma),
                sigma_tp = COALESCE(orbital_elements.sigma_tp, excluded.sigma_tp),
                sigma_per = COALESCE(orbital_elements.sigma_per, excluded.sigma_per),
                sigma_n = COALESCE(orbital_elements.sigma_n, excluded.sigma_n),
                sigma_ad = COALESCE(orbital_elements.sigma_ad, excluded.sigma_ad),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            params,
        )
        return
    if table == "physical_properties":
        conn.execute(
            """
            INSERT INTO physical_properties (
                object_id, radius_km, equatorial_radius_km, polar_radius_km, mass_kg,
                density_g_cm3, rotation_period_hours, axial_tilt_deg, surface_gravity_m_s2,
                escape_velocity_km_s, gm_km3_s2, slope_g, extent_km, pole_ra_dec,
                diameter_sigma_km, volume_km3, ellipticity, moment_of_inertia, j2,
                magnetic_field, length_of_day_hours, synodic_period_days,
                mean_orbital_velocity_km_s, min_orbital_velocity_km_s,
                max_orbital_velocity_km_s, tropical_period_days, mean_temperature_k,
                black_body_temperature_k, solar_irradiance_w_m2
            ) VALUES (
                :object_id, :radius_km, :equatorial_radius_km, :polar_radius_km, :mass_kg,
                :density_g_cm3, :rotation_period_hours, :axial_tilt_deg, :surface_gravity_m_s2,
                :escape_velocity_km_s, :gm_km3_s2, :slope_g, :extent_km, :pole_ra_dec,
                :diameter_sigma_km, :volume_km3, :ellipticity, :moment_of_inertia, :j2,
                :magnetic_field, :length_of_day_hours, :synodic_period_days,
                :mean_orbital_velocity_km_s, :min_orbital_velocity_km_s,
                :max_orbital_velocity_km_s, :tropical_period_days, :mean_temperature_k,
                :black_body_temperature_k, :solar_irradiance_w_m2
            )
            ON CONFLICT(object_id) DO UPDATE SET
                radius_km = COALESCE(physical_properties.radius_km, excluded.radius_km),
                equatorial_radius_km = COALESCE(physical_properties.equatorial_radius_km, excluded.equatorial_radius_km),
                polar_radius_km = COALESCE(physical_properties.polar_radius_km, excluded.polar_radius_km),
                mass_kg = COALESCE(physical_properties.mass_kg, excluded.mass_kg),
                density_g_cm3 = COALESCE(physical_properties.density_g_cm3, excluded.density_g_cm3),
                rotation_period_hours = COALESCE(physical_properties.rotation_period_hours, excluded.rotation_period_hours),
                axial_tilt_deg = COALESCE(physical_properties.axial_tilt_deg, excluded.axial_tilt_deg),
                surface_gravity_m_s2 = COALESCE(physical_properties.surface_gravity_m_s2, excluded.surface_gravity_m_s2),
                escape_velocity_km_s = COALESCE(physical_properties.escape_velocity_km_s, excluded.escape_velocity_km_s),
                gm_km3_s2 = COALESCE(physical_properties.gm_km3_s2, excluded.gm_km3_s2),
                slope_g = COALESCE(physical_properties.slope_g, excluded.slope_g),
                extent_km = COALESCE(physical_properties.extent_km, excluded.extent_km),
                pole_ra_dec = COALESCE(physical_properties.pole_ra_dec, excluded.pole_ra_dec),
                diameter_sigma_km = COALESCE(physical_properties.diameter_sigma_km, excluded.diameter_sigma_km),
                volume_km3 = COALESCE(physical_properties.volume_km3, excluded.volume_km3),
                ellipticity = COALESCE(physical_properties.ellipticity, excluded.ellipticity),
                moment_of_inertia = COALESCE(physical_properties.moment_of_inertia, excluded.moment_of_inertia),
                j2 = COALESCE(physical_properties.j2, excluded.j2),
                magnetic_field = COALESCE(physical_properties.magnetic_field, excluded.magnetic_field),
                length_of_day_hours = COALESCE(physical_properties.length_of_day_hours, excluded.length_of_day_hours),
                synodic_period_days = COALESCE(physical_properties.synodic_period_days, excluded.synodic_period_days),
                mean_orbital_velocity_km_s = COALESCE(physical_properties.mean_orbital_velocity_km_s, excluded.mean_orbital_velocity_km_s),
                min_orbital_velocity_km_s = COALESCE(physical_properties.min_orbital_velocity_km_s, excluded.min_orbital_velocity_km_s),
                max_orbital_velocity_km_s = COALESCE(physical_properties.max_orbital_velocity_km_s, excluded.max_orbital_velocity_km_s),
                tropical_period_days = COALESCE(physical_properties.tropical_period_days, excluded.tropical_period_days),
                mean_temperature_k = COALESCE(physical_properties.mean_temperature_k, excluded.mean_temperature_k),
                black_body_temperature_k = COALESCE(physical_properties.black_body_temperature_k, excluded.black_body_temperature_k),
                solar_irradiance_w_m2 = COALESCE(physical_properties.solar_irradiance_w_m2, excluded.solar_irradiance_w_m2),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            params,
        )
        return
    conn.execute(
        """
        INSERT INTO visual_properties (
            object_id, geometric_albedo, bond_albedo, absolute_magnitude_h, colour_b_v,
            spectral_type, dominant_colour_hex, colour_u_b, colour_i_r, spectral_type_tholen,
            magnitude_v10, comet_m1, comet_k1, comet_m2, comet_k2, nongrav_a1, nongrav_a2,
            nongrav_a3, nongrav_dt
        ) VALUES (
            :object_id, :geometric_albedo, :bond_albedo, :absolute_magnitude_h, :colour_b_v,
            :spectral_type, :dominant_colour_hex, :colour_u_b, :colour_i_r, :spectral_type_tholen,
            :magnitude_v10, :comet_m1, :comet_k1, :comet_m2, :comet_k2, :nongrav_a1, :nongrav_a2,
            :nongrav_a3, :nongrav_dt
        )
        ON CONFLICT(object_id) DO UPDATE SET
            geometric_albedo = COALESCE(visual_properties.geometric_albedo, excluded.geometric_albedo),
            bond_albedo = COALESCE(visual_properties.bond_albedo, excluded.bond_albedo),
            absolute_magnitude_h = COALESCE(visual_properties.absolute_magnitude_h, excluded.absolute_magnitude_h),
            colour_b_v = COALESCE(visual_properties.colour_b_v, excluded.colour_b_v),
            spectral_type = COALESCE(visual_properties.spectral_type, excluded.spectral_type),
            dominant_colour_hex = COALESCE(visual_properties.dominant_colour_hex, excluded.dominant_colour_hex),
            colour_u_b = COALESCE(visual_properties.colour_u_b, excluded.colour_u_b),
            colour_i_r = COALESCE(visual_properties.colour_i_r, excluded.colour_i_r),
            spectral_type_tholen = COALESCE(visual_properties.spectral_type_tholen, excluded.spectral_type_tholen),
            magnitude_v10 = COALESCE(visual_properties.magnitude_v10, excluded.magnitude_v10),
            comet_m1 = COALESCE(visual_properties.comet_m1, excluded.comet_m1),
            comet_k1 = COALESCE(visual_properties.comet_k1, excluded.comet_k1),
            comet_m2 = COALESCE(visual_properties.comet_m2, excluded.comet_m2),
            comet_k2 = COALESCE(visual_properties.comet_k2, excluded.comet_k2),
            nongrav_a1 = COALESCE(visual_properties.nongrav_a1, excluded.nongrav_a1),
            nongrav_a2 = COALESCE(visual_properties.nongrav_a2, excluded.nongrav_a2),
            nongrav_a3 = COALESCE(visual_properties.nongrav_a3, excluded.nongrav_a3),
            nongrav_dt = COALESCE(visual_properties.nongrav_dt, excluded.nongrav_dt),
            updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        params,
    )


def add_classification(conn, object_id: str, label: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO classifications (object_id, label) VALUES (?, ?)",
        (object_id, label),
    )


def add_source(conn, *, object_id: str | None, table_name: str,
               source_name: str, source_url: str | None = None,
               field_name: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO sources (object_id, table_name, field_name, source_name, source_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (object_id, table_name, field_name, source_name, source_url),
    )


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------
BACKOFF_BASE = 2.0
BACKOFF_BASE_429 = 4.0  # a rate limiter is asking for a longer pause than a 502
BACKOFF_CAP = 120.0
# One fetch may try this many times, and may spend this many seconds of wall
# clock doing so. Both limits are deliberate:
#   * attempts alone do not cover an outage — eight attempts on this schedule
#     wait at most 2+4+8+16+32+64+120 = 246 s, and the 03:00/04:05 UTC JPL SBDB
#     502s on 2026-09-20 each ran for a few minutes;
#   * time alone does not bound the work — with no attempt limit a fast-failing
#     endpoint would be hammered flat out until the budget ran down.
FETCH_MAX_ATTEMPTS = 8
FETCH_BUDGET_SECONDS = 300.0


def backoff_seconds(attempt: int, *, base: float = BACKOFF_BASE, cap: float = BACKOFF_CAP) -> float:
    """Exponential back-off with full jitter: 0..min(cap, base * 2**attempt)."""
    return random.uniform(0, min(cap, base * (2 ** attempt)))


class RetryBudget:
    """How much a single fetch may spend: `max_attempts` tries, `seconds` of
    wall clock from the first one.

    No attempt is started once the budget is gone and no sleep runs past it, so
    a fetch cannot wait indefinitely on an upstream that never recovers: the
    ceiling is `seconds` plus the one request timeout already in flight when
    the deadline passes.
    """

    def __init__(self, max_attempts: int = FETCH_MAX_ATTEMPTS,
                 seconds: float = FETCH_BUDGET_SECONDS) -> None:
        self.max_attempts = max(1, max_attempts)
        self.seconds = max(0.0, seconds)
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def remaining(self) -> float:
        return self.seconds - self.elapsed

    def next_delay(self, attempt: int, *, base: float = BACKOFF_BASE) -> float | None:
        """Seconds to wait before attempt `attempt + 1` (0-based), or None when
        the attempt limit or the time budget says to give up now."""
        if attempt + 1 >= self.max_attempts:
            return None
        remaining = self.remaining
        if remaining <= 0:
            return None
        return min(backoff_seconds(attempt, base=base), remaining)


def _fetch(what: str, url: str, *, params: dict | None, retries: int, timeout: int,
           budget: float, extract):
    """Shared retry loop for fetch_json / fetch_bytes.

    One loop rather than two so both helpers obey the same budget, the same
    429 handling, and the same give-up message.
    """
    policy = RetryBudget(max_attempts=retries, seconds=budget)
    last_err: Exception | None = None
    last_status: int | None = None
    attempts = 0
    for attempt in range(policy.max_attempts):
        attempts = attempt + 1
        base = BACKOFF_BASE
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                last_status = 429
                base = BACKOFF_BASE_429
            else:
                r.raise_for_status()
                return extract(r)
        except (requests.RequestException, ValueError) as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None:
                last_status = status
        delay = policy.next_delay(attempt, base=base)
        if delay is None:
            break
        time.sleep(delay)
    detail = f"last status {last_status}" if last_status is not None else str(last_err)
    raise RuntimeError(
        f"{what} gave up after {attempts}/{policy.max_attempts} attempts in "
        f"{policy.elapsed:.0f}s of {policy.seconds:.0f}s budget ({detail}) for {url}"
    )


def fetch_json(url: str, params: dict | None = None, retries: int = FETCH_MAX_ATTEMPTS,
               timeout: int = 30, budget: float = FETCH_BUDGET_SECONDS) -> dict:
    """GET and decode JSON, retrying transient failures within the budget."""
    return _fetch("fetch_json", url, params=params, retries=retries, timeout=timeout,
                  budget=budget, extract=lambda r: r.json())


def fetch_bytes(url: str, params: dict | None = None, retries: int = FETCH_MAX_ATTEMPTS,
                timeout: int = 30, budget: float = FETCH_BUDGET_SECONDS) -> bytes:
    """GET raw bytes, retrying transient failures within the budget."""
    return _fetch("fetch_bytes", url, params=params, retries=retries, timeout=timeout,
                  budget=budget, extract=lambda r: r.content)


def fetch_text(url: str, params: dict | None = None, retries: int = FETCH_MAX_ATTEMPTS,
               timeout: int = 30, encoding: str = "utf-8",
               budget: float = FETCH_BUDGET_SECONDS) -> str:
    """Text fetch with the same retry/429 handling as fetch_bytes (a thin
    decode on top of it, rather than a second copy of the retry loop).
    Decodes leniently (errors="replace") since upstream pages occasionally
    carry a stray non-UTF-8 byte — see ingest_showers's module docstring for
    a concrete example."""
    raw = fetch_bytes(url, params=params, retries=retries, timeout=timeout, budget=budget)
    return raw.decode(encoding, errors="replace")


# ---------------------------------------------------------------------------
# Slug / ID helpers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def asteroid_id(spkid_or_num: Any, name: str | None = None) -> str:
    if name:
        return f"ast-{spkid_or_num}-{slugify(name)}"
    return f"ast-{spkid_or_num}"


def comet_id(designation: str) -> str:
    return f"comet-{slugify(designation)}"

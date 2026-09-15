"""FastAPI REST front-end for the solar-system-db catalogue.

Read-only. Mirrors the MCP server's tools as HTTP endpoints, calling the same
shared data-access layer (solar_db.data_access) — so the two interfaces can't
drift apart. **For astronomy, not astrology** — see the project README.

OpenAPI spec is served at /openapi.json; Swagger UI at /docs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make solar_db importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from solar_db import SolarDB, compute_heliocentric_position, next_perihelion_jd
from solar_db.positions import date_to_jd
from solar_db.sky_lookup import SkyLookupError, resolve_and_report

# --------------------------------------------------------------------------
# App + rate limiter
# --------------------------------------------------------------------------
db = SolarDB()

limiter = Limiter(key_func=get_remote_address,
                  default_limits=["60/minute", "1000/day"])

app = FastAPI(
    title="solar-system-db REST API",
    description=(
        "A queryable catalogue of known solar-system objects — planets, moons, "
        "dwarf planets, asteroids, comets, TNOs, centaurs, and rings. Sourced "
        "from NASA/JPL and the IAU Minor Planet Center. "
        "**For astronomy, science education, sci-fi worldbuilding, and "
        "model-building — not astrology.**"
    ),
    version="0.1.0",
    contact={"name": "solar-system-db",
             "url": "https://github.com/wizzouk2/solar-system-db"},
    license_info={"name": "MIT",
                  "url": "https://opensource.org/licenses/MIT"},
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------
@app.get("/api/v1/objects", tags=["catalog"], summary="Filter solar-system objects")
@limiter.limit("60/minute")
def get_objects(
    request: Request,
    type: Optional[str] = Query(None, description="object_type filter (planet/moon/asteroid/comet/...)"),
    parent: Optional[str] = Query(None, description="parent body name/id (e.g. 'Jupiter')"),
    min_radius_km: Optional[float] = None,
    max_radius_km: Optional[float] = None,
    max_eccentricity: Optional[float] = None,
    min_semi_major_axis_au: Optional[float] = None,
    max_semi_major_axis_au: Optional[float] = None,
    neo: Optional[bool] = None,
    pha: Optional[bool] = None,
    named_only: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return {
        "results": db.find_objects(
            object_type=type, parent=parent,
            min_radius_km=min_radius_km, max_radius_km=max_radius_km,
            max_eccentricity=max_eccentricity,
            min_semi_major_axis_au=min_semi_major_axis_au,
            max_semi_major_axis_au=max_semi_major_axis_au,
            neo=neo, pha=pha, named_only=named_only,
            limit=limit, offset=offset,
        ),
        "limit": limit, "offset": offset,
    }


@app.get("/api/v1/objects/{name_or_designation:path}",
         tags=["catalog"], summary="Get full record for one object")
@limiter.limit("60/minute")
def get_object(request: Request, name_or_designation: str):
    obj = db.get_object(name_or_designation)
    if obj is None:
        raise HTTPException(status_code=404,
                            detail=f"No object found matching {name_or_designation!r}")
    return obj


@app.get("/api/v1/planets/{name}/moons", tags=["catalog"],
         summary="List moons of a planet or dwarf planet")
@limiter.limit("60/minute")
def list_moons(request: Request, name: str):
    return {"results": db.list_moons(name)}


@app.get("/api/v1/planets/{name}/rings", tags=["catalog"],
         summary="List rings of a planet or dwarf planet")
@limiter.limit("60/minute")
def list_rings(request: Request, name: str):
    return {"results": db.get_rings(name)}


@app.get("/api/v1/dwarf-planets", tags=["catalog"],
         summary="IAU dwarf planets (+ candidates if requested)")
@limiter.limit("60/minute")
def list_dwarf_planets(request: Request, include_candidates: bool = False):
    return {"results": db.list_dwarf_planets(include_candidates=include_candidates)}


@app.get("/api/v1/neos", tags=["catalog"], summary="Near-Earth Objects")
@limiter.limit("60/minute")
def list_neos(request: Request,
              min_diameter_km: Optional[float] = None,
              max_diameter_km: Optional[float] = None,
              limit: int = Query(200, ge=1, le=1000)):
    return {"results": db.list_neos(min_diameter_km=min_diameter_km,
                                     max_diameter_km=max_diameter_km,
                                     limit=limit)}


@app.get("/api/v1/comets/periodic", tags=["catalog"],
         summary="Numbered periodic comets")
@limiter.limit("60/minute")
def list_periodic_comets(request: Request,
                          limit: int = Query(500, ge=1, le=2000)):
    return {"results": db.list_periodic_comets(limit=limit)}


@app.get("/api/v1/tnos", tags=["catalog"],
         summary="Trans-Neptunian objects + centaurs")
@limiter.limit("60/minute")
def list_tnos(request: Request, limit: int = Query(500, ge=1, le=2000)):
    return {"results": db.list_tnos(limit=limit)}


@app.get("/api/v1/search", tags=["catalog"],
         summary="Fuzzy text search across names/designations/discoverers")
@limiter.limit("60/minute")
def search(request: Request,
           q: str = Query(..., min_length=1, description="Search query"),
           limit: int = Query(20, ge=1, le=100)):
    return {"query": q, "results": db.search(q, limit=limit)}


# --------------------------------------------------------------------------
# Positions / ephemeris
# --------------------------------------------------------------------------
@app.get("/api/v1/positions/{name_or_designation:path}", tags=["positions"],
         summary="Heliocentric position by two-body Kepler propagation")
@limiter.limit("60/minute")
def compute_position(request: Request, name_or_designation: str,
                      date: str = Query(..., description="ISO date or datetime")):
    elements = db.get_orbital_elements(name_or_designation)
    if not elements:
        raise HTTPException(status_code=404,
                            detail=f"No object found matching {name_or_designation!r}")
    try:
        jd = date_to_jd(date)
        return {
            "name": elements.get("name"),
            "designation": elements.get("designation"),
            "input_date": date,
            **compute_heliocentric_position(elements, jd),
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/v1/sky/{name_or_designation:path}", tags=["positions"],
         summary="Where an object appears in Earth's sky (RA/Dec, constellation, optional observer view)")
@limiter.limit("60/minute")
def sky_position(request: Request, name_or_designation: str,
                 date: Optional[str] = Query(None, description="ISO date or datetime (UTC); default now"),
                 lat: Optional[float] = Query(None, ge=-90, le=90,
                                              description="Observer latitude, degrees north"),
                 lon: Optional[float] = Query(None, ge=-180, le=180,
                                              description="Observer longitude, degrees east")):
    """Geocentric RA/Dec (J2000), constellation, hemisphere, distance from
    Earth and elongation from the Sun. Supply `lat` and `lon` together to add
    altitude/azimuth, whether it is up after dark, and rise/transit/set for
    that UT day. Moons report their parent's position. Two-body accuracy (~1°)."""
    try:
        return resolve_and_report(db, name_or_designation, date, lat, lon)
    except SkyLookupError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@app.get("/api/v1/perihelion/{name_or_designation:path}",
         tags=["positions"], summary="Next perihelion JD")
@limiter.limit("60/minute")
def next_perihelion(request: Request, name_or_designation: str):
    elements = db.get_orbital_elements(name_or_designation)
    if not elements:
        raise HTTPException(status_code=404,
                            detail=f"No object found matching {name_or_designation!r}")
    try:
        return {
            "name": elements.get("name"),
            "designation": elements.get("designation"),
            "next_perihelion_jd": next_perihelion_jd(elements),
            "orbital_period_days": elements.get("orbital_period_days"),
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# --------------------------------------------------------------------------
# Reference
# --------------------------------------------------------------------------
@app.get("/api/v1/object-types", tags=["reference"],
         summary="Object types present in the catalogue and their counts")
@limiter.limit("60/minute")
def list_object_types(request: Request):
    return {"results": db.list_object_types()}


@app.get("/api/v1/sources", tags=["reference"],
         summary="Upstream data sources + last-retrieved timestamps")
@limiter.limit("60/minute")
def get_sources(request: Request):
    return {"results": db.get_sources()}


@app.get("/api/v1/schema", tags=["reference"], response_class=PlainTextResponse,
         summary="SQLite schema DDL (read-only)")
@limiter.limit("30/minute")
def get_schema(request: Request):
    return db.get_schema()


@app.get("/api/v1/stats", tags=["reference"],
         summary="Catalogue stats + last refresh timestamp")
@limiter.limit("60/minute")
def get_stats(request: Request):
    return db.stats()


# --------------------------------------------------------------------------
# Healthcheck
# --------------------------------------------------------------------------
@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok", "total_objects": db.stats()["total_objects"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8003")),
        reload=False,
    )

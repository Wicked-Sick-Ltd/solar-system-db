"""FastAPI REST front-end for the solar-system-db catalogue.

Read-only HTTP/JSON over the SQLite catalogue. Many routes call the shared
`solar_db` data-access layer, as the MCP server does, but REST and MCP are
maintained as separate surfaces and can diverge; a later contract PR is
meant to pin them together. **For astronomy, not astrology** — see the
project README.

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
from fastapi.responses import PlainTextResponse
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
             "url": "https://github.com/Wicked-Sick-Ltd/solar-system-db"},
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
    orbit_class: Optional[str] = Query(None, description="SBDB orbit class code, e.g. MBA, APO, TNO, JFc"),
    max_moid_au: Optional[float] = Query(None, ge=0, description="Earth MOID at most this many AU"),
    min_diameter_km: Optional[float] = Query(None, ge=0),
    max_condition_code: Optional[int] = Query(None, ge=0, le=9, description="orbit uncertainty code 0 (best) … 9"),
    discovered_after: Optional[str] = Query(None, description="ISO date"),
    after: Optional[str] = Query(None, description="keyset pagination: last id of the previous page"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0, description="ignored when `after` is given"),
):
    """Filter the catalogue. For walks over all 1.4 M bodies use `after`
    (keyset pagination) rather than `offset`; `next_after` is null on the last page."""
    results = db.find_objects(
        object_type=type, parent=parent,
        min_radius_km=min_radius_km, max_radius_km=max_radius_km,
        max_eccentricity=max_eccentricity,
        min_semi_major_axis_au=min_semi_major_axis_au,
        max_semi_major_axis_au=max_semi_major_axis_au,
        neo=neo, pha=pha, named_only=named_only,
        orbit_class=orbit_class, max_moid_au=max_moid_au, min_diameter_km=min_diameter_km,
        max_condition_code=max_condition_code, discovered_after=discovered_after,
        after=after, limit=limit, offset=offset,
    )
    return {
        "results": results, "limit": limit, "offset": offset,
        "next_after": results[-1]["id"] if after is not None and len(results) == limit else None,
    }


# --------------------------------------------------------------------------
# Per-object detail (v2) and bulk close approaches — registered BEFORE the
# catch-all /objects/{name:path} route so sub-paths are not swallowed by it
# --------------------------------------------------------------------------
@app.get("/api/v1/objects/{name_or_designation:path}/close-approaches", tags=["detail"],
         summary="Close approaches of one body to the planets / Moon")
@limiter.limit("60/minute")
def object_close_approaches(request: Request, name_or_designation: str,
                            date_min: Optional[str] = Query(None, alias="from"),
                            date_max: Optional[str] = Query(None, alias="to"),
                            body: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)):
    rows = db.close_approaches_for(name_or_designation, date_min=date_min, date_max=date_max, body=body, limit=limit)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"No object found matching {name_or_designation!r}")
    return {"object": name_or_designation, "results": rows}


@app.get("/api/v1/objects/{name_or_designation:path}/discovery", tags=["detail"], summary="Discovery circumstances")
@limiter.limit("60/minute")
def object_discovery(request: Request, name_or_designation: str):
    d = db.get_discovery(name_or_designation)
    if d is None:
        raise HTTPException(status_code=404, detail=f"No object found matching {name_or_designation!r}")
    return d


@app.get("/api/v1/objects/{name_or_designation:path}/designations", tags=["detail"],
         summary="Every number, name, provisional and alternate designation")
@limiter.limit("60/minute")
def object_designations(request: Request, name_or_designation: str):
    d = db.get_designations(name_or_designation)
    if d is None:
        raise HTTPException(status_code=404, detail=f"No object found matching {name_or_designation!r}")
    return {"results": d}


@app.get("/api/v1/planets/{name}/atmosphere", tags=["detail"], summary="Atmosphere (NASA fact sheet)")
@limiter.limit("60/minute")
def planet_atmosphere(request: Request, name: str):
    a = db.get_atmosphere(name)
    if a is None:
        raise HTTPException(status_code=404, detail=f"No object found matching {name!r}")
    return a


@app.get("/api/v1/close-approaches", tags=["detail"],
         summary="All close approaches to a body in a date window, nearest first")
@limiter.limit("60/minute")
def close_approaches(request: Request,
                     date_min: str = Query(..., alias="from", description="ISO date"),
                     date_max: str = Query(..., alias="to", description="ISO date"),
                     body: str = Query("Earth"),
                     max_dist_au: float = Query(0.05, gt=0, le=1.0),
                     limit: int = Query(200, ge=1, le=1000)):
    return {"from": date_min, "to": date_max, "body": body, "max_dist_au": max_dist_au,
            "results": db.close_approaches_between(date_min, date_max, body=body, max_dist_au=max_dist_au, limit=limit)}


@app.get("/api/v1/meteor-showers", tags=["catalog"], summary="IAU meteor showers")
@limiter.limit("60/minute")
def list_meteor_showers(request: Request,
                        established_only: bool = Query(False, description="Keep only MDC status codes 1 (single established shower, group) or 6 (member of the established group)"),
                        active_on: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD); filters to showers active on this date"),
                        limit: int = Query(500, ge=1, le=1000)):
    try:
        items = db.list_meteor_showers(established_only=established_only, active_on=active_on, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"items": items, "count": len(items)}


@app.get("/api/v1/meteor-showers/{code}", tags=["detail"], summary="One IAU meteor shower by code or name")
@limiter.limit("60/minute")
def get_meteor_shower(request: Request, code: str):
    shower = db.get_meteor_shower(code)
    if shower is None:
        raise HTTPException(status_code=404, detail=f"No meteor shower found matching {code!r}")
    return shower


@app.get("/api/v1/download", tags=["reference"], summary="Where to get the whole database as one file")
@limiter.limit("60/minute")
def download_manifest(request: Request):
    m = db.download_manifest()
    if m is None:
        raise HTTPException(status_code=404, detail="No published artefact yet — the nightly publish has not run on this host.")
    return m


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


def run() -> None:
    """Start the API server for the packaged console script."""
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8003")),
        reload=False,
    )


if __name__ == "__main__":
    run()

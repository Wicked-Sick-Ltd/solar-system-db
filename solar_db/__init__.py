"""solar_db — shared read-only data-access layer for the SQLite catalogue.

Both the MCP server (mcp-server/server.py) and the REST API (api/main.py)
import from here, so their behaviour cannot drift apart.
"""
from .data_access import SolarDB
from .positions import compute_heliocentric_position, next_perihelion_jd
from .sky import sky_report

__all__ = ["SolarDB", "compute_heliocentric_position", "next_perihelion_jd", "sky_report"]

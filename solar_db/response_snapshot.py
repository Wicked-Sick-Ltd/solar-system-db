"""Compact metadata for rows and identity read in one SQLite transaction."""

from .catalogue_identity import read_identity


class CatalogueReadError(RuntimeError):
    """The bounded read failed; never expose SQLite paths or SQL to callers."""


def catalogue_snapshot(conn):
    identity = read_identity(conn)
    return {
        "schema_version": 1,
        "association": "same-read-transaction",
        **{
            key: identity[key]
            for key in (
                "status",
                "reason",
                "catalogue_id",
                "build_identifier",
                "hash_policy",
            )
        },
    }

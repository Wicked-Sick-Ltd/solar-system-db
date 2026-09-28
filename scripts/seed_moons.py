"""Natural satellites of the planets, by name where the IAU has given one and
by provisional designation (e.g. S/2003 J 5) where it hasn't.

Each entry: id, name, parent_id, plus optional discoverer/year. Physical and
orbital properties are left empty here — the high-detail values for the major
moons come from seed_major.py, and ingest_sats.py fills in the rest from JPL's
satellite tables.
"""
from __future__ import annotations

def m(name: str, parent: str, *, discoverer: str | None = None,
      year: str | None = None) -> dict:
    """Helper — build a moon row with sensible defaults."""
    slug = name.lower().replace("'", "").replace(" ", "-")
    return {
        "id": f"moon-{slug}",
        "name": name,
        "parent_id": parent,
        "discoverer": discoverer,
        "discovery_date": year,
    }


# Jupiter — all currently named moons (74 named; remaining ~21 are S/YYYY).
JUPITER_NAMED = [
    # Inner / regular
    m("Metis",      "planet-jupiter", discoverer="Stephen P. Synnott", year="1979"),
    m("Adrastea",   "planet-jupiter", discoverer="David Jewitt et al.", year="1979"),
    m("Amalthea",   "planet-jupiter", discoverer="Edward Emerson Barnard", year="1892"),
    m("Thebe",      "planet-jupiter", discoverer="Stephen P. Synnott", year="1979"),

    # Himalia group
    m("Leda",       "planet-jupiter", discoverer="Charles T. Kowal", year="1974"),
    m("Himalia",    "planet-jupiter", discoverer="Charles Dillon Perrine", year="1904"),
    m("Lysithea",   "planet-jupiter", discoverer="Seth Barnes Nicholson", year="1938"),
    m("Elara",      "planet-jupiter", discoverer="Charles Dillon Perrine", year="1905"),
    m("Dia",        "planet-jupiter", discoverer="Spacewatch", year="2000"),

    # Carpo / Themisto
    m("Themisto",   "planet-jupiter", discoverer="Charles T. Kowal", year="1975"),
    m("Carpo",      "planet-jupiter", discoverer="Scott S. Sheppard et al.", year="2003"),
    m("Valetudo",   "planet-jupiter", discoverer="Scott S. Sheppard et al.", year="2016"),

    # Ananke group (retrograde)
    m("Euporie",    "planet-jupiter", year="2001"),
    m("Eupheme",    "planet-jupiter", year="2003"),
    m("Mneme",      "planet-jupiter", year="2003"),
    m("Harpalyke",  "planet-jupiter", year="2000"),
    m("Hermippe",   "planet-jupiter", year="2001"),
    m("Praxidike",  "planet-jupiter", year="2000"),
    m("Thyone",     "planet-jupiter", year="2001"),
    m("Ananke",     "planet-jupiter", discoverer="Seth Barnes Nicholson", year="1951"),
    m("Iocaste",    "planet-jupiter", year="2000"),
    m("Euanthe",    "planet-jupiter", year="2001"),
    m("Thelxinoe",  "planet-jupiter", year="2003"),
    m("Helike",     "planet-jupiter", year="2003"),
    m("Orthosie",   "planet-jupiter", year="2001"),

    # Carme group (retrograde)
    m("Pasithee",   "planet-jupiter", year="2001"),
    m("Chaldene",   "planet-jupiter", year="2000"),
    m("Arche",      "planet-jupiter", year="2002"),
    m("Isonoe",     "planet-jupiter", year="2000"),
    m("Erinome",    "planet-jupiter", year="2000"),
    m("Kale",       "planet-jupiter", year="2001"),
    m("Aitne",      "planet-jupiter", year="2001"),
    m("Taygete",    "planet-jupiter", year="2000"),
    m("Carme",      "planet-jupiter", discoverer="Seth Barnes Nicholson", year="1938"),
    m("Kalyke",     "planet-jupiter", year="2000"),
    m("Eukelade",   "planet-jupiter", year="2003"),
    m("Kallichore", "planet-jupiter", year="2003"),
    m("Eirene",     "planet-jupiter", year="2003"),
    m("Philophrosyne","planet-jupiter", year="2003"),

    # Pasiphae group (retrograde)
    m("Eurydome",   "planet-jupiter", year="2001"),
    m("Sponde",     "planet-jupiter", year="2001"),
    m("Pasiphae",   "planet-jupiter", discoverer="Philibert Jacques Melotte", year="1908"),
    m("Megaclite",  "planet-jupiter", year="2000"),
    m("Sinope",     "planet-jupiter", discoverer="Seth Barnes Nicholson", year="1914"),
    m("Hegemone",   "planet-jupiter", year="2003"),
    m("Aoede",      "planet-jupiter", year="2003"),
    m("Callirrhoe", "planet-jupiter", year="1999"),
    m("Autonoe",    "planet-jupiter", year="2001"),
    m("Cyllene",    "planet-jupiter", year="2003"),
    m("Kore",       "planet-jupiter", year="2003"),
    m("Herse",      "planet-jupiter", year="2003"),
    m("S/2003 J 10","planet-jupiter", year="2003"),
    m("S/2003 J 12","planet-jupiter", year="2003"),
    m("S/2003 J 19","planet-jupiter", year="2003"),
    m("S/2003 J 23","planet-jupiter", year="2003"),
    m("S/2011 J 1", "planet-jupiter", year="2011"),
    m("S/2011 J 3", "planet-jupiter", year="2011"),
    m("S/2018 J 2", "planet-jupiter", year="2018"),
    m("S/2018 J 4", "planet-jupiter", year="2018"),
    m("S/2021 J 1", "planet-jupiter", year="2021"),
    m("S/2021 J 2", "planet-jupiter", year="2021"),
    m("S/2021 J 3", "planet-jupiter", year="2021"),
    m("S/2021 J 4", "planet-jupiter", year="2021"),
    m("S/2021 J 5", "planet-jupiter", year="2021"),
    m("S/2021 J 6", "planet-jupiter", year="2021"),
    m("S/2022 J 1", "planet-jupiter", year="2022"),
    m("S/2022 J 2", "planet-jupiter", year="2022"),
    m("S/2022 J 3", "planet-jupiter", year="2022"),
    m("S/2003 J 2", "planet-jupiter", year="2003"),
    m("S/2003 J 4", "planet-jupiter", year="2003"),
    m("S/2003 J 9", "planet-jupiter", year="2003"),
    m("S/2003 J 16","planet-jupiter", year="2003"),
    m("S/2003 J 24","planet-jupiter", year="2003"),
]

# Saturn — IAU-named moons only. Provisional designations come from JPL's
# satellite elements table (ingest_sats), which is the authority for which
# moons exist. Never invent designations here: any seeded name JPL doesn't
# list becomes an empty "ghost" moon on the public site.
SATURN_NAMED = [
    m("Pan",         "planet-saturn", discoverer="Mark R. Showalter", year="1990"),
    m("Daphnis",     "planet-saturn", discoverer="Cassini Imaging Team", year="2005"),
    m("Atlas",       "planet-saturn", discoverer="Richard Terrile", year="1980"),
    m("Prometheus",  "planet-saturn", discoverer="Voyager Imaging Team", year="1980"),
    m("Pandora",     "planet-saturn", discoverer="Voyager Imaging Team", year="1980"),
    m("Epimetheus",  "planet-saturn", discoverer="Richard Walker", year="1980"),
    m("Janus",       "planet-saturn", discoverer="Audouin Dollfus", year="1966"),
    m("Aegaeon",     "planet-saturn", discoverer="Cassini Imaging Team", year="2008"),
    m("Methone",     "planet-saturn", discoverer="Cassini Imaging Team", year="2004"),
    m("Anthe",       "planet-saturn", discoverer="Cassini Imaging Team", year="2007"),
    m("Pallene",     "planet-saturn", discoverer="Cassini Imaging Team", year="2004"),
    m("Telesto",     "planet-saturn", year="1980"),
    m("Calypso",     "planet-saturn", year="1980"),
    m("Polydeuces",  "planet-saturn", discoverer="Cassini Imaging Team", year="2004"),
    m("Helene",      "planet-saturn", year="1980"),
    m("Kiviuq",      "planet-saturn", year="2000"),
    m("Ijiraq",      "planet-saturn", year="2000"),
    m("Paaliaq",     "planet-saturn", year="2000"),
    m("Skathi",      "planet-saturn", year="2000"),
    m("Albiorix",    "planet-saturn", year="2000"),
    m("Bebhionn",    "planet-saturn", year="2004"),
    m("Erriapus",    "planet-saturn", year="2000"),
    m("Skoll",       "planet-saturn", year="2006"),
    m("Siarnaq",     "planet-saturn", year="2000"),
    m("Tarqeq",      "planet-saturn", year="2007"),
    m("Tarvos",      "planet-saturn", year="2000"),
    m("Greip",       "planet-saturn", year="2006"),
    m("Hyrrokkin",   "planet-saturn", year="2006"),
    m("Jarnsaxa",    "planet-saturn", year="2006"),
    m("Mundilfari",  "planet-saturn", year="2000"),
    m("Bergelmir",   "planet-saturn", year="2004"),
    m("Narvi",       "planet-saturn", year="2003"),
    m("Suttungr",    "planet-saturn", year="2000"),
    m("Hati",        "planet-saturn", year="2004"),
    m("Farbauti",    "planet-saturn", year="2004"),
    m("Thrymr",      "planet-saturn", year="2000"),
    m("Aegir",       "planet-saturn", year="2004"),
    m("Bestla",      "planet-saturn", year="2004"),
    m("Fenrir",      "planet-saturn", year="2004"),
    m("Surtur",      "planet-saturn", year="2006"),
    m("Kari",        "planet-saturn", year="2006"),
    m("Ymir",        "planet-saturn", year="2000"),
    m("Loge",        "planet-saturn", year="2006"),
    m("Fornjot",     "planet-saturn", year="2004"),
    m("Geirrod",     "planet-saturn", year="2019"),
    m("Gerd",        "planet-saturn", year="2019"),
    m("Gridr",       "planet-saturn", year="2019"),
    m("Angrboda",    "planet-saturn", year="2019"),
    m("Skrymir",     "planet-saturn", year="2019"),
    m("Gunnlod",     "planet-saturn", year="2019"),
    m("Thiazzi",     "planet-saturn", year="2019"),
    m("Alvaldi",     "planet-saturn", year="2019"),
    m("Beli",        "planet-saturn", year="2019"),
    m("Eggther",     "planet-saturn", year="2019"),
]
# Uranus — 28 moons. The five major handled in seed_major.
URANUS_OTHERS = [
    m("Cordelia",   "planet-uranus", year="1986"),
    m("Ophelia",    "planet-uranus", year="1986"),
    m("Bianca",     "planet-uranus", year="1986"),
    m("Cressida",   "planet-uranus", year="1986"),
    m("Desdemona",  "planet-uranus", year="1986"),
    m("Juliet",     "planet-uranus", year="1986"),
    m("Portia",     "planet-uranus", year="1986"),
    m("Rosalind",   "planet-uranus", year="1986"),
    m("Cupid",      "planet-uranus", year="2003"),
    m("Belinda",    "planet-uranus", year="1986"),
    m("Perdita",    "planet-uranus", year="1986"),
    m("Puck",       "planet-uranus", year="1985"),
    m("Mab",        "planet-uranus", year="2003"),
    m("Francisco",  "planet-uranus", year="2001"),
    m("Caliban",    "planet-uranus", year="1997"),
    m("Stephano",   "planet-uranus", year="1999"),
    m("Trinculo",   "planet-uranus", year="2001"),
    m("Sycorax",    "planet-uranus", year="1997"),
    m("Margaret",   "planet-uranus", year="2003"),
    m("Prospero",   "planet-uranus", year="1999"),
    m("Setebos",    "planet-uranus", year="1999"),
    m("Ferdinand",  "planet-uranus", year="2001"),
    m("S/2023 U 1", "planet-uranus", year="2023"),
]

# Neptune — 16 moons, 1 major (Triton) handled in seed_major.
NEPTUNE_OTHERS = [
    m("Naiad",      "planet-neptune", year="1989"),
    m("Thalassa",   "planet-neptune", year="1989"),
    m("Despina",    "planet-neptune", year="1989"),
    m("Galatea",    "planet-neptune", year="1989"),
    m("Larissa",    "planet-neptune", year="1989"),
    m("Hippocamp",  "planet-neptune", year="2013"),
    m("Proteus",    "planet-neptune", year="1989"),
    m("Halimede",   "planet-neptune", year="2002"),
    m("Sao",        "planet-neptune", year="2002"),
    m("Laomedeia",  "planet-neptune", year="2002"),
    m("Psamathe",   "planet-neptune", year="2003"),
    m("Neso",       "planet-neptune", year="2002"),
    m("S/2002 N 5", "planet-neptune", year="2002"),
    m("S/2021 N 1", "planet-neptune", year="2021"),
]

ALL_MOONS = (
    JUPITER_NAMED + SATURN_NAMED + URANUS_OTHERS + NEPTUNE_OTHERS
)

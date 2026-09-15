"""Scrape the NASA planetary fact sheets into seed/factsheets.json.

Run by hand when NASA updates the sheets (they carry a "Last Updated" date);
the JSON is committed and reviewed like any other seed. `ingest_factsheets.py`
loads it at build time — the build never scrapes NASA.

    python scripts/scrape_factsheets.py            # fetch live pages
    python scripts/scrape_factsheets.py --from DIR # parse saved pages (tests)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, fetch_text  # noqa: E402

BASE = "https://nssdc.gsfc.nasa.gov/planetary/factsheet/"
PAGES = {  # object id → page
    "planet-mercury": "mercuryfact.html", "planet-venus": "venusfact.html", "planet-earth": "earthfact.html",
    "moon-luna": "moonfact.html", "planet-mars": "marsfact.html", "planet-jupiter": "jupiterfact.html",
    "planet-saturn": "saturnfact.html", "planet-uranus": "uranusfact.html", "planet-neptune": "neptunefact.html",
    "dwarf-pluto": "plutofact.html",
}
OUT = ROOT / "seed" / "factsheets.json"


def _clean(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def parse_value(raw: str) -> float | str | None:
    """'1,898.13' → 1898.13; '9.9250*' → 9.925; 'Unknown*' → 'Unknown'; '' → None."""
    s = raw.replace(",", "").replace("−", "-").strip()
    s = re.sub(r"[*†]+$", "", s).strip()
    if s in ("", "-", "--"):
        return None
    m = re.match(r"^[-+]?\d+(\.\d+)?([eE][-+]?\d+)?$", s)
    if m:
        return float(s)
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*(?:\(.*\)|±.*|to .*)$", s)  # "0.00335 (approx)" / "12 ± 3"
    if m:
        return float(m.group(1))
    return s


def _pre_pairs(block: str) -> list[tuple[str, str]]:
    """'Mass (10<sup>24</sup> kg)      5.9722' → ('Mass (10 24 kg)', '5.9722')."""
    pairs = []
    block = re.sub(r"<su[bp]>(.*?)</su[bp]>", r" \1", block)          # 10<sup>24</sup> → 10 24
    for line in html.unescape(re.sub(r"<[^>]+>", "", block)).splitlines():
        m = re.match(r"^\s*(.+?)(?:\s{2,}|\t)(\S.*)$", line.rstrip())
        if m:
            label = re.sub(r"\s+", " ", m.group(1)).strip()
            value = m.group(2).strip().split()[0] if m.group(2).strip() else ""
            if label and not label.endswith(":"):
                pairs.append((label, value))
    return pairs


def parse_page(page: str) -> dict[str, Any]:
    """label → first value column, from every table and every non-atmosphere <pre>
    block on the page, plus the atmosphere block."""
    values: dict[str, float | str | None] = {}

    def put(label: str, raw: str) -> None:
        if label and label not in values and not label.lower().startswith(("bulk", "orbital", "ratio")):
            values[label] = parse_value(raw)

    for row in re.findall(r"<tr.*?</tr>", page, re.S):
        cells = [_clean(c) for c in re.findall(r"<t[hd].*?</t[hd]>", row, re.S)]
        if len(cells) >= 2:
            put(cells[0], cells[1])
    atm_match = re.search(r"<h3>[^<]*Atmosphere[^<]*</h3>\s*<pre>(.*?)</pre>", page, re.S | re.I)
    for block in re.findall(r"<pre>(.*?)</pre>", page, re.S):
        if atm_match and block == atm_match.group(1):
            continue
        for label, raw in _pre_pairs(block):
            put(label, raw)
    atmosphere = parse_atmosphere(_clean(atm_match.group(1))) if atm_match else None
    return {"values": values, "atmosphere": atmosphere}


def parse_atmosphere(text: str) -> dict[str, Any]:
    def grab(pattern: str) -> str | None:
        m = re.search(pattern, text, re.I)
        return m.group(1).strip() if m else None

    def num(s: str | None) -> float | None:
        if not s:
            return None
        m = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s.replace(",", ""))
        return float(m.group()) if m else None

    pressure = grab(r"Surface pressure:\s*(.*?)(?=\s+(?:Surface|Average|Mean|Temperature|Total|Density|Scale|Wind|Diurnal|Atmospheric)\b|$)")
    temp = grab(r"(?:Temperature at 1 bar|Average temperature|Surface temperature|Mean temperature)[^:]*:\s*(.*?)(?=\s+(?:Temperature at|Diurnal|Density|Surface|Total|Scale|Wind|Mean molecular|Atmospheric)\b|$)")
    density = grab(r"(?:Density at 1 bar|Surface density|Average density)[^:]*:\s*([^A-Z]*?kg/m ?3)")
    scale = grab(r"Scale height:\s*([\d.,]+\s*km)")
    mmw = grab(r"Mean molecular weight:\s*([\d.]+)")
    wind = grab(r"Wind speeds?:?\s*(.*?)(?=\s+Scale height|\s+Mean molecular|\s+Atmospheric composition|$)")
    comp_text = grab(r"Atmospheric composition[^:]*:?\s*(.*?)(?=\s+Aerosols|\s+Notes on|$)")

    pressure_bar = None
    if pressure:
        pm = re.search(r"([\d.,]+)\s*(bars?|mb\b|millibars?|Pa\b|microbars?|µbars?|nanobars?)", pressure, re.I)
        if pm:
            v = float(pm.group(1).replace(",", ""))
            u = pm.group(2).lower()
            pressure_bar = v / 1000 if u.startswith(("mb", "milli")) else v / 1e5 if u == "pa" else \
                v / 1e6 if u.startswith(("micro", "µ")) else v / 1e9 if u.startswith("nano") else v

    composition = []
    if comp_text:
        for group in re.split(r"(?=Major|Minor)", comp_text):
            unit = "ppm" if re.search(r"\(ppm\)", group[:40]) else "ppb" if re.search(r"\(ppb\)", group[:40]) else "%"
            body = re.sub(r"^(Major|Minor)( \((?:ppm|ppb)\))?:?\s*", "", group).strip()
            # "Molecular hydrogen (H 2 ) - 89.8% (2.0%); Helium (He) - 10.2%"
            for m in re.finditer(r"([A-Z][A-Za-z0-9 ()\-]*?\)?)\s*-\s*([\d.]+)\s*(%|ppm|ppb)?\s*(?:\(([^)]*)\))?", body):
                composition.append({"species": m.group(1).strip(), "fraction": float(m.group(2)),
                                    "unit": m.group(3) or unit, "uncertainty": m.group(4)})
            # "78.084% Nitrogen (N 2 ), 20.946% Oxygen (O 2 )"  /  "9340 ppm Argon (Ar)"
            for m in re.finditer(r"([\d.]+)\s*(%|ppm|ppb)\s+([A-Z][A-Za-z\- ]*?(?:\([^)]*\))?)(?=[,;]|\s+[\d.]+\s*(?:%|ppm|ppb)|$)", body):
                composition.append({"species": m.group(3).strip(), "fraction": float(m.group(1)),
                                    "unit": m.group(2), "uncertainty": None})
    for c in composition:  # "Nitrogen (N 2 " → "Nitrogen (N2)"
        sp = re.sub(r"\(([^)]*)\)?$", lambda m: "(" + m.group(1).replace(" ", "") + ")", c["species"].strip())
        c["species"] = re.sub(r"\s+", " ", sp).strip()
    return {
        "surface_pressure_bar": pressure_bar,
        "pressure_note": pressure, "temperature_k": num(temp), "temperature_note": temp,
        "density_kg_m3": num(density), "scale_height_km": num(scale), "mean_molecular_weight": num(mmw),
        "wind_note": wind, "composition": composition, "raw": text[:2000],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--from", dest="src", help="directory of saved *fact.html pages (offline)")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)
    result: dict[str, Any] = {"_meta": {"source": BASE, "retrieved_at": time.strftime("%Y-%m-%d", time.gmtime())}}
    for obj_id, page in PAGES.items():
        text = (Path(args.src) / page).read_text() if args.src else fetch_text(BASE + page)
        parsed = parse_page(text)
        parsed["source_url"] = BASE + page
        result[obj_id] = parsed
        print(f"  {obj_id:16s} {len(parsed['values'])} values, atmosphere={'yes' if parsed['atmosphere'] else 'no'}")
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

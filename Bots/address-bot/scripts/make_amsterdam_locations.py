#!/usr/bin/env python3
"""Generate location files per municipality grouped under province folders.

This replaces the Amsterdam-only generator with a country-wide exporter based on
CBS StatLine data for neighbourhoods. Municipalities are mapped to provinces by
reading the Wikipedia list of Dutch municipalities (current as of today) and a
small set of manual overrides for historic municipalities that still appear in

The output layout looks like:

    out/locations/<province>/<municipality>/LOCATIONS.js

Each LOCATIONS.js file contains entries formatted as
"Neighbourhood, Municipality, Province, Netherlands".
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CBS_WIJKEN_URL = (
    "https://opendata.cbs.nl/ODataApi/odata/84583NED/"
    "WijkenEnBuurten?$top=20000"
)
WIKIPEDIA_MUNICIPALITIES_URL = "https://nl.wikipedia.org/wiki/Lijst_van_Nederlandse_gemeenten"

# Convert Dutch province labels from the Wikipedia table to the names requested
# for the folder layout.
PROVINCE_NAME_REMAP: Mapping[str, str] = {
    "Noord-Brabant": "North Brabant",
    "Noord Brabant": "North Brabant",
    "Noord-Holland": "North Holland",
    "Noord Holland": "North Holland",
    "Zuid-Holland": "South Holland",
    "Zuid Holland": "South Holland",
    "Fryslân": "Friesland",
    "Friesland": "Friesland",
    "Caribisch Nederland": "Caribbean Netherlands",
}

# Municipalities that still exist in the CBS 2019 dataset but are no longer
# listed on the current Wikipedia page. These receive manual province mappings
# so we can still place their neighbourhoods.
MUNICIPALITY_OVERRIDES: Mapping[str, str] = {
    "Appingedam": "Groningen",
    "Beemster": "North Holland",
    "Bergen (L.)": "Limburg",
    "Bergen (NH.)": "North Holland",
    "Boxmeer": "North Brabant",
    "Brielle": "South Holland",
    "Cuijk": "North Brabant",
    "Dantumadiel": "Friesland",
    "De Fryske Marren": "Friesland",
    "Delfzijl": "Groningen",
    "Haaren": "North Brabant",
    "Grave": "North Brabant",
    "Heerhugowaard": "North Holland",
    "Hellevoetsluis": "South Holland",
    "Landerd": "North Brabant",
    "Langedijk": "North Holland",
    "Loppersum": "Groningen",
    "Mill en Sint Hubert": "North Brabant",
    "Nuenen, Gerwen en Nederwetten": "North Brabant",
    "Sint Anthonis": "North Brabant",
    "Tytsjerksteradiel": "Friesland",
    "Uden": "North Brabant",
    "Weesp": "North Holland",
    "Westvoorne": "South Holland",
    "'s-Gravenhage": "South Holland",
    "Bergen (NH)": "North Holland",
}


def _normalize_cell(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = text.replace("\xad", "")
    text = text.replace("\u200b", "")
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class WikiTableParser(HTMLParser):
    """Minimal HTML table parser geared toward Wikipedia wikitables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._capturing = False
        self._table_depth = 0
        self._current_table: Optional[List[List[str]]] = None
        self._current_row: Optional[List[str]] = None
        self._capturing_cell = False
        self._cell_buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            classes = set((attrs_dict.get("class") or "").split())
            if not self._capturing and "wikitable" in classes:
                self._capturing = True
                self._table_depth = 1
                self._current_table = []
            elif self._capturing:
                self._table_depth += 1
        elif tag == "tr" and self._capturing:
            self._current_row = []
        elif tag in {"td", "th"} and self._capturing and self._current_row is not None:
            self._capturing_cell = True
            self._cell_buffer = []
        elif tag == "br" and self._capturing_cell:
            self._cell_buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._capturing:
            self._table_depth -= 1
            if self._table_depth == 0:
                if self._current_table:
                    self.tables.append(self._current_table)
                self._capturing = False
                self._current_table = None
        elif tag == "tr" and self._capturing:
            if self._current_table is not None and self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag in {"td", "th"} and self._capturing_cell:
            text = _normalize_cell("".join(self._cell_buffer))
            if self._current_row is not None:
                self._current_row.append(text)
            self._capturing_cell = False
            self._cell_buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing_cell:
            self._cell_buffer.append(data)


@dataclass
class Municipality:
    code: str
    name: str
    province: str
    neighbourhoods: List[str]


def fetch_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req) as resp:
        return resp.read()


def fetch_json(url: str) -> dict:
    data = fetch_bytes(url)
    text = data.decode("utf-8-sig")
    return json.loads(text)


def slugify(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    ascii_only = ascii_only.lower()
    ascii_only = ascii_only.replace("&", " and ")
    ascii_only = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    ascii_only = re.sub(r"-+", "-", ascii_only)
    return ascii_only.strip("-") or "item"


def load_municipality_provinces() -> Dict[str, str]:
    try:
        html = fetch_bytes(WIKIPEDIA_MUNICIPALITIES_URL).decode("utf-8")
    except HTTPError as err:
        raise SystemExit(f"Failed to download municipality list: {err}")

    parser = WikiTableParser()
    parser.feed(html)

    target_table: Optional[List[List[str]]] = None
    header_row: Optional[List[str]] = None
    for table in parser.tables:
        for row in table:
            if any("Gemeente" in cell for cell in row):
                header_row = row
                target_table = table
                break
        if target_table is not None:
            break

    if not target_table or not header_row:
        raise SystemExit("Could not locate municipality table on Wikipedia page")

    header = [_normalize_cell(cell) for cell in header_row]
    try:
        municipality_idx = next(idx for idx, cell in enumerate(header) if "Gemeente" in cell)
        province_idx = next(idx for idx, cell in enumerate(header) if "Provin" in cell)
    except StopIteration as err:
        raise SystemExit("Could not determine header columns in municipality table") from err

    mapping: Dict[str, str] = {}
    for row in target_table:
        if row is header_row:
            continue
        if any("Gemeente" in cell for cell in row):
            continue
        if len(row) <= max(municipality_idx, province_idx):
            continue
        municipality = row[municipality_idx].strip()
        province = row[province_idx].strip()
        if not municipality or not province:
            continue
        province = PROVINCE_NAME_REMAP.get(province, province)
        mapping[municipality] = province

    mapping.update(MUNICIPALITY_OVERRIDES)
    return mapping



def build_municipality_index(wijken_data: Iterable[dict]) -> Dict[str, Municipality]:
    muni_names: Dict[str, str] = {}
    neighbourhoods: Dict[str, set] = defaultdict(set)

    for item in wijken_data:
        key = (item.get("Key") or "").strip()
        title = (item.get("Title") or "").strip()
        municipality_code = (item.get("Municipality") or "").strip()

        if key.startswith("GM"):
            muni_names[key] = title
        elif key.startswith("BU") and municipality_code:
            if title:
                neighbourhoods[municipality_code].add(title)

    municipality_to_province = load_municipality_provinces()

    result: Dict[str, Municipality] = {}
    unresolved: List[str] = []

    for code, name in sorted(muni_names.items()):
        province = municipality_to_province.get(name)
        if not province:
            # Fallback: try removing brackets or alternate spellings.
            fallback_variants = {
                name.replace(" ('s-Gravenhage)", ""),
                name.replace("Gemeente ", ""),
                name.replace("-", " "),
            }
            province = next(
                (municipality_to_province.get(var) for var in fallback_variants if var in municipality_to_province),
                None,
            )
        if not province:
            unresolved.append(name)
            continue

        result[code] = Municipality(
            code=code,
            name=name,
            province=province,
            neighbourhoods=sorted(neighbourhoods.get(code, set()), key=str.casefold),
        )

    if unresolved:
        unresolved_list = ", ".join(sorted(unresolved))
        raise SystemExit(
            "Missing province mapping for: " + unresolved_list
        )

    return result


def write_locations(root: Path, municipality: Municipality) -> None:
    if not municipality.neighbourhoods:
        return

    province_slug = slugify(municipality.province)
    municipality_slug = slugify(municipality.name)
    out_dir = root / province_slug / municipality_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / "LOCATIONS.js"

    with out_file.open("w", encoding="utf-8") as fh:
        fh.write("LOCATIONS = [\n")
        for idx, neighbourhood in enumerate(municipality.neighbourhoods):
            location = f"{neighbourhood}, {municipality.name}, {municipality.province}, Netherlands"
            comma = "," if idx + 1 < len(municipality.neighbourhoods) else ""
            fh.write(f"  {json.dumps(location)}{comma}\n")
        fh.write("]\n")


def main() -> None:
    print("Downloading nationwide neighbourhood list…", file=sys.stderr)
    wijken_payload = fetch_json(CBS_WIJKEN_URL)
    wijken_data = wijken_payload.get("value") or []
    if not wijken_data:
        raise SystemExit("CBS dataset returned no rows.")

    municipalities = build_municipality_index(wijken_data)

    root = Path(__file__).resolve().parent.parent / "out" / "locations"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    total_files = 0
    for municipality in municipalities.values():
        write_locations(root, municipality)
        if municipality.neighbourhoods:
            total_files += 1

    print(
        f"✓ Generated {total_files} municipality files across {len(set(m.province for m in municipalities.values()))} provinces.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

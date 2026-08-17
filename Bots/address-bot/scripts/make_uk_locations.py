#!/usr/bin/env python3
"""Generate UK-style LOCATIONS.js files from OS Open Names.

The output mirrors the Netherlands location tree:

    out/locations/united-kingdom/<area>/<local-authority>/LOCATIONS.js

Each entry has four searchable parts:

    "Small place, local authority, area, United Kingdom"

For two-tier English areas, ``area`` is normally the county. For unitary
authorities, Scotland, and Wales, the OS region/country is used as the area.
OS Open Names covers Great Britain. Northern Ireland is generated separately
by ``make_northern_ireland_locations.py`` and is preserved by this exporter.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, Mapping, Sequence


DOWNLOADS_API = (
    "https://api.os.uk/downloads/v1/products/OpenNames/downloads"
    "?area=GB&format=CSV"
)
INCLUDED_LOCAL_TYPES = frozenset(
    {"City", "Town", "Village", "Hamlet", "Suburban Area", "Other Settlement"}
)
HEADER_MEMBER = "Doc/OS_Open_Names_Header.csv"


@dataclass(frozen=True)
class Location:
    name: str
    local_authority: str
    area: str

    @property
    def text(self) -> str:
        return (
            f"{self.name}, {self.local_authority}, "
            f"{self.area}, United Kingdom"
        )


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def slugify(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii").lower()
    ascii_only = ascii_only.replace("&", " and ")
    ascii_only = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    return re.sub(r"-+", "-", ascii_only).strip("-") or "item"


def choose_hierarchy(row: Mapping[str, str]) -> tuple[str, str] | None:
    """Return (local authority, area) using the most specific OS hierarchy."""
    district = clean(row.get("DISTRICT_BOROUGH"))
    county = clean(row.get("COUNTY_UNITARY"))
    region = clean(row.get("REGION"))
    country = clean(row.get("COUNTRY"))
    populated_place = clean(row.get("POPULATED_PLACE"))

    if district:
        local_authority = district
        area = county or region or country
    elif county:
        local_authority = county
        area = region or country
    elif populated_place:
        local_authority = populated_place
        area = region or country
    else:
        return None

    if not area or area.casefold() == local_authority.casefold():
        area = country or region
    if not local_authority or not area:
        return None
    return local_authority, area


def rows_from_zip(source: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(source) as archive:
        members = sorted(
            name
            for name in archive.namelist()
            if name.startswith("Data/") and name.lower().endswith(".csv")
        )
        if not members:
            raise ValueError(f"{source} contains no OS Open Names Data/*.csv files")

        with archive.open(HEADER_MEMBER) as raw_header:
            header_text = io.TextIOWrapper(raw_header, encoding="utf-8-sig").read()
        fieldnames = next(csv.reader([header_text]))

        for member in members:
            with archive.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                yield from csv.DictReader(text, fieldnames=fieldnames)


def build_locations(
    rows: Iterable[Mapping[str, str]],
    included_types: frozenset[str] = INCLUDED_LOCAL_TYPES,
) -> list[Location]:
    locations: set[Location] = set()
    for row in rows:
        if clean(row.get("TYPE")) != "populatedPlace":
            continue
        if clean(row.get("LOCAL_TYPE")) not in included_types:
            continue

        name = clean(row.get("NAME1"))
        hierarchy = choose_hierarchy(row)
        if not name or hierarchy is None:
            continue
        local_authority, area = hierarchy

        # A city/town record that merely repeats its parent adds no sub-city
        # search coverage and creates awkward "Reading, Reading, ..." entries.
        if name.casefold() == local_authority.casefold():
            continue
        locations.add(Location(name, local_authority, area))

    return sorted(
        locations,
        key=lambda item: (
            item.area.casefold(),
            item.local_authority.casefold(),
            item.name.casefold(),
            item.text,
        ),
    )


def group_locations(
    locations: Iterable[Location],
) -> DefaultDict[tuple[str, str], list[str]]:
    groups: DefaultDict[tuple[str, str], list[str]] = defaultdict(list)
    for location in locations:
        groups[(location.area, location.local_authority)].append(location.text)
    return groups


def write_locations(output_root: Path, locations: Sequence[Location]) -> int:
    groups = group_locations(locations)
    staging = output_root.with_name(output_root.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for (area, local_authority), values in sorted(
        groups.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold())
    ):
        out_dir = staging / slugify(area) / slugify(local_authority)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "LOCATIONS.js"
        with out_file.open("w", encoding="utf-8") as handle:
            handle.write("LOCATIONS = [\n")
            for index, value in enumerate(values):
                comma = "," if index + 1 < len(values) else ""
                handle.write(f"  {json.dumps(value, ensure_ascii=False)}{comma}\n")
            handle.write("]\n")

    northern_ireland = output_root / "northern-ireland"
    if northern_ireland.is_dir():
        shutil.copytree(
            northern_ireland,
            staging / "northern-ireland",
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    staging.replace(output_root)
    return len(groups)


def download_latest(destination: Path) -> Path:
    request = urllib.request.Request(
        DOWNLOADS_API, headers={"User-Agent": "fixmypresence-address-bot/1.0"}
    )
    with urllib.request.urlopen(request) as response:
        releases = json.load(response)
    if not releases:
        raise RuntimeError("OS Downloads API returned no Open Names CSV release")

    release = releases[0]
    url = release["url"] + ("&redirect" if "?" in release["url"] else "?redirect")
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {release['fileName']} ({release['size']} bytes)…", file=sys.stderr)
    urllib.request.urlretrieve(url, destination)
    return destination


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Existing OS Open Names GB CSV zip; latest release is downloaded if omitted.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parent.parent
            / "out"
            / "locations"
            / "united-kingdom"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report counts without replacing the output directory.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="address-bot-uk-") as tmp:
        source = args.source or download_latest(Path(tmp) / "opname_csv_gb.zip")
        if not source.is_file():
            raise SystemExit(f"OS Open Names source not found: {source}")

        locations = build_locations(rows_from_zip(source))
        if not locations:
            raise SystemExit("No usable populated places found; output was not changed.")

        groups = group_locations(locations)
        print(
            f"Validated {len(locations):,} unique locations across "
            f"{len(groups):,} local-authority files.",
            file=sys.stderr,
        )
        if args.dry_run:
            return 0

        file_count = write_locations(args.output, locations)
        print(f"Generated {file_count:,} files under {args.output}.", file=sys.stderr)
        print(
            "This refreshed Great Britain and preserved any existing "
            "northern-ireland subtree.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

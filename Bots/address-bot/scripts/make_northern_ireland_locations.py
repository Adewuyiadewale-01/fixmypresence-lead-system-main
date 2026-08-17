#!/usr/bin/env python3
"""Generate Northern Ireland LOCATIONS.js files from official boundary data.

OSNI townlands provide the deep search units. Current ONS local-government
district boundaries provide the parent hierarchy:

    out/locations/united-kingdom/northern-ireland/<district>/LOCATIONS.js

Each output value has this shape:

    "Townland, District, Northern Ireland, United Kingdom"

Only the ``northern-ireland`` subtree is replaced. Existing Great Britain
output remains untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, Sequence


OSNI_TOWNLANDS_QUERY_URL = (
    "https://services6.arcgis.com/MmUrOQU5v1he9gfS/arcgis/rest/services/"
    "OSNI_Open_Data_Largescale_Boundaries_Townlands/FeatureServer/0/query"
)
ONS_DISTRICTS_QUERY_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Local_Authority_Districts_DEC_2025_Boundaries_UK_BGC/FeatureServer/0/query"
)
OSNI_SOURCE_PAGE = (
    "https://www.data.gov.uk/dataset/29498c8e-73de-4336-b9a7-d16f31a0ee92/"
    "osni-open-data-largescale-boundaries-townlands7"
)
ONS_SOURCE_PAGE = (
    "https://geoportal.statistics.gov.uk/search?q=BDY_LAD"
    "&sort=Date+Created%7Ccreated%7Cdesc"
)
USER_AGENT = "fixmypresence-address-bot/1.0"
PAGE_SIZE = 2000


@dataclass(frozen=True)
class Townland:
    source_id: int
    name: str
    longitude: float
    latitude: float


@dataclass(frozen=True)
class District:
    code: str
    name: str
    polygons: tuple = field(hash=False, compare=False)
    bounds: tuple[float, float, float, float] = field(hash=False, compare=False)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def display_name(value: object) -> str:
    name = clean(value)
    if name and name == name.upper():
        name = name.title()
    return name


def slugify(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii").lower()
    ascii_only = ascii_only.replace("&", " and ")
    ascii_only = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    return re.sub(r"-+", "-", ascii_only).strip("-") or "item"


def _read_json(url: str, params: dict[str, object]) -> dict:
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(request_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected response from {url}")
    if payload.get("error"):
        raise RuntimeError(f"ArcGIS request failed: {payload['error']}")
    return payload


def fetch_townlands() -> Iterator[Townland]:
    offset = 0
    while True:
        payload = _read_json(
            OSNI_TOWNLANDS_QUERY_URL,
            {
                "where": "1=1",
                "outFields": "FID,TownlandNa",
                "returnGeometry": "false",
                "returnCentroid": "true",
                "outSR": 4326,
                "orderByFields": "FID",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json",
            },
        )
        features = payload.get("features") or []
        for feature in features:
            attributes = feature.get("attributes") or {}
            centroid = feature.get("centroid") or {}
            name = display_name(attributes.get("TownlandNa"))
            if not name or centroid.get("x") is None or centroid.get("y") is None:
                continue
            yield Townland(
                source_id=int(attributes["FID"]),
                name=name,
                longitude=float(centroid["x"]),
                latitude=float(centroid["y"]),
            )
        offset += len(features)
        if not features or not payload.get("exceededTransferLimit"):
            break


def _iter_rings(geometry: dict) -> Iterator[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        yield from coordinates
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            yield from polygon


def _geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    points = [
        point
        for ring in _iter_rings(geometry)
        for point in ring
        if len(point) >= 2
    ]
    if not points:
        raise ValueError("District geometry contains no coordinates")
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def districts_from_geojson(payload: dict) -> list[District]:
    districts: list[District] = []
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        code = clean(properties.get("LAD25CD"))
        name = clean(properties.get("LAD25NM"))
        if not code.startswith("N09") or not name:
            continue
        districts.append(
            District(
                code=code,
                name=name,
                polygons=(geometry,),
                bounds=_geometry_bounds(geometry),
            )
        )
    return sorted(districts, key=lambda district: district.code)


def fetch_districts() -> list[District]:
    payload = _read_json(
        ONS_DISTRICTS_QUERY_URL,
        {
            "where": "LAD25CD like 'N09%'",
            "outFields": "LAD25CD,LAD25NM",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
        },
    )
    districts = districts_from_geojson(payload)
    if len(districts) != 11:
        raise RuntimeError(f"Expected 11 Northern Ireland districts, found {len(districts)}")
    return districts


def _point_to_segment_distance(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    fraction = max(
        0.0,
        min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)),
    )
    return math.hypot(x - (x1 + fraction * dx), y - (y1 + fraction * dy))


def nearest_district_for_point(
    townland: Townland,
    districts_by_code: dict[str, District],
) -> District | None:
    distances: list[tuple[float, District]] = []
    for district in districts_by_code.values():
        closest = math.inf
        for geometry in district.polygons:
            for ring in _iter_rings(geometry):
                if len(ring) < 2:
                    continue
                previous = ring[-1]
                for current in ring:
                    closest = min(
                        closest,
                        _point_to_segment_distance(
                            townland.longitude,
                            townland.latitude,
                            previous[0],
                            previous[1],
                            current[0],
                            current[1],
                        ),
                    )
                    previous = current
        distances.append((closest, district))

    distances.sort(key=lambda item: (item[0], item[1].code))
    if not distances or distances[0][0] > 0.1:
        return None
    return distances[0][1]


def _point_on_segment(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> bool:
    cross = (y - y1) * (x2 - x1) - (x - x1) * (y2 - y1)
    if not math.isclose(cross, 0.0, abs_tol=1e-10):
        return False
    return (
        min(x1, x2) - 1e-10 <= x <= max(x1, x2) + 1e-10
        and min(y1, y2) - 1e-10 <= y <= max(y1, y2) + 1e-10
    )


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            intersect_x = ((x2 - x1) * (y - y1) / (y2 - y1)) + x1
            if x < intersect_x:
                inside = not inside
        previous = current
    return inside


def _point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    for polygon in polygons:
        if not polygon or not _point_in_ring(x, y, polygon[0]):
            continue
        if not any(_point_in_ring(x, y, hole) for hole in polygon[1:]):
            return True
    return False


def assign_townlands(
    townlands: Iterable[Townland],
    districts: Sequence[District],
    *,
    full_extent_fallback=None,
) -> DefaultDict[District, list[Townland]]:
    assigned: DefaultDict[District, list[Townland]] = defaultdict(list)
    unmatched: list[Townland] = []
    districts_by_code = {district.code: district for district in districts}
    for townland in townlands:
        matches = []
        for district in districts:
            min_x, min_y, max_x, max_y = district.bounds
            if not (
                min_x <= townland.longitude <= max_x
                and min_y <= townland.latitude <= max_y
            ):
                continue
            if any(
                _point_in_geometry(
                    townland.longitude,
                    townland.latitude,
                    geometry,
                )
                for geometry in district.polygons
            ):
                matches.append(district)
        if len(matches) == 1:
            assigned[matches[0]].append(townland)
        else:
            fallback_match = (
                full_extent_fallback(townland, districts_by_code)
                if full_extent_fallback is not None
                else None
            )
            if fallback_match is not None:
                assigned[fallback_match].append(townland)
            else:
                unmatched.append(townland)

    if unmatched:
        sample = ", ".join(
            f"{townland.name} ({townland.source_id})"
            for townland in unmatched[:10]
        )
        raise RuntimeError(
            f"{len(unmatched)} townlands did not map to exactly one district: {sample}"
        )
    return assigned


def write_locations(
    output_root: Path,
    assigned: DefaultDict[District, list[Townland]],
) -> dict:
    staging = output_root.with_name(output_root.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    total_locations = 0
    district_counts: dict[str, int] = {}
    for district in sorted(assigned, key=lambda item: item.code):
        unique_values = sorted(
            {
                (
                    f"{townland.name}, {district.name}, "
                    "Northern Ireland, United Kingdom"
                )
                for townland in assigned[district]
                if townland.name.casefold() != district.name.casefold()
            },
            key=str.casefold,
        )
        if not unique_values:
            raise RuntimeError(f"No usable townlands found for {district.name}")
        out_dir = staging / slugify(district.name)
        out_dir.mkdir(parents=True)
        with (out_dir / "LOCATIONS.js").open("w", encoding="utf-8") as handle:
            handle.write("LOCATIONS = [\n")
            for index, value in enumerate(unique_values):
                comma = "," if index + 1 < len(unique_values) else ""
                handle.write(f"  {json.dumps(value, ensure_ascii=False)}{comma}\n")
            handle.write("]\n")
        district_counts[district.name] = len(unique_values)
        total_locations += len(unique_values)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location_unit": "OSNI townland",
        "parent_unit": "ONS local government district",
        "assignment": (
            "Townland centroid within ONS district boundary; offshore or "
            "detached polygons use nearest district boundary within 0.1 degrees"
        ),
        "districts": len(district_counts),
        "locations": total_locations,
        "district_location_counts": district_counts,
        "sources": {
            "townlands": OSNI_SOURCE_PAGE,
            "districts": ONS_SOURCE_PAGE,
        },
    }
    (staging / "SOURCE.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if output_root.exists():
        shutil.rmtree(output_root)
    staging.replace(output_root)
    return metadata


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parent.parent
            / "out"
            / "locations"
            / "united-kingdom"
            / "northern-ireland"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download, map, and validate without replacing output.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    districts = fetch_districts()
    townlands = list(fetch_townlands())
    if not townlands:
        raise SystemExit("OSNI returned no usable townlands; output was not changed.")

    assigned = assign_townlands(
        townlands,
        districts,
        full_extent_fallback=nearest_district_for_point,
    )
    assigned_count = sum(len(values) for values in assigned.values())
    print(
        f"Validated {assigned_count:,} OSNI townlands across "
        f"{len(assigned):,} Northern Ireland districts.",
        file=sys.stderr,
    )
    if args.dry_run:
        return 0

    metadata = write_locations(args.output, assigned)
    print(
        f"Generated {metadata['locations']:,} unique search locations in "
        f"{metadata['districts']} files under {args.output}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

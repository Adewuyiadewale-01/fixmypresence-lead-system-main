# Country Location Scraping Strategies

This document records how Address Bot will collect deep-search location units
for each country. Update the `Actual units scraped` field only after a completed
run has been counted and validated.

## Shared Goal

Produce location strings that Google Maps can understand while providing
coverage below the city level.

Preferred hierarchy:

```text
Small area, municipality or city, region or state, country
```

Example:

```text
Bovenlanden, Aalsmeer, North Holland, Netherlands
```

## Shared Quality Rules

- Prefer official national or regional geographic sources.
- Use named small areas that Google Maps can resolve.
- Preserve the parent municipality, region, and country.
- Remove exact duplicate location combinations.
- Keep alternate-language names only when they improve coverage.
- Exclude identifiers that have no searchable place name.
- Record the source dataset version or publication date.
- Keep estimated totals separate from actual scraped totals.
- Validate a sample of generated locations in Google Maps before use.

---

## Netherlands

### Status

Scraped. The current output was generated from a historical CBS dataset and
should eventually be refreshed with the newest suitable CBS source.

### Primary Sources

- CBS StatLine `WijkenEnBuurten` dataset `84583NED`
- Dutch Wikipedia municipality list for municipality-to-province mapping
- Manual province mappings for historic municipalities missing from the
  current municipality list

### Scrape Strategy

1. Download the nationwide CBS neighbourhood dataset.
2. Read records with keys beginning with `GM` as municipalities.
3. Read records with keys beginning with `BU` as neighbourhoods.
4. Connect every neighbourhood to its municipality using the CBS municipality
   code.
5. Map each municipality to a province.
6. Apply reviewed overrides for historic municipalities.
7. Deduplicate and alphabetically sort neighbourhood names per municipality.
8. Generate one `LOCATIONS.js` file per municipality.

### Location Unit Breakdown

```text
Neighbourhood -> Municipality -> Province -> Netherlands
```

Example:

```text
Bovenlanden, Aalsmeer, North Holland, Netherlands
```

### Output Layout

```text
out/locations/netherlands/<province>/<municipality>/LOCATIONS.js
```

### Filtering Notes

- CBS neighbourhood names are retained even when they represent industrial,
  harbour, park, or sparsely populated areas.
- A future quality pass may classify these units without deleting them from
  the raw source output.
- Historic municipalities in the 2019 dataset require manual mapping.

### Unit Counts

- Estimated units before scraping: Not recorded
- Actual units scraped: **13,553**
- Municipality files generated: **355**
- Administrative regions represented: **12 provinces**
- Count verified on: **June 18, 2026**

---

## United Kingdom

### Status

Generated for all four UK nations. Great Britain was generated from the April
2026 OS Open Names release. Northern Ireland was generated from official OSNI
large-scale townlands and current ONS local-government district boundaries.

### Primary Sources

- Ordnance Survey OS Open Names for Great Britain
- ONS Index of Place Names as a supplementary hierarchy and coverage source
- OSNI large-scale townland boundaries for Northern Ireland
- ONS December 2025 local-government-district boundaries for the Northern
  Ireland parent hierarchy

### Scrape Strategy

1. Download the latest OS Open Names release from the public OS Downloads API.
2. Select useful named populated-place records, initially:
   - City
   - Town
   - Village
   - Hamlet
   - Suburban Area
   - Other Settlement
3. Exclude roads, postcodes, natural features, and unrelated named objects
   from the neighbourhood-level output.
4. Use available administrative fields and ONS lookups to attach each place
   to its district, borough or local authority and its nation or region.
5. Deduplicate using the full location hierarchy rather than the small-area
   name alone, because UK place names frequently repeat.
6. Preserve official Welsh, Scots, or Gaelic names where they improve search
   coverage, without generating duplicate searches for identical places.
7. Process Northern Ireland townlands through OSNI data, assign each townland
   centroid to its current ONS local government district, and normalize it
   into the same output structure. For the four offshore or detached polygons
   outside the land-only district geometry, use the nearest official district
   boundary within a strict 0.1-degree limit.
8. Validate samples from England, Scotland, Wales, and Northern Ireland in
   Google Maps.

### Location Unit Breakdown

Preferred:

```text
Neighbourhood or locality -> Borough/district/local authority -> Region or nation -> United Kingdom
```

Examples:

```text
Mayfair, City of Westminster, Greater London, United Kingdom
Leith, Edinburgh, Scotland, United Kingdom
Ballymurphy, Belfast, Northern Ireland, United Kingdom
```

The available hierarchy will differ among England, Scotland, Wales, and
Northern Ireland. Coverage is more important than forcing every nation into an
identical administrative vocabulary.

### Proposed Output Layout

```text
out/locations/united-kingdom/<area>/<local-authority>/LOCATIONS.js
out/locations/united-kingdom/northern-ireland/<district>/LOCATIONS.js
```

### Filtering Notes

- The ONS Index of Place Names contains more than 100,000 entries, but that is
  not the expected usable search count.
- Alternative names, duplicate names, administrative entries, and unsuitable
  feature types must be filtered.
- Repeated townland names inside the same district collapse to one identical
  Google Maps query because the source has no lower parent field that would
  distinguish those duplicate names.

### Unit Counts

- Actual Great Britain units scraped: **42,264**
- Great Britain local-authority files generated: **350**
- OSNI townland polygons validated: **9,520**
- Northern Ireland unique search units scraped: **8,831**
- Northern Ireland district files generated: **11**
- Total UK unique search units: **51,095**
- Total UK authority/district files: **361**
- Count verified on: **June 22, 2026**

---

## New Country Template

Copy this section when adding another country.

### Country

`COUNTRY NAME`

### Status

`Researching / Planned / Scraping / Scraped / Validated`

### Primary Sources

- `Official source and dataset`
- `Supplementary hierarchy source`

### Scrape Strategy

1. `Download or query source data.`
2. `Identify the smallest useful named location unit.`
3. `Connect it to parent administrative areas.`
4. `Normalize names and hierarchy.`
5. `Deduplicate full location combinations.`
6. `Validate representative samples in Google Maps.`

### Location Unit Breakdown

```text
Small area -> Municipality/city -> Region/state -> Country
```

### Output Layout

```text
out/locations/<country>/<region>/<municipality>/LOCATIONS.js
```

### Filtering Notes

- `Document included and excluded unit types.`
- `Document language and alternate-name handling.`
- `Document known coverage gaps.`

### Unit Counts

- Estimated units before scraping: **PENDING**
- Actual units scraped: **PENDING**
- Output files generated: **PENDING**
- Administrative regions represented: **PENDING**
- Count verified on: **PENDING**

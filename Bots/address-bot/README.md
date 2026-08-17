# address-bot

Generate Google Maps search locations below city level.

## Netherlands

The existing Netherlands exporter reads CBS neighbourhoods:

```bash
python3 scripts/make_amsterdam_locations.py
```

## United Kingdom

The Great Britain exporter downloads the latest OS Open Names CSV release:

```bash
python3 scripts/make_uk_locations.py
```

The Northern Ireland exporter downloads official OSNI townlands and assigns
them to the 11 current ONS local-government districts:

```bash
python3 scripts/make_northern_ireland_locations.py
```

Preview counts without changing output:

```bash
python3 scripts/make_uk_locations.py --dry-run
python3 scripts/make_northern_ireland_locations.py --dry-run
```

Reuse an already downloaded release:

```bash
python3 scripts/make_uk_locations.py \
  --source /path/to/opname_csv_gb.zip
```

Output shape:

```text
out/locations/united-kingdom/<area>/<local-authority>/LOCATIONS.js
out/locations/united-kingdom/northern-ireland/<district>/LOCATIONS.js
```

Example:

```text
Mayfair, City of Westminster, Greater London, United Kingdom
```

The Great Britain exporter preserves the existing `northern-ireland` subtree.
Run both exporters when rebuilding complete UK coverage.

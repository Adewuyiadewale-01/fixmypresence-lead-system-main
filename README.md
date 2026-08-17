# FixMyPresence Lead System

An operator-controlled Python system for discovering business leads from Google Maps, enriching company and contact data from public websites, routing results through Google Sheets, and monitoring the pipeline from a FastAPI dashboard.

## Repository layout

- `Bots/maps_scraper/` — parallel Google Maps collection, task scheduling, deduplication, and delivery.
- `Bots/enricher/` — website and LinkedIn-oriented enrichment workflows.
- `Bots/common/` — shared Google Sheets, validation, and configuration helpers.
- `controller/` — long-running pipeline coordination.
- `dashboard/` — FastAPI control and observability interface.
- `deploy/contabo/` — VM deployment and operational documentation.

`Bots/address-bot` is first-party source for generating and maintaining the location datasets consumed by the Maps scraper.

## Local setup

Use Python 3.11 or later. Keep credentials, browser profiles, operational state, and collected lead data outside Git.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -v
```

The dashboard is intentionally bound to `127.0.0.1` by default. See [the deployment guide](deploy/contabo/README.md) before running it on a VM.

## Repository hygiene

- Never commit API keys, webhook tokens, service-account files, browser sessions, or exported lead/contact data.
- Runtime outputs are intentionally ignored. Commit redacted test fixtures instead when a test needs sample data.
- Run the test suite before committing. The GitHub workflow uses the same command.

## Status

This repository contains an operational automation system. Deployment configuration and live integration settings are machine-specific and must remain outside version control.

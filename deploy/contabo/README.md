# Contabo VM Deployment

For day-to-day use after deployment, see [OPERATOR_MANUAL.md](./OPERATOR_MANUAL.md).

This project deploys as one Python application that includes:

- dashboard backend: `dashboard/`
- dashboard frontend assets: `dashboard/templates/` and `dashboard/static/`
- controller / watchers: `controller/`
- scraper and enrichment workers: `Bots/`
- helper scripts: `scripts/`

Do **not** deploy your local `.venv`, logs, browser profiles, lock files, or secrets.
If you want the VM to continue from the local run state, use the explicit resume-state bundle step below.

## 1. Copy the code to the VM

Recommended path on the VM:

```bash
mkdir -p /opt/fixmypresence
```

From your local machine, sync the repo while excluding local runtime junk:

```bash
rsync -av --delete \
  --exclude-from=deploy/contabo/rsync-exclude.txt \
  /Users/tonyisbuiding/Desktop/fixmypresence-lead-system-main/ \
  root@86.48.1.96:/opt/fixmypresence/
```

## 1b. Optional: copy local resume state to the VM

Use this when the VM should continue from the current local scraper/enrichment state instead of starting fresh.

Create a local state bundle:

```bash
cd /Users/tonyisbuiding/Desktop/fixmypresence-lead-system-main
bash scripts/create_resume_state_bundle.sh
```

Copy the printed bundle to the VM:

```bash
rsync -av deploy/resume-state/fixmypresence-resume-state-*.tar.gz \
  root@86.48.1.96:/opt/fixmypresence/deploy/resume-state/
```

Apply it on the VM while the dashboard service is stopped:

```bash
ssh root@86.48.1.96
systemctl stop fixmypresence-dashboard || true
cd /opt/fixmypresence
latest_bundle="$(ls -t deploy/resume-state/fixmypresence-resume-state-*.tar.gz | head -1)"
bash scripts/apply_resume_state_bundle.sh "$latest_bundle" /opt/fixmypresence
```

The apply script backs up existing VM state before extraction and removes stale runtime locks.

## 2. Bootstrap the VM environment

On the VM:

```bash
cd /opt/fixmypresence
chmod +x scripts/vm_bootstrap.sh scripts/run_dashboard_server.sh
./scripts/vm_bootstrap.sh
```

That script will:

- install Python tooling
- create `.venv`
- install Python dependencies
- install Playwright Chromium + OS dependencies
- create `logs/`, `controller/logs/`, and `secrets/`

## 3. Install Google credentials

Place the service account JSON on the VM at:

```bash
/opt/fixmypresence/secrets/google-service-account.json
```

Make it readable only by root:

```bash
chmod 600 /opt/fixmypresence/secrets/google-service-account.json
```

## 4. Create the dashboard environment file

```bash
mkdir -p /etc/fixmypresence
cp /opt/fixmypresence/deploy/contabo/dashboard.env.example /etc/fixmypresence/dashboard.env
```

Adjust values as needed, especially:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `DASHBOARD_HOST`
- `DASHBOARD_PORT`
- `DASHBOARD_USE_XVFB`
- `CYCLE_RUN_MINUTES`
- `CYCLE_REST_MINUTES`

For the current dashboard/controller wiring, the Maps scraper launches in headed mode by default.
On a headless VM, keep `DASHBOARD_USE_XVFB=1` so the dashboard service runs inside a virtual display
and the scraper/controller children inherit a valid `DISPLAY`.

## 5. Install and start the systemd service

```bash
cp /opt/fixmypresence/deploy/contabo/fixmypresence-dashboard.service /etc/systemd/system/fixmypresence-dashboard.service
systemctl daemon-reload
systemctl enable --now fixmypresence-dashboard
systemctl status fixmypresence-dashboard
```

## 6. Check the dashboard

If you bind to `127.0.0.1`, reverse-proxy it through nginx/Caddy or SSH-tunnel to it.

If you bind to `0.0.0.0`, open:

```text
http://86.48.1.96:8000
```

## Useful commands

```bash
journalctl -u fixmypresence-dashboard -f
tail -f /opt/fixmypresence/logs/dashboard_server.log
cd /opt/fixmypresence && ./scripts/dashboard_stop.sh --force
cd /opt/fixmypresence && ./scripts/dashboard_start.sh
cd /opt/fixmypresence && ./.venv/bin/python controller/clean_pipeline.py --all
```

## Notes

- Apps Script webhooks are already remote and do not need to be deployed on the VM.
- The VM must have outbound internet access for Google auth, webhooks, and scraping.
- Runtime files and logs will be recreated automatically after deployment.

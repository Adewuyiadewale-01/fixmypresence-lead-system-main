# FixMyPresence Operator Manual

This manual is for day-to-day operation of the Contabo VM deployment.

It covers:

- how to start and stop the dashboard
- how to run the scraper safely
- what the main dashboard pages are for
- what to do when something pauses, times out, or looks stuck

## 1. Core facts

- The dashboard is hosted by the `fixmypresence-dashboard` systemd service.
- The dashboard service also launches the controller and watcher subprocesses.
- The Maps scraper runs in headed mode by default, but on the VM it uses `xvfb`, so you will not see browser windows on your laptop.
- Runtime state lives inside the repo on the VM, especially under `Bots/maps_scraper/output/` and `logs/`.

## 2. Main access points

- Dashboard URL:
  - `http://5.189.162.59:8000`
- SSH into the VM:
  - `ssh root@5.189.162.59`
- Current deployed service name:
  - `fixmypresence-dashboard`

## 3. Dashboard pages

### Overview

Use this page for the main operational controls.

- `Start Manual`: starts the controller for a single run cycle
- `Start Cycle`: starts the scheduler-driven run/rest cycle
- `Stop`: asks the controller/scheduler to stop gracefully
- `Kill`: hard-stops the running controller process
- `Restart Dashboard`: restarts the dashboard service
- `Shutdown Dashboard`: stops the dashboard service

### Scraper

Use this page to monitor the Maps scraper in detail.

- current category
- current location / keyword
- searches completed
- leads collected
- batch callouts
- aggression / auto-scaler note
- pipeline sheet capacity

### Robot

Use this page for automation posture and category progression.

- review batch warnings and critical signals
- approve the next scraper category manually
- watch category auto-progress timing
- review subsystem mesh state

### Enrichment

Use this page to monitor LinkedIn enrichment, keyword detection, scoring, and scraper_v3 watcher state.

### Config

Use this page to change runtime behavior without editing files manually.

Important sections:

- `Maps Scraper`
  - worker count
  - batch size
  - category advance timeout
  - delivery retry cooldown
- `Enrichment`
  - LinkedIn forced-drain threshold and max wait
  - keyword / scoring thresholds
  - scraper_v3 watcher thresholds
- `Google Sheets`
  - pipeline and delivery routing

### Logs

Use this page when you need the fastest read on what just happened.

## 4. Terminal commands you will actually use

### Check dashboard status

```bash
systemctl status fixmypresence-dashboard
```

### Start dashboard

```bash
systemctl start fixmypresence-dashboard
```

### Stop dashboard

```bash
systemctl stop fixmypresence-dashboard
```

### Restart dashboard

```bash
systemctl restart fixmypresence-dashboard
```

### Follow dashboard logs live

```bash
journalctl -u fixmypresence-dashboard -f
```

### Read the application log file

```bash
tail -f /opt/fixmypresence-lead-system/logs/dashboard_server.log
```

## 5. Normal operating flow

### Starting the day

1. Open the dashboard.
2. Confirm the Overview page loads.
3. Check that the pipeline sheets and watcher cards look healthy.
4. Start the scraper from `Overview` if it is not already running.
5. Open `Scraper` or `Robot` to watch the first batch and category progression.

### Deploying local resume state

Use this only when the VM should continue from the current local scraper/enrichment state.

Local machine:

```bash
cd /Users/tonyisbuiding/Desktop/fixmypresence-lead-system-main
bash scripts/create_resume_state_bundle.sh
rsync -av deploy/resume-state/fixmypresence-resume-state-*.tar.gz \
  root@5.189.162.59:/opt/fixmypresence-lead-system/deploy/resume-state/
```

VM:

```bash
systemctl stop fixmypresence-dashboard || true
cd /opt/fixmypresence-lead-system
latest_bundle="$(ls -t deploy/resume-state/fixmypresence-resume-state-*.tar.gz | head -1)"
bash scripts/apply_resume_state_bundle.sh "$latest_bundle" /opt/fixmypresence-lead-system
systemctl restart fixmypresence-dashboard
```

The resume bundle includes scraper progress, pending batches, dedupe/output files, enrichment checkpoints/results, controller watcher state, and dashboard-managed config. It does not include browser profiles, locks, logs, `.venv`, or secrets.

### While the scraper is running

- Use `Scraper` for detailed run health.
- Use `Robot` for category approvals and decision feed review.
- Use `Enrichment` to confirm downstream backlog is moving.

### Ending the day

- If you want to stop only the current run, use `Stop`.
- If you want the whole dashboard offline, use terminal:

```bash
systemctl stop fixmypresence-dashboard
```

## 6. Category progression

The scraper processes one keyword category at a time.

When a category completes, one of these happens:

- you approve the next category on the `Robot` page
- the category auto-progress timeout is reached

The timeout is configurable in:

- `Config` -> `Maps Scraper` -> `Category Advance Timeout (minutes)`

## 7. Delivery timeout behavior

If Google / Apps Script delivery is slow, the scraper may pause to protect the buffered leads.

What happens now:

- the undelivered batch is preserved
- the controller waits for the configured cooldown
- the controller auto-retries after the cooldown

The cooldown is configurable in:

- `Config` -> `Maps Scraper` -> `Delivery Retry Cooldown (minutes)`

Default:

- `15` minutes

## 8. LinkedIn forced drain behavior

The LinkedIn watcher drains when:

- backlog reaches the configured threshold
- or the forced-drain timeout is reached

You can change this in:

- `Config` -> `Enrichment` -> `LinkedIn Enrichment`

Key settings:

- drain threshold
- max wait before forced drain
- watcher interval

## 9. Common situations

### The dashboard page reloads after I shut it down from the UI

Cause:

- the deployment uses systemd
- if systemd restarts the service, the page can come back

Best control method:

```bash
systemctl stop fixmypresence-dashboard
```

### SSH says `Broken pipe` or `Connection reset by peer`

Cause:

- your SSH session dropped

Fix:

- reconnect with `ssh root@86.48.1.96`
- make sure your prompt is the VM prompt before running Linux commands

### I do not see browser windows during headed scraping

This is expected on the VM.

- the scraper runs inside `xvfb`
- browser windows are not visible on your laptop

### The scraper stopped after a Google timeout

Look for messages like:

- `Google Sync Post Failed`
- `Delivery failed. Stopping to preserve buffered leads.`

Meaning:

- the scraper protected the batch instead of losing it
- the controller should now retry after the delivery cooldown

### Category approval says controller already running

Meaning:

- the controller subprocess is still active

Wait for the current controller action to end, then approve again.

## 10. Logs and runtime files

Important places on the VM:

- dashboard log:
  - `/opt/fixmypresence/logs/dashboard_server.log`
- controller logs:
  - `/opt/fixmypresence/controller/logs/`
- scraper output/state:
  - `/opt/fixmypresence/Bots/maps_scraper/output/`

Useful scraper state files:

- `completed_tasks.jsonl`
- `pending_batch.json`
- `category_state.json`
- `scraper_health.json`
- `scraper_stats.json`

## 11. After code changes

When local code changes need to reach the VM:

1. sync the repo with `rsync`
2. restart the dashboard service

Typical command sequence:

```bash
rsync -av --delete \
  --exclude-from=deploy/contabo/rsync-exclude.txt \
  /path/to/fixmypresence-lead-system-main/ \
  root@86.48.1.96:/opt/fixmypresence/
```

Then on the VM:

```bash
systemctl restart fixmypresence-dashboard
systemctl status fixmypresence-dashboard
```

## 12. Safe recovery checklist

If something looks wrong:

1. Check `Overview`
2. Check `Logs`
3. Run:

```bash
systemctl status fixmypresence-dashboard
journalctl -u fixmypresence-dashboard -n 100 --no-pager
```

4. If needed, restart the dashboard:

```bash
systemctl restart fixmypresence-dashboard
```

5. If the issue is scraper-only, review `Robot`, `Scraper`, and the files in `Bots/maps_scraper/output/` before clearing anything

## 13. Do not do these casually

- do not delete scraper output/state files unless you intend to reset progress
- do not use `kill -9` first unless graceful stop has failed
- do not assume a dropped SSH session means the dashboard died
- do not expect visible browser windows from the VM headed setup

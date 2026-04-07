/**
 * Scraper Page - Controls, progress, logs
 * Depends on common.js (api, showToast, confirmAction, formatTimestamp)
 */

// =============================================================================
// UI UPDATES (from status-update event)
// =============================================================================

function updateButtonStates(running) {
    document.getElementById('btn-start-manual').disabled = running;
    document.getElementById('btn-start-cycle').disabled = running;
    document.getElementById('btn-stop').disabled = !running;
    document.getElementById('btn-kill').disabled = !running;
    document.getElementById('btn-clean').disabled = running;

    [document.getElementById('btn-start-manual'), document.getElementById('btn-start-cycle')].forEach(btn => {
        btn.classList.toggle('btn-disabled', running);
    });
    [document.getElementById('btn-stop'), document.getElementById('btn-kill')].forEach(btn => {
        btn.classList.toggle('btn-disabled', !running);
    });
}

function updateProcessInfo(process) {
    document.getElementById('process-pid').textContent = process.pid || '--';
    document.getElementById('process-uptime').textContent = process.uptime || '--';
}

function fmtNum(value) {
    if (value === null || value === undefined || value === '') return '--';
    if (typeof value === 'number') return value.toLocaleString();
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value);
}

function fmtOneDecimal(value, suffix = '') {
    if (value === null || value === undefined || value === '') return '--';
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return '--';
    return `${parsed.toFixed(1)}${suffix}`;
}

function fmtAgo(isoString) {
    if (!isoString) return '--';
    const diffMs = Date.now() - new Date(isoString).getTime();
    if (!Number.isFinite(diffMs) || diffMs < 0) return formatTimestamp(isoString);
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function setChip(el, text, tone = 'info') {
    if (!el) return;
    el.textContent = text;
    el.classList.remove('attn-info', 'attn-warning', 'attn-danger');
    el.classList.add(
        tone === 'danger' ? 'attn-danger' :
        tone === 'warning' ? 'attn-warning' :
        'attn-info'
    );
}

function updateSchedulerInfo(scheduler) {
    const phaseEl = document.getElementById('cycle-phase');
    const nextEl = document.getElementById('cycle-next');

    if (scheduler.enabled) {
        phaseEl.textContent = scheduler.phase === 'RUNNING' ? 'RUNNING' : 'RESTING';
        phaseEl.classList.add('cycle-enabled');
    } else {
        phaseEl.textContent = 'DISABLED';
        phaseEl.classList.remove('cycle-enabled');
    }

    if (scheduler.next_transition) {
        const diffMs = new Date(scheduler.next_transition) - new Date();
        if (diffMs > 0) {
            const h = Math.floor(diffMs / (1000 * 60 * 60));
            const m = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
            nextEl.textContent = `in ${h}h ${m}m`;
        } else {
            nextEl.textContent = 'Now';
        }
    } else {
        nextEl.textContent = '--';
    }

    if (scheduler.config) {
        document.getElementById('run-hours').value = scheduler.config.run_hours || 4;
        document.getElementById('rest-hours').value = scheduler.config.rest_hours || 2;
    }
}

function updateScraperProgress(scraper, processRunning, mapsScraper) {
    const statusEl = document.getElementById('scraper-status');
    const chipEl = document.getElementById('scraper-progress-chip');
    const summaryEl = document.getElementById('scraper-progress-summary');

    if (!scraper || scraper.error) {
        statusEl.textContent = 'No data';
        statusEl.classList.add('status-error');
        setChip(chipEl, 'No live scraper data', 'warning');
        summaryEl.textContent = 'The scraper progress file is unavailable right now.';
        return;
    }
    statusEl.classList.remove('status-error');

    let statusText = 'Unknown';
    if (!processRunning && scraper.has_progress) {
        statusText = 'PAUSED (Last snapshot)';
        statusEl.classList.add('status-paused');
    } else if (!scraper.has_progress) {
        statusText = 'Fresh Start';
        statusEl.classList.remove('status-paused');
    } else {
        const statusMap = {
            'in_progress': 'In Progress',
            'category_complete': 'Category Complete',
            'all_complete': 'All Complete',
            'fresh': 'Fresh Start'
        };
        statusText = statusMap[scraper.status] || scraper.status || 'unknown';
        statusEl.classList.remove('status-paused');
    }
    statusEl.textContent = statusText;

    const progress = scraper.progress || {};
    const cat = progress.category || {};
    const locations = progress.locations || {};
    const resume = progress.resume_cursor || {};
    const overall = progress.overall || {};
    const current = progress.current_location || {};

    document.getElementById('scraper-category').textContent =
        cat.index !== undefined
            ? `${cat.index + 1}/${cat.total || '?'}${cat.progress_pct !== undefined ? ` · ${fmtOneDecimal(cat.progress_pct, '%')}` : ''}`
            : '--';

    document.getElementById('scraper-location').textContent = current.name || '--';
    document.getElementById('scraper-keyword').textContent = current.keyword_name || '--';
    document.getElementById('scraper-searches').textContent =
        overall.completed_searches !== undefined
            ? `${fmtNum(overall.completed_searches)}/${fmtNum(overall.total_searches || '?')}${overall.progress_pct !== undefined ? ` · ${fmtOneDecimal(overall.progress_pct, '%')}` : ''}`
            : '--';
    document.getElementById('scraper-leads').textContent = fmtNum(overall.total_leads_collected);
    document.getElementById('scraper-batches').textContent = fmtNum(overall.batch_number);
    document.getElementById('scraper-search-pace').textContent =
        mapsScraper && mapsScraper.searches_per_hour !== null && mapsScraper.searches_per_hour !== undefined
            ? `${fmtOneDecimal(mapsScraper.searches_per_hour)}/hr`
            : '--';

    const lastSaved = progress.last_saved || '--';
    document.getElementById('scraper-last-saved').textContent =
        lastSaved !== '--' ? formatTimestamp(lastSaved) : '--';
    document.getElementById('scraper-last-activity').textContent =
        mapsScraper && mapsScraper.last_activity ? `${formatTimestamp(mapsScraper.last_activity)} (${fmtAgo(mapsScraper.last_activity)})` : '--';

    document.getElementById('scraper-resume-cursor').textContent =
        resume.category_complete
            ? 'Ready for next category'
            : (resume.keyword_name && resume.location_name)
                ? `${resume.keyword_name} @ ${resume.location_name}`
                : '--';

    document.getElementById('scraper-locations').textContent =
        locations.total !== undefined
            ? `${fmtNum(locations.completed || 0)}/${fmtNum(locations.total)} complete · ${fmtNum(locations.partial || 0)} partial`
            : '--';

    const progressChipTone =
        mapsScraper && mapsScraper.current_phase === 'paused_critical'
            ? 'danger'
            : (!processRunning && scraper.has_progress ? 'warning' : 'info');
    setChip(chipEl, statusText, progressChipTone);

    const summaryParts = [];
    if (scraper.summary) summaryParts.push(scraper.summary);
    if (mapsScraper && mapsScraper.searches_per_hour) summaryParts.push(`${fmtOneDecimal(mapsScraper.searches_per_hour)}/hr current pace`);
    if (mapsScraper && mapsScraper.run && mapsScraper.run.leads_per_100_searches !== null && mapsScraper.run.leads_per_100_searches !== undefined) {
        summaryParts.push(`${fmtOneDecimal(mapsScraper.run.leads_per_100_searches)} leads per 100 searches this run`);
    }
    summaryEl.textContent = summaryParts.length ? summaryParts.join(' · ') : 'No scraper summary available yet.';
}

function getAggressionReason(scraper) {
    if (!scraper) return 'No aggression guidance available yet.';
    const run = scraper.run || {};
    const captcha = Number(run.captcha_count || 0);
    const errors = Number(run.error_count || 0);
    const websiteRate = Number(run.website_found_rate || 0);
    const yieldRate = Number(run.leads_per_100_searches || 0);

    if (captcha > 0) return 'Captcha pressure is showing up in the current run, so it is safer to back off before increasing load.';
    if (errors >= 5) return 'Worker instability is rising enough to inspect the scraper before pushing harder.';
    if (scraper.current_phase === 'paused_critical') return 'A critical signal was confirmed across batches, so the scraper paused itself for review.';
    if (websiteRate >= 75 && yieldRate >= 220) return 'Yield and website quality are both strong, so the current profile looks safe to push modestly if needed.';
    if (websiteRate > 0 && websiteRate < 55) return 'Too many scraped rows are landing without a usable website, which usually means the search mix is getting noisy.';
    if (yieldRate > 0 && yieldRate < 120) return 'The current keyword/location mix is underproducing, so higher aggression is unlikely to help right now.';
    return 'The current run profile looks healthy enough to leave alone unless you need more throughput urgently.';
}

function updateScrapeHealth(scraper) {
    if (!scraper) return;
    const run = scraper.run || {};
    const allTime = scraper.all_time || {};
    const deliverySheet = scraper.delivery_sheet || {};

    document.getElementById('scraper-run-yield').textContent =
        run.leads_per_100_searches !== null && run.leads_per_100_searches !== undefined
            ? fmtOneDecimal(run.leads_per_100_searches)
            : '--';
    document.getElementById('scraper-run-website-rate').textContent =
        run.website_found_rate !== null && run.website_found_rate !== undefined
            ? `${fmtOneDecimal(run.website_found_rate, '%')}`
            : '--';
    document.getElementById('scraper-run-website-rate-sub').textContent =
        run.website_found !== null && run.website_found !== undefined
            ? `${fmtNum(run.website_found)} of ${fmtNum(run.website_total || 0)} delivered rows`
            : 'current run website rate';
    document.getElementById('scraper-run-batch-yield').textContent =
        run.leads_per_batch !== null && run.leads_per_batch !== undefined
            ? fmtOneDecimal(run.leads_per_batch)
            : '--';
    document.getElementById('scraper-all-yield').textContent =
        allTime.leads_per_100_searches !== null && allTime.leads_per_100_searches !== undefined
            ? fmtOneDecimal(allTime.leads_per_100_searches)
            : '--';
    document.getElementById('scraper-all-website-rate').textContent =
        allTime.website_found_rate !== null && allTime.website_found_rate !== undefined
            ? `${fmtOneDecimal(allTime.website_found_rate, '%')}`
            : '--';
    document.getElementById('scraper-all-website-rate-sub').textContent =
        allTime.website_found !== null && allTime.website_found !== undefined
            ? `${fmtNum(allTime.website_found)} of ${fmtNum(allTime.website_total || 0)} historical rows`
            : 'historical website rate';
    document.getElementById('scraper-all-batch-yield').textContent =
        allTime.leads_per_batch !== null && allTime.leads_per_batch !== undefined
            ? fmtOneDecimal(allTime.leads_per_batch)
            : '--';

    const runCaptchas = Number(run.captcha_count || 0);
    const runErrors = Number(run.error_count || 0);
    document.getElementById('scraper-run-risk').textContent = `${fmtNum(runCaptchas)} / ${fmtNum(runErrors)}`;
    if (run.searches > 0 && (runCaptchas > 0 || runErrors > 0)) {
        const eventRate = ((runCaptchas + runErrors) / run.searches) * 100;
        document.getElementById('scraper-run-risk-sub').textContent = `${fmtOneDecimal(eventRate, '%')} of this run's searches`;
    } else {
        document.getElementById('scraper-run-risk-sub').textContent = 'current run risk signals';
    }

    const allCaptchas = allTime.captcha_count;
    const allErrors = allTime.error_count;
    document.getElementById('scraper-all-risk').textContent =
        allCaptchas != null || allErrors != null
            ? `${fmtNum(allCaptchas)} / ${fmtNum(allErrors)}`
            : '--';
    document.getElementById('scraper-all-risk-sub').textContent =
        allTime.captcha_rate != null || allTime.error_rate != null
            ? `${fmtOneDecimal(allTime.captcha_rate || 0, '%')} captcha · ${fmtOneDecimal(allTime.error_rate || 0, '%')} error`
            : 'historical risk since instrumentation';

    const healthTone = scraper.current_phase === 'paused_critical'
        ? 'danger'
        : (runCaptchas > 0 || runErrors >= 5 ? 'warning' : 'info');
    setChip(document.getElementById('scraper-health-chip'), scraper.recommendation || 'Learning baseline', healthTone);

    const healthNotes = [];
    if (run.website_found_rate !== null && run.website_found_rate !== undefined) {
        healthNotes.push(`This run website-found rate is ${fmtOneDecimal(run.website_found_rate, '%')}`);
    }
    if (run.leads_per_batch !== null && run.leads_per_batch !== undefined) {
        healthNotes.push(`This run average batch yield is ${fmtOneDecimal(run.leads_per_batch)} leads`);
    }
    if (runCaptchas > 0 || runErrors > 0) {
        healthNotes.push(`${fmtNum(runCaptchas)} captchas and ${fmtNum(runErrors)} worker errors seen this run`);
    } else {
        healthNotes.push('No current captcha or worker error pressure detected');
    }
    document.getElementById('scraper-health-note').textContent = healthNotes.join(' · ');

    const labelEl = document.getElementById('scraper-sheet-label');
    const subEl = document.getElementById('scraper-sheet-sub');
    const metaEl = document.getElementById('scraper-sheet-meta');
    const fillEl = document.getElementById('scraper-sheet-fill');
    const rowsEl = document.getElementById('scraper-sheet-rows');
    const noteEl = document.getElementById('scraper-sheet-note');
    const chipEl = document.getElementById('scraper-sheet-chip');
    if (labelEl && subEl && metaEl && fillEl && rowsEl && noteEl && chipEl) {
        const label = deliverySheet.sheet_label || 'Pipeline sheet';
        const tab = deliverySheet.tab_name || 'Leads';
        const currentRows = Number(deliverySheet.current_rows || 0);
        const stop = Number(deliverySheet.stop_row_threshold || 0);
        const warning = Number(deliverySheet.warning_row_threshold || 0);
        const pct = stop > 0 ? Math.max(0, Math.min((currentRows / stop) * 100, 100)) : 0;

        labelEl.textContent = label;
        subEl.textContent = tab;
        metaEl.textContent = stop > 0 ? `${fmtNum(currentRows)} / ${fmtNum(stop)}` : fmtNum(currentRows);
        rowsEl.textContent = deliverySheet.data_rows != null ? `${fmtNum(deliverySheet.data_rows)} data rows` : 'Row count unavailable';
        fillEl.style.width = `${pct}%`;
        fillEl.classList.remove('is-warning', 'is-danger');

        if (deliverySheet.blocked) {
            fillEl.classList.add('is-danger');
            setChip(chipEl, 'Cap reached', 'danger');
            noteEl.textContent = 'Stop threshold reached. Add and activate a new pipeline sheet before resuming.';
        } else if (deliverySheet.warning || (warning > 0 && currentRows >= warning)) {
            fillEl.classList.add('is-warning');
            setChip(chipEl, 'Approaching cap', 'warning');
            noteEl.textContent = deliverySheet.rows_remaining_to_stop != null
                ? `${fmtNum(deliverySheet.rows_remaining_to_stop)} rows remain before the stop threshold`
                : 'Approaching the configured row cap';
        } else if (stop > 0) {
            setChip(chipEl, 'Healthy capacity', 'info');
            noteEl.textContent = `${fmtNum(Math.max(stop - currentRows, 0))} rows remain before the stop threshold`;
        } else {
            setChip(chipEl, 'Monitoring', 'info');
            noteEl.textContent = 'Monitoring pipeline sheet capacity';
        }
    }
}

function updateAggression(scraper) {
    if (!scraper) return;
    document.getElementById('scraper-workers').textContent = fmtNum(scraper.worker_count);
    document.getElementById('scraper-batch-size').textContent = fmtNum(scraper.batch_size);
    document.getElementById('scraper-website-workers').textContent = fmtNum(scraper.website_status_workers);
    document.getElementById('scraper-recommendation').textContent = scraper.recommendation || '--';
    setChip(
        document.getElementById('scraper-aggression-chip'),
        scraper.recommendation || 'Learning baseline',
        scraper.recommendation_tone || 'info'
    );
    document.getElementById('scraper-aggression-note').textContent = getAggressionReason(scraper);
}

function renderCallouts(scraper) {
    const listEl = document.getElementById('scraper-callouts-list');
    const chipEl = document.getElementById('scraper-callouts-chip');
    if (!listEl || !chipEl) return;

    const callouts = (scraper && Array.isArray(scraper.recent_callouts)) ? scraper.recent_callouts : [];
    if (!callouts.length) {
        setChip(chipEl, 'No active callouts', 'info');
        listEl.innerHTML = '<div class="attention-empty">No scraper callouts yet.</div>';
        return;
    }

    const hasCritical = callouts.some((item) => item.severity === 'critical');
    const hasWarning = callouts.some((item) => item.severity === 'warning');
    setChip(
        chipEl,
        hasCritical ? 'Critical signal on record' : hasWarning ? 'Warnings active' : 'Healthy signals recorded',
        hasCritical ? 'danger' : hasWarning ? 'warning' : 'info'
    );

    listEl.innerHTML = callouts.map((item) => {
        const level = item.severity === 'critical' ? 'danger' : item.severity === 'warning' ? 'warning' : 'info';
        const batchText = item.batch_number ? `Batch ${escapeHtml(item.batch_number)}` : 'Run';
        const actionText = item.action ? escapeHtml(item.action.replaceAll('_', ' ')) : 'continue';
        return `
            <div class="attention-item attn-${level}">
                <span class="attn-dot"></span>
                <div class="attn-source">${escapeHtml(item.signal_type || 'system')}</div>
                <div class="attn-msg">${escapeHtml(item.message || '')}</div>
                <div class="callout-meta">
                    <span class="callout-badge">${batchText}</span>
                    <span class="callout-badge">${actionText}</span>
                    <span class="callout-age">${escapeHtml(fmtAgo(item.timestamp))}</span>
                </div>
            </div>
        `;
    }).join('');
}

// =============================================================================
// LOGS
// =============================================================================

async function refreshLogs() {
    try {
        const data = await api('/logs?lines=100');
        const content = document.getElementById('log-content');
        const viewer = document.getElementById('log-viewer');

        if (data.error) {
            content.textContent = `Error: ${data.error}`;
        } else if (data.file_missing) {
            content.textContent = 'Log file not created yet. Start the system to generate logs.';
        } else if (!data.lines || data.lines.length === 0) {
            content.textContent = 'Log file exists but is empty.';
        } else {
            content.textContent = data.lines.join('\n');
            viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (error) {
        document.getElementById('log-content').textContent = `Failed to load logs: ${error.message}`;
    }
}

// =============================================================================
// BUTTON HANDLERS
// =============================================================================

async function handleStartManual() {
    try {
        const result = await api('/control/start', 'POST', { mode: 'manual' });
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleStartCycle() {
    const runHours = document.getElementById('run-hours').value || 4;
    const restHours = document.getElementById('rest-hours').value || 2;
    if (!confirmAction(`Start auto-cycle?\n\n• Run for ${runHours} hours\n• Rest for ${restHours} hours\n• Repeat until manually stopped`)) return;

    try {
        const result = await api('/control/start', 'POST', { mode: 'cycle' });
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleStop() {
    try {
        const result = await api('/control/stop', 'POST');
        showToast(result.message, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleKill() {
    if (!confirmAction('FORCE KILL\n\nThis will immediately terminate the controller without saving state.\n\nAre you sure?')) return;
    try {
        const result = await api('/control/kill', 'POST');
        showToast(result.message, 'warning');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleClean() {
    const mode = document.getElementById('clean-mode').value;
    const confirmText = {
        'logs': 'Clear all log files?',
        'progress': 'WARNING: Reset scraper progress?\n\nThis will make the scraper start from the beginning.',
        'outputs': 'Clear batch output files?',
        'full': 'FULL CLEAN (Safe)\n\nThis will:\n- Clear all logs\n- Reset enricher/watcher progress\n- Delete batch outputs\n\nLeads data in Google Sheets will NOT be deleted.\nAre you sure?',
        'nuclear': 'NUCLEAR CLEAN - DANGER!\n\nThis will PERMANENTLY DELETE:\n- ALL leads from Google Sheets\n- ALL destination tabs\n- ALL local progress and output files\n- ALL logs\n\nTHIS CANNOT BE UNDONE!\nAre you absolutely sure?'
    };

    if (!confirmAction(confirmText[mode] || 'Proceed with cleaning?')) return;
    if (mode === 'full' && !confirmAction('Last chance! Type of clean: FULL (Safe)\n\nClick OK to proceed.')) return;
    if (mode === 'nuclear') {
        if (!confirmAction('SECOND WARNING!\n\nYou are about to delete ALL DATA.\n\nClick OK only if you want a completely fresh start.')) return;
        if (!confirmAction('FINAL WARNING!\n\nThis is your last chance to cancel.')) return;
    }

    try {
        showToast('Cleaning in progress...', 'info');
        const result = await api('/control/clean', 'POST', { mode });
        const cleaned = result.cleaned?.length > 0 ? result.cleaned.join(', ') : 'nothing to clean';
        if (result.warning) showToast(result.warning, 'warning');
        showToast(`Cleaned: ${cleaned}`, 'success');
        refreshLogs();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleSaveConfig() {
    const runHours = parseInt(document.getElementById('run-hours').value) || 4;
    const restHours = parseInt(document.getElementById('rest-hours').value) || 2;
    try {
        await api('/config', 'POST', { run_hours: runHours, rest_hours: restHours });
        showToast('Configuration saved', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// =============================================================================
// INIT
// =============================================================================

(function() {
    // Bind buttons
    document.getElementById('btn-start-manual').addEventListener('click', handleStartManual);
    document.getElementById('btn-start-cycle').addEventListener('click', handleStartCycle);
    document.getElementById('btn-stop').addEventListener('click', handleStop);
    document.getElementById('btn-kill').addEventListener('click', handleKill);
    document.getElementById('btn-clean').addEventListener('click', handleClean);
    document.getElementById('btn-save-config').addEventListener('click', handleSaveConfig);
    document.getElementById('btn-refresh-logs').addEventListener('click', refreshLogs);
    document.getElementById('btn-download-logs').addEventListener('click', () => window.open('/logs?lines=1000&format=text', '_blank'));

    // Listen for global status updates
    window.addEventListener('status-update', (e) => {
        const data = e.detail;
        updateButtonStates(data.process.running);
        updateProcessInfo(data.process);
        updateSchedulerInfo(data.scheduler);
        updateScraperProgress(data.scraper, data.process.running, data.pipeline?.maps_scraper);
        updateScrapeHealth(data.pipeline?.maps_scraper);
        updateAggression(data.pipeline?.maps_scraper);
        renderCallouts(data.pipeline?.maps_scraper);
    });

    // Initial logs load + auto-refresh
    refreshLogs();
    setInterval(refreshLogs, 5000);

    // Re-init lucide icons for this page's dynamic content
    lucide.createIcons();
})();

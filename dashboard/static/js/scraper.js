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

function updateScraperProgress(scraper, processRunning) {
    const statusEl = document.getElementById('scraper-status');

    if (!scraper || scraper.error) {
        statusEl.textContent = 'No data';
        statusEl.classList.add('status-error');
        return;
    }
    statusEl.classList.remove('status-error');

    if (!processRunning && scraper.has_progress) {
        statusEl.textContent = 'PAUSED (Last snapshot)';
        statusEl.classList.add('status-paused');
    } else if (!scraper.has_progress) {
        statusEl.textContent = 'Fresh Start';
        statusEl.classList.remove('status-paused');
    } else {
        const statusMap = {
            'in_progress': 'In Progress',
            'category_complete': 'Category Complete',
            'all_complete': 'All Complete',
            'fresh': 'Fresh Start'
        };
        statusEl.textContent = statusMap[scraper.status] || scraper.status || 'unknown';
        statusEl.classList.remove('status-paused');
    }

    const progress = scraper.progress || {};
    const cat = progress.category || {};
    document.getElementById('scraper-category').textContent =
        cat.index !== undefined ? `${cat.index + 1}/${cat.total || '?'}` : '--';

    const loc = progress.current_location || {};
    document.getElementById('scraper-location').textContent = loc.name || '--';
    document.getElementById('scraper-keyword').textContent = loc.keyword_name || '--';

    const overall = progress.overall || {};
    document.getElementById('scraper-searches').textContent =
        overall.completed_searches !== undefined
            ? `${overall.completed_searches}/${overall.total_searches || '?'}`
            : '--';
    document.getElementById('scraper-leads').textContent = overall.total_leads_collected || '--';

    const lastSaved = progress.last_saved || scraper.checked_at || '--';
    document.getElementById('scraper-last-saved').textContent =
        lastSaved !== '--' ? formatTimestamp(lastSaved) : '--';
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
    document.getElementById('btn-download-logs').addEventListener('click', () => window.open('/logs?lines=1000', '_blank'));

    // Listen for global status updates
    window.addEventListener('status-update', (e) => {
        const data = e.detail;
        updateButtonStates(data.process.running);
        updateProcessInfo(data.process);
        updateSchedulerInfo(data.scheduler);
        updateScraperProgress(data.scraper, data.process.running);
    });

    // Initial logs load + auto-refresh
    refreshLogs();
    setInterval(refreshLogs, 5000);

    // Re-init lucide icons for this page's dynamic content
    lucide.createIcons();
})();

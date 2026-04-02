/**
 * FixMyPresence Control Center - Frontend Logic
 * Version 2: Fixed state consistency, button guards, logs, and cycle clarity
 */

// =============================================================================
// STATE
// =============================================================================

let autoRefresh = true;
let statusRefreshInterval = 3000; // 3 seconds
let logsRefreshInterval = 5000; // 5 seconds when running, slower when stopped
let isRunning = false; // Track if controller is running

// =============================================================================
// API HELPERS
// =============================================================================

async function api(endpoint, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) {
        options.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(endpoint, options);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Request failed');
        }

        return data;
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        throw error;
    }
}

// =============================================================================
// UI UPDATES
// =============================================================================

function updateStatusIndicator(running, schedulerPhase) {
    const indicator = document.getElementById('status-indicator');
    const text = document.getElementById('status-text');

    indicator.classList.remove('running', 'resting', 'stopped');

    if (running) {
        if (schedulerPhase === 'RUNNING') {
            indicator.classList.add('running');
            text.textContent = 'RUNNING (Cycle)';
        } else {
            indicator.classList.add('running');
            text.textContent = 'RUNNING';
        }
    } else if (schedulerPhase === 'RESTING') {
        indicator.classList.add('resting');
        text.textContent = 'RESTING';
    } else {
        indicator.classList.add('stopped');
        text.textContent = 'STOPPED';
    }
}

function updateButtonStates(running) {
    // Start buttons: disabled when running
    document.getElementById('btn-start-manual').disabled = running;
    document.getElementById('btn-start-cycle').disabled = running;

    // Stop buttons: disabled when stopped
    document.getElementById('btn-stop').disabled = !running;
    document.getElementById('btn-kill').disabled = !running;

    // Clean button: disabled when running (safety)
    document.getElementById('btn-clean').disabled = running;

    // Update button styling
    const startBtns = [document.getElementById('btn-start-manual'), document.getElementById('btn-start-cycle')];
    const stopBtns = [document.getElementById('btn-stop'), document.getElementById('btn-kill')];

    startBtns.forEach(btn => {
        btn.classList.toggle('btn-disabled', running);
    });
    stopBtns.forEach(btn => {
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

    // Show phase with clear indicator
    if (scheduler.enabled) {
        phaseEl.textContent = scheduler.phase === 'RUNNING' ? 'RUNNING' : 'RESTING';
        phaseEl.classList.add('cycle-enabled');
    } else {
        phaseEl.textContent = 'DISABLED';
        phaseEl.classList.remove('cycle-enabled');
    }

    // Show next transition time
    if (scheduler.next_transition) {
        const next = new Date(scheduler.next_transition);
        const now = new Date();
        const diffMs = next - now;

        if (diffMs > 0) {
            const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
            const diffMins = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
            nextEl.textContent = `in ${diffHours}h ${diffMins}m`;
        } else {
            nextEl.textContent = 'Now';
        }
    } else {
        nextEl.textContent = '--';
    }

    // Update config inputs from saved values
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

    // KEY FIX: Show PAUSED if process not running but has progress
    if (!processRunning && scraper.has_progress) {
        statusEl.textContent = 'PAUSED (Last snapshot)';
        statusEl.classList.add('status-paused');
    } else if (!scraper.has_progress) {
        statusEl.textContent = 'Fresh Start';
        statusEl.classList.remove('status-paused');
    } else {
        // Live status from scraper
        const status = scraper.status || 'unknown';
        const statusMap = {
            'in_progress': 'In Progress',
            'category_complete': 'Category Complete',
            'all_complete': 'All Complete',
            'fresh': 'Fresh Start'
        };
        statusEl.textContent = statusMap[status] || status;
        statusEl.classList.remove('status-paused');
    }

    // Progress details
    const progress = scraper.progress || {};

    // Category
    const cat = progress.category || {};
    if (cat.index !== undefined) {
        document.getElementById('scraper-category').textContent =
            `${cat.index + 1}/${cat.total || '?'}`;
    } else {
        document.getElementById('scraper-category').textContent = '--';
    }

    // Location
    const loc = progress.current_location || {};
    document.getElementById('scraper-location').textContent =
        loc.name || '--';

    // Keyword
    document.getElementById('scraper-keyword').textContent =
        loc.keyword_name || '--';

    // Searches
    const overall = progress.overall || {};
    if (overall.completed_searches !== undefined) {
        document.getElementById('scraper-searches').textContent =
            `${overall.completed_searches}/${overall.total_searches || '?'}`;
    } else {
        document.getElementById('scraper-searches').textContent = '--';
    }

    // Leads
    document.getElementById('scraper-leads').textContent =
        overall.total_leads_collected || '--';

    // Last saved - always show this
    const lastSaved = progress.last_saved || scraper.checked_at || '--';
    document.getElementById('scraper-last-saved').textContent =
        lastSaved !== '--' ? formatTimestamp(lastSaved) : '--';
}

function formatTimestamp(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString() + ' ' + date.toLocaleDateString();
    } catch {
        return isoString;
    }
}

async function refreshStatus() {
    try {
        const data = await api('/status');

        // Track running state globally
        isRunning = data.process.running;

        updateStatusIndicator(data.process.running, data.scheduler.phase);
        updateButtonStates(data.process.running);
        updateProcessInfo(data.process);
        updateSchedulerInfo(data.scheduler);
        updateScraperProgress(data.scraper, data.process.running);

    } catch (error) {
        console.error('Failed to refresh status:', error);
    }
}

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
            // Auto-scroll to bottom
            viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (error) {
        console.error('Failed to refresh logs:', error);
        document.getElementById('log-content').textContent = `Failed to load logs: ${error.message}`;
    }
}

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    // Auto-remove after 4 seconds
    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// =============================================================================
// CONFIRMATION DIALOGS
// =============================================================================

function confirmAction(message) {
    return confirm(message);
}

// =============================================================================
// BUTTON HANDLERS
// =============================================================================

async function handleStartManual() {
    try {
        const result = await api('/control/start', 'POST', { mode: 'manual' });
        showToast(result.message, result.success ? 'success' : 'error');
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleStartCycle() {
    const runHours = document.getElementById('run-hours').value || 4;
    const restHours = document.getElementById('rest-hours').value || 2;

    if (!confirmAction(`Start auto-cycle?\n\n• Run for ${runHours} hours\n• Rest for ${restHours} hours\n• Repeat until manually stopped`)) {
        return;
    }

    try {
        const result = await api('/control/start', 'POST', { mode: 'cycle' });
        showToast(result.message, result.success ? 'success' : 'error');
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleStop() {
    try {
        const result = await api('/control/stop', 'POST');
        showToast(result.message, 'success');
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleKill() {
    if (!confirmAction('FORCE KILL\n\nThis will immediately terminate the controller without saving state.\n\nAre you sure?')) {
        return;
    }

    try {
        const result = await api('/control/kill', 'POST');
        showToast(result.message, 'warning');
        refreshStatus();
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
        'nuclear': 'NUCLEAR CLEAN - DANGER!\n\nThis will PERMANENTLY DELETE:\n- ALL leads from Google Sheets\n- ALL destination tabs (Hydro_formula, etc)\n- ALL local progress and output files\n- ALL logs\n\nTHIS CANNOT BE UNDONE!\nAre you absolutely sure?'
    };

    if (!confirmAction(confirmText[mode] || 'Proceed with cleaning?')) {
        return;
    }

    // Double confirm for full clean
    if (mode === 'full') {
        if (!confirmAction('Last chance! Type of clean: FULL (Safe)\n\nClick OK to proceed.')) {
            return;
        }
    }

    // Triple confirm for nuclear clean
    if (mode === 'nuclear') {
        if (!confirmAction('SECOND WARNING!\n\nYou are about to delete ALL DATA from:\n- Leads tab\n- Validated_leads\n- Ultra_validated\n- Hydro_formula / destination tabs\n\nClick OK only if you want a completely fresh start.')) {
            return;
        }
        if (!confirmAction('FINAL WARNING!\n\nType of clean: NUCLEAR\n\nThis is your last chance to cancel.\nClick OK to permanently delete everything.')) {
            return;
        }
    }

    try {
        showToast('Cleaning in progress...', 'info');
        const result = await api('/control/clean', 'POST', { mode });
        const cleanedItems = result.cleaned && result.cleaned.length > 0
            ? result.cleaned.join(', ')
            : 'nothing to clean';

        if (result.warning) {
            showToast(result.warning, 'warning');
        }
        showToast(`Cleaned: ${cleanedItems}`, 'success');
        refreshStatus();
        refreshLogs();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function handleSaveConfig() {
    const runHours = parseInt(document.getElementById('run-hours').value) || 4;
    const restHours = parseInt(document.getElementById('rest-hours').value) || 2;

    try {
        const result = await api('/config', 'POST', {
            run_hours: runHours,
            rest_hours: restHours
        });
        showToast('Configuration saved', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function handleDownloadLogs() {
    window.open('/logs?lines=1000', '_blank');
}

// =============================================================================
// INITIALIZATION
// =============================================================================

function init() {
    // Bind buttons
    document.getElementById('btn-start-manual').addEventListener('click', handleStartManual);
    document.getElementById('btn-start-cycle').addEventListener('click', handleStartCycle);
    document.getElementById('btn-stop').addEventListener('click', handleStop);
    document.getElementById('btn-kill').addEventListener('click', handleKill);
    document.getElementById('btn-clean').addEventListener('click', handleClean);
    document.getElementById('btn-save-config').addEventListener('click', handleSaveConfig);
    document.getElementById('btn-refresh-logs').addEventListener('click', refreshLogs);
    document.getElementById('btn-download-logs').addEventListener('click', handleDownloadLogs);

    // Initial load
    refreshStatus();
    refreshLogs();

    // Auto-refresh status every 3 seconds
    setInterval(() => {
        if (autoRefresh) {
            refreshStatus();
        }
    }, statusRefreshInterval);

    // Auto-refresh logs - faster when running, slower when stopped
    setInterval(() => {
        if (autoRefresh) {
            // Refresh logs more frequently when running
            refreshLogs();
        }
    }, logsRefreshInterval);
}

document.addEventListener('DOMContentLoaded', init);

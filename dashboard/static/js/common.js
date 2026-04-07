/**
 * FixMyPresence Control Center - Common JS
 * Shared across all pages: API helper, toasts, sidebar status
 */

// =============================================================================
// API HELPER
// =============================================================================

async function api(endpoint, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) {
        options.body = JSON.stringify(body);
    }

    const response = await fetch(endpoint, options);
    const data = await response.json();

    if (!response.ok) {
        throw new Error(data.detail || 'Request failed');
    }

    return data;
}

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// =============================================================================
// SIDEBAR STATUS (runs on every page)
// =============================================================================

let _globalIsRunning = false;
let _globalSchedulerPhase = null;
let _statusPollInFlight = false;
let _lastRotationSnapshot = null;

const ROTATION_REGISTRY_LABELS = {
    pipeline_sheets: 'Pipeline',
    source_settlement: 'Source',
    output_target: 'Output'
};

function formatRelativeFuture(isoString) {
    if (!isoString) return '--';
    const target = new Date(isoString);
    if (Number.isNaN(target.getTime())) return '--';
    const diffMs = target.getTime() - Date.now();
    if (diffMs <= 0) return 'now';
    const totalSeconds = Math.round(diffMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes >= 60) {
        const hours = Math.floor(minutes / 60);
        const remMinutes = minutes % 60;
        return `${hours}h ${remMinutes}m`;
    }
    if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
    return `${seconds}s`;
}

function formatRelativePast(isoString) {
    if (!isoString) return '--';
    const target = new Date(isoString);
    if (Number.isNaN(target.getTime())) return '--';
    const diffMs = Date.now() - target.getTime();
    if (diffMs <= 0) return 'just now';
    const totalSeconds = Math.round(diffMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes >= 60) {
        const hours = Math.floor(minutes / 60);
        const remMinutes = minutes % 60;
        return `${hours}h ${remMinutes}m ago`;
    }
    if (minutes > 0) return `${minutes}m ago`;
    return `${seconds}s ago`;
}

function inferFetchMode(entry) {
    const live = entry?.live_status || {};
    const errorText = String(entry?.last_error || live?.error || '').toLowerCase();
    if (errorText.includes('quota exceeded') || errorText.includes('[429]')) {
        return { key: 'quota', label: 'Backoff' };
    }
    if (entry?.rotation_status === 'paused_pending_approval' || live?.phase === 'blocked') {
        return { key: 'blocked', label: 'Blocked' };
    }
    if (entry?.rotation_status === 'prepared' || live?.phase === 'prepare') {
        return { key: 'prepare', label: 'Prepare' };
    }
    if (live?.phase === 'warning') {
        return { key: 'warning', label: 'Warning' };
    }
    if (live?.phase === 'healthy') {
        return { key: 'healthy', label: 'Healthy' };
    }
    return { key: 'healthy', label: 'Watching' };
}

function renderSidebarFetchStatus(rotation) {
    _lastRotationSnapshot = rotation || null;
    const modeEl = document.getElementById('sidebar-fetch-mode');
    const nextEl = document.getElementById('sidebar-fetch-next');
    const lastEl = document.getElementById('sidebar-fetch-last');
    const noteEl = document.getElementById('sidebar-fetch-note');
    const listEl = document.getElementById('sidebar-fetch-registry-list');
    if (!modeEl || !nextEl || !lastEl || !noteEl || !listEl) return;

    const registries = (rotation && rotation.registries) || {};
    const entries = ['pipeline_sheets', 'source_settlement', 'output_target']
        .map((key) => ({ key, entry: registries[key] || {} }));

    const modePriority = { quota: 5, blocked: 4, prepare: 3, warning: 2, healthy: 1 };
    let selectedMode = { key: 'healthy', label: 'Awaiting' };
    entries.forEach(({ entry }) => {
        const candidate = inferFetchMode(entry);
        if ((modePriority[candidate.key] || 0) > (modePriority[selectedMode.key] || 0)) {
            selectedMode = candidate;
        }
    });

    const nextChecks = entries
        .map(({ entry }) => entry?.next_check_at)
        .filter(Boolean)
        .map((value) => new Date(value))
        .filter((value) => !Number.isNaN(value.getTime()))
        .sort((a, b) => a - b);
    const lastChecks = entries
        .map(({ entry }) => entry?.last_checked_at)
        .filter(Boolean)
        .map((value) => new Date(value))
        .filter((value) => !Number.isNaN(value.getTime()))
        .sort((a, b) => b - a);

    modeEl.className = `sidebar-fetch-mode nav-text ${selectedMode.key}`;
    modeEl.textContent = selectedMode.label;
    nextEl.textContent = nextChecks.length ? formatRelativeFuture(nextChecks[0].toISOString()) : '--';
    lastEl.textContent = lastChecks.length ? formatRelativePast(lastChecks[0].toISOString()) : '--';

    const quotaEntry = entries.find(({ entry }) => inferFetchMode(entry).key === 'quota');
    const blockedEntry = entries.find(({ entry }) => inferFetchMode(entry).key === 'blocked');
    if (quotaEntry) {
        noteEl.textContent = `${ROTATION_REGISTRY_LABELS[quotaEntry.key]} is in quota backoff.`;
    } else if (blockedEntry) {
        noteEl.textContent = `${ROTATION_REGISTRY_LABELS[blockedEntry.key]} is waiting on approval or auto-continue.`;
    } else if (selectedMode.key === 'prepare') {
        noteEl.textContent = 'A replacement sheet is being prepared before capacity is reached.';
    } else if (selectedMode.key === 'warning') {
        noteEl.textContent = 'Capacity warning is active. Monitoring cadence is elevated.';
    } else {
        noteEl.textContent = 'Adaptive fetch cadence is healthy across the monitored sheet paths.';
    }

    listEl.innerHTML = entries.map(({ key, entry }) => {
        const mode = inferFetchMode(entry);
        const nextCheck = entry?.next_check_at ? formatRelativeFuture(entry.next_check_at) : '--';
        return `
            <div class="sidebar-fetch-registry">
                <span class="sidebar-fetch-registry-name">${ROTATION_REGISTRY_LABELS[key]}</span>
                <span class="sidebar-fetch-registry-value">${mode.label} · ${nextCheck}</span>
            </div>
        `;
    }).join('');
}

function refreshSidebarFetchCountdown() {
    if (!_lastRotationSnapshot) return;
    renderSidebarFetchStatus(_lastRotationSnapshot);
}

function updateGlobalStatus(processRunning, schedulerPhase) {
    _globalIsRunning = processRunning;
    _globalSchedulerPhase = schedulerPhase;

    // Sidebar dot
    const dot = document.getElementById('sidebar-dot');
    const text = document.getElementById('sidebar-status-text');
    if (dot) {
        dot.classList.remove('running', 'resting', 'stopped');
        if (processRunning) {
            dot.classList.add('running');
            if (text) text.textContent = 'Running';
        } else if (schedulerPhase === 'RESTING') {
            dot.classList.add('resting');
            if (text) text.textContent = 'Resting';
        } else {
            dot.classList.add('stopped');
            if (text) text.textContent = 'Stopped';
        }
    }

    // Page header indicator
    const indicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    if (indicator) {
        indicator.classList.remove('running', 'resting', 'stopped');
        if (processRunning) {
            indicator.classList.add('running');
            if (statusText) statusText.textContent = schedulerPhase === 'RUNNING' ? 'RUNNING (Cycle)' : 'RUNNING';
        } else if (schedulerPhase === 'RESTING') {
            indicator.classList.add('resting');
            if (statusText) statusText.textContent = 'RESTING';
        } else {
            indicator.classList.add('stopped');
            if (statusText) statusText.textContent = 'STOPPED';
        }
    }
}

async function pollGlobalStatus() {
    if (_statusPollInFlight) return;
    _statusPollInFlight = true;
    try {
        const data = await api('/status');
        updateGlobalStatus(data.process.running, data.scheduler.phase);
        renderSidebarFetchStatus((data.meta || {}).sheet_rotation || null);
        // Dispatch custom event so page-specific JS can react
        window.dispatchEvent(new CustomEvent('status-update', { detail: data }));
    } catch (e) {
        console.error('Status poll failed:', e);
    } finally {
        _statusPollInFlight = false;
    }
}

// Poll every 30s. Most sheet-backed signals do not need second-by-second
// freshness, and a slower cadence dramatically reduces Google Sheets read
// pressure across the dashboard.
pollGlobalStatus();
setInterval(pollGlobalStatus, 30000);
setInterval(refreshSidebarFetchCountdown, 1000);

// =============================================================================
// HELPERS
// =============================================================================

function formatTimestamp(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString() + ' ' + date.toLocaleDateString();
    } catch {
        return isoString;
    }
}

function confirmAction(message) {
    return confirm(message);
}

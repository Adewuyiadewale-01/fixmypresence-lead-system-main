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
    try {
        const data = await api('/status');
        updateGlobalStatus(data.process.running, data.scheduler.phase);
        // Dispatch custom event so page-specific JS can react
        window.dispatchEvent(new CustomEvent('status-update', { detail: data }));
    } catch (e) {
        console.error('Status poll failed:', e);
    }
}

// Poll every 3s
pollGlobalStatus();
setInterval(pollGlobalStatus, 3000);

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

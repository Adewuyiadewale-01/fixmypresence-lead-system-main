/**
 * Robot Page - automation posture, decisions, and callouts
 * Depends on common.js (formatTimestamp)
 */

function robotFmtNum(value) {
    if (value === null || value === undefined || value === '') return '--';
    if (typeof value === 'number') return value.toLocaleString();
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value);
}

function robotFmtPct(value) {
    if (value === null || value === undefined || value === '') return '--';
    const parsed = Number(value);
    return Number.isFinite(parsed) ? `${parsed.toFixed(1)}%` : String(value);
}

function robotFmtAgo(isoString) {
    if (!isoString) return '--';
    const diffMs = Date.now() - new Date(isoString).getTime();
    if (!Number.isFinite(diffMs)) return '--';
    if (diffMs < 0) return formatTimestamp(isoString);
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

function robotEscapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function robotSetChip(el, text, tone = 'info') {
    if (!el) return;
    el.textContent = text;
    el.classList.remove('attn-info', 'attn-warning', 'attn-danger');
    el.classList.add(
        tone === 'danger' ? 'attn-danger' :
        tone === 'warning' ? 'attn-warning' :
        'attn-info'
    );
}

function humanizeSnake(value) {
    if (!value) return '--';
    return String(value)
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (m) => m.toUpperCase());
}

function robotDotClass(status, isDanger = false) {
    if (isDanger) return 'warning';
    if (status === 'running' || status === 'active' || status === 'healthy') return 'active';
    if (status === 'warning' || status === 'confirming' || status === 'stale' || status === 'paused_critical' || status === 'resting') return 'warning';
    return '';
}

function robotFmtWaitWindow(waitingMinutes, maxWaitMinutes) {
    if (waitingMinutes == null && maxWaitMinutes == null) return '--';
    if (waitingMinutes == null) return `0m / ${robotFmtNum(maxWaitMinutes)}m`;
    if (maxWaitMinutes == null) return `${robotFmtNum(waitingMinutes)}m`;
    return `${robotFmtNum(waitingMinutes)}m / ${robotFmtNum(maxWaitMinutes)}m`;
}

function robotFmtRemaining(isoString) {
    if (!isoString) return '--';
    const diffMs = new Date(isoString).getTime() - Date.now();
    if (!Number.isFinite(diffMs)) return '--';
    if (diffMs <= 0) return 'due now';
    const mins = Math.floor(diffMs / 60000);
    const secs = Math.floor((diffMs % 60000) / 1000);
    if (mins < 1) return `${secs}s`;
    const hours = Math.floor(mins / 60);
    if (hours < 1) return `${mins}m`;
    return `${hours}h ${mins % 60}m`;
}

function isActiveCriticalCallout(item, currentPhase) {
    if (!item || item.severity !== 'critical') return false;
    if (currentPhase === 'paused_critical') return item.action === 'auto_paused';
    if (currentPhase === 'confirming') return item.action === 'confirming';
    return false;
}

let robotCategoryApprovalInFlight = false;
let robotCategoryAutoApprovedDeadline = null;
let robotCategoryCountdownTimer = null;
let robotPauseReviewActionInFlight = false;
let robotCategoryUiState = {
    awaitingApproval: false,
    processRunning: false,
    nextCategory: null,
    deadline: null,
    timeoutMinutes: null,
    overdue: false,
    allComplete: false,
};

function enrichmentStateText(enrichment) {
    if (!enrichment) return 'Idle';
    if (enrichment.is_stale) return 'Stale lock';
    if (enrichment.locked) return 'Draining';
    if (enrichment.trigger_state === 'timeout_ready') return 'Forced drain due';
    if (enrichment.trigger_state === 'threshold_ready') return 'Ready to drain';
    if ((enrichment.pending || 0) > 0) return 'Watching backlog';
    return 'Idle';
}

function summarizeRobotState(process, scheduler, scraper, attention, callouts, enrichment) {
    const progression = scraper.category_progression || {};
    const criticalCount =
        callouts.filter((item) => item.severity === 'critical').length +
        attention.filter((item) => item.level === 'danger').length;
    const warningCount =
        callouts.filter((item) => item.severity === 'warning').length +
        attention.filter((item) => item.level === 'warning').length;

    if (scraper.current_phase === 'paused_critical') {
        return {
            state: 'Paused For Review',
            action: 'Manual review required',
            source: scraper.recommendation || 'Scraper guardrail',
            summary: 'Robot confirmed a critical scraper signal across consecutive batches and paused the scraper after the batch boundary.',
            next: 'Review the latest critical callout and logs before restarting.',
            tone: 'danger',
        };
    }

    if (scraper.current_phase === 'confirming') {
        return {
            state: 'Confirming Signal',
            action: 'Finish next batch',
            source: 'Scraper confirmation logic',
            summary: 'Robot saw a critical-looking batch and is using the next completed batch to confirm whether the issue is systemic or batch-specific.',
            next: 'Let the next batch finish, then review whether the signal cleared or escalated.',
            tone: 'warning',
        };
    }

    if (enrichment?.is_stale) {
        return {
            state: 'Enrichment Stalled',
            action: 'Review watcher lock',
            source: 'Enrichment watcher',
            summary: 'Robot sees a stale enrichment lock, which usually means the LinkedIn drain worker crashed or never released its lock cleanly.',
            next: 'Inspect enrichment logs and clear or restart the stuck worker before backlog builds further.',
            tone: 'warning',
        };
    }

    if (enrichment?.locked) {
        return {
            state: 'Draining Enrichment Queue',
            action: 'Let drain finish',
            source: 'LinkedIn enrichment',
            summary: 'Robot sees the LinkedIn enricher actively draining the validated backlog right now.',
            next: 'No intervention needed unless the lock age keeps climbing without coverage moving.',
            tone: 'info',
        };
    }

    if (enrichment?.trigger_state === 'timeout_ready' || enrichment?.trigger_state === 'threshold_ready') {
        return {
            state: 'Enrichment Ready',
            action: 'Launch or verify drain',
            source: enrichment.trigger_state === 'timeout_ready' ? 'Forced drain timer' : 'Backlog threshold',
            summary: 'Robot sees enough enrichment pressure to justify a LinkedIn drain run and is watching for the watcher to launch it.',
            next: 'Confirm the watcher ticked recently or trigger a drain if the queue stays ready.',
            tone: enrichment.trigger_state === 'timeout_ready' ? 'warning' : 'info',
        };
    }

    if (criticalCount > 0) {
        return {
            state: 'Recent Critical History',
            action: 'Review cleared criticals',
            source: scraper.recommendation || 'Recent critical history',
            summary: 'Robot sees cleared critical events in recent history. The scraper is not currently in a live critical state, but the pattern still deserves a review.',
            next: 'Use the feed and logs to decide whether the recent criticals were batch-specific or point to a broader trend.',
            tone: 'warning',
        };
    }

    if (warningCount > 0) {
        return {
            state: 'Watching Warnings',
            action: 'Continue with caution',
            source: scraper.recommendation || 'Warning callouts',
            summary: 'Robot is allowing automation to continue while warning-level signals stay on watch.',
            next: 'Check whether warnings are batch-specific or building into a wider pattern.',
            tone: 'warning',
        };
    }

    if (progression.awaiting_approval) {
        return {
            state: progression.auto_progress_overdue ? 'Auto Progress Due' : 'Awaiting Category Approval',
            action: progression.auto_progress_overdue ? 'Auto-progressing' : 'Approve or wait',
            source: 'Category progression gate',
            summary: progression.auto_progress_overdue
                ? 'Robot reached the category timeout window and is ready to auto-progress the scraper.'
                : 'Robot is holding the next scraper category until an operator approves it or the timeout window expires.',
            next: progression.auto_progress_overdue
                ? 'The next status refresh will auto-progress if the controller is still idle.'
                : 'Approve the next category here on Robot, or wait for the timeout window to expire.',
            tone: progression.auto_progress_overdue ? 'warning' : 'info',
        };
    }

    if (process.running) {
        return {
            state: 'Monitoring Healthy Run',
            action: 'Continue automation',
            source: scraper.recommendation || 'Healthy scraper posture',
            summary: 'Robot sees no urgent flags and is letting the current automation path continue.',
            next: 'No manual action needed unless throughput or quality drifts.',
            tone: 'info',
        };
    }

    if (scheduler.enabled && scheduler.phase === 'RESTING') {
        return {
            state: 'Resting Window',
            action: 'Wait for next cycle',
            source: 'Scheduler',
            summary: 'Robot is idle because the scheduler is in a rest window, not because the system is broken.',
            next: 'No action needed unless you want to override the schedule manually.',
            tone: 'info',
        };
    }

    return {
        state: 'Idle',
        action: 'Waiting for operator',
        source: 'System idle',
        summary: 'Robot is watching a quiet system and will surface callouts as automation starts running again.',
        next: 'Use the Scraper or Overview page when you are ready to resume work.',
        tone: 'info',
    };
}

function renderRobotState(data) {
    const process = data.process || {};
    const scheduler = data.scheduler || {};
    const pipeline = data.pipeline || {};
    const scraper = pipeline.maps_scraper || {};
    const enrichment = pipeline.enrichment || {};
    const attention = Array.isArray(pipeline.attention) ? pipeline.attention : [];
    const callouts = Array.isArray(scraper.recent_callouts) ? scraper.recent_callouts : [];
    const latestCallout = callouts[0];

    const posture = summarizeRobotState(process, scheduler, scraper, attention, callouts, enrichment);
    const enrichmentDrivenSources = new Set([
        'Enrichment watcher',
        'LinkedIn enrichment',
        'Forced drain timer',
        'Backlog threshold',
    ]);
    const enrichmentDriven = enrichmentDrivenSources.has(posture.source);
    robotSetChip(document.getElementById('robot-posture-chip'), posture.state, posture.tone);
    document.getElementById('robot-posture-summary').textContent = posture.summary;

    document.getElementById('robot-decision-state').textContent = posture.state;
    document.getElementById('robot-decision-action').textContent = posture.action;
    document.getElementById('robot-decision-source').textContent =
        latestCallout && !enrichmentDriven
            ? `${humanizeSnake(latestCallout.signal_type)} · ${humanizeSnake(latestCallout.severity)}`
            : posture.source;
    document.getElementById('robot-decision-latest').textContent =
        enrichmentDriven
            ? (
                enrichment?.is_stale
                    ? `Lock stuck for ${robotFmtNum(enrichment.lock_age)}m.`
                    : enrichment?.trigger_state === 'timeout_ready'
                        ? 'Forced drain threshold reached. Watcher should launch on the next tick.'
                        : enrichment?.trigger_state === 'threshold_ready'
                            ? 'Backlog threshold reached. Watcher should drain on the next tick.'
                            : enrichment?.locked
                                ? 'LinkedIn drain worker is actively running.'
                                : 'Enrichment backlog is being monitored.'
            )
            : (latestCallout?.message || 'No recent scraper decision note yet.');

    const schedulerText = scheduler.enabled
        ? `${humanizeSnake(scheduler.phase || 'running')}${scheduler.next_transition ? ` · next ${robotFmtAgo(scheduler.next_transition)}` : ''}`
        : 'Disabled';
    const scraperPhase = scraper.current_phase
        ? humanizeSnake(scraper.current_phase)
        : humanizeSnake(scraper.status || 'idle');

    document.getElementById('robot-context-process').textContent =
        process.running ? `Running${process.pid ? ` · PID ${robotFmtNum(process.pid)}` : ''}` : 'Stopped';
    document.getElementById('robot-context-scheduler').textContent = schedulerText;
    document.getElementById('robot-context-scraper').textContent = scraperPhase;
    document.getElementById('robot-context-batch').textContent =
        scraper.current_batch_number != null ? `Batch ${robotFmtNum(scraper.current_batch_number)}` : '--';
    document.getElementById('robot-context-window').textContent =
        scraper.run_started_at
            ? `${process.running ? 'Running' : 'Last run'} · ${formatTimestamp(process.running ? scraper.run_started_at : (scraper.run_ended_at || scraper.run_started_at))}`
            : '--';
    document.getElementById('robot-context-activity').textContent =
        scraper.last_activity ? `${formatTimestamp(scraper.last_activity)} (${robotFmtAgo(scraper.last_activity)})` : '--';

    const criticalCount =
        callouts.filter((item) => item.severity === 'critical').length +
        attention.filter((item) => item.level === 'danger').length;
    const warningCount =
        callouts.filter((item) => item.severity === 'warning').length +
        attention.filter((item) => item.level === 'warning').length;

    document.getElementById('robot-review-critical').textContent = robotFmtNum(criticalCount);
    document.getElementById('robot-review-warning').textContent = robotFmtNum(warningCount);
    document.getElementById('robot-review-system').textContent = robotFmtNum(attention.length);
    document.getElementById('robot-review-next').textContent = posture.next;
}

function renderRobotEnrichment(enrichment) {
    const chipEl = document.getElementById('robot-enrichment-chip');
    const pending = Number(enrichment?.pending || 0);
    const threshold = Number(enrichment?.trigger_threshold || 0);
    const eligible = Number(enrichment?.eligible_live || 0);
    const pct = threshold > 0 ? Math.min(100, Math.round((pending / threshold) * 100)) : 0;
    const state = enrichmentStateText(enrichment);

    let tone = 'info';
    if (enrichment?.is_stale || enrichment?.trigger_state === 'timeout_ready') tone = 'warning';
    else if (enrichment?.locked || enrichment?.trigger_state === 'threshold_ready') tone = 'info';

    robotSetChip(chipEl, state, tone);

    document.getElementById('robot-enrichment-state').textContent = state;
    document.getElementById('robot-enrichment-backlog').textContent =
        threshold > 0 ? `${robotFmtNum(pending)} / ${robotFmtNum(threshold)}` : robotFmtNum(pending);
    document.getElementById('robot-enrichment-coverage').textContent =
        enrichment?.coverage_rate != null
            ? `${robotFmtPct(enrichment.coverage_rate)} · ${robotFmtNum(enrichment.checked)}/${robotFmtNum(eligible)} checked`
            : '--';
    document.getElementById('robot-enrichment-employees').textContent =
        enrichment?.employee_completion_rate != null
            ? `${robotFmtPct(enrichment.employee_completion_rate)} · ${robotFmtNum(enrichment.employees)}/${robotFmtNum(enrichment.found)} found`
            : '--';
    document.getElementById('robot-enrichment-window').textContent =
        robotFmtWaitWindow(enrichment?.waiting_minutes, enrichment?.max_wait_minutes);
    document.getElementById('robot-enrichment-launch').textContent =
        enrichment?.last_launch_at ? `${formatTimestamp(enrichment.last_launch_at)} (${robotFmtAgo(enrichment.last_launch_at)})` : '--';
    document.getElementById('robot-enrichment-threshold').textContent =
        threshold > 0 ? `${robotFmtNum(pending)} / ${robotFmtNum(threshold)} · ${pct}%` : robotFmtNum(pending);

    const fill = document.getElementById('robot-enrichment-fill');
    if (fill) {
        fill.style.width = `${pct}%`;
        fill.className = 'pipeline-progress-fill';
        if (enrichment?.is_stale || enrichment?.trigger_state === 'timeout_ready') fill.classList.add('danger');
        else if (pct >= 75 || enrichment?.trigger_state === 'threshold_ready') fill.classList.add('warning');
    }

    let note = 'Waiting for enrichment telemetry.';
    if (enrichment?.is_stale) {
        note = `Lock stuck for ${robotFmtNum(enrichment.lock_age)}m.`;
    } else if (enrichment?.locked) {
        note = enrichment?.reason
            ? `Drain worker active via ${humanizeSnake(enrichment.reason)}.`
            : 'Drain worker is actively processing backlog.';
    } else if (enrichment?.trigger_state === 'timeout_ready') {
        note = 'Forced drain threshold reached. The watcher should launch a run on the next tick.';
    } else if (enrichment?.trigger_state === 'threshold_ready') {
        note = 'Backlog threshold reached. The watcher should drain the queue on the next tick.';
    } else if (pending > 0) {
        note = eligible > 0
            ? `${robotFmtNum(pending)} of ${robotFmtNum(eligible)} validated live rows still need first-pass LinkedIn outcome.`
            : `${robotFmtNum(pending)} rows are still waiting for first-pass LinkedIn outcome.`;
    } else {
        note = 'No pending first-pass LinkedIn backlog right now.';
    }
    document.getElementById('robot-enrichment-note').textContent = note;
}

function renderRobotCallouts(scraper) {
    const listEl = document.getElementById('robot-callouts-list');
    const chipEl = document.getElementById('robot-callouts-chip');
    const callouts = (scraper && Array.isArray(scraper.recent_callouts)) ? scraper.recent_callouts : [];

    if (!callouts.length) {
        robotSetChip(chipEl, 'No scraper decisions on record', 'info');
        listEl.innerHTML = '<div class="attention-empty">No scraper decisions on record yet.</div>';
        return;
    }

    const currentPhase = scraper?.current_phase || '';
    const hasCritical = callouts.some((item) => item.severity === 'critical');
    const hasWarning = callouts.some((item) => item.severity === 'warning');
    robotSetChip(
        chipEl,
        currentPhase === 'paused_critical'
            ? 'Critical decision active'
            : currentPhase === 'confirming'
                ? 'Confirming signal'
                : hasCritical
                    ? 'Recent critical history'
                    : hasWarning
                        ? 'Warning decisions active'
                        : 'Healthy decision notes',
        currentPhase === 'paused_critical'
            ? 'danger'
            : (currentPhase === 'confirming' || hasCritical || hasWarning)
                ? 'warning'
                : 'info'
    );

    let renderedDecisionActionCard = false;
    const itemsHtml = callouts.map((item, index) => {
        const activeCritical = isActiveCriticalCallout(item, currentPhase);
        const historicalCritical = item.severity === 'critical' && !activeCritical;
        const tone = activeCritical ? 'danger' : item.severity === 'warning' || historicalCritical ? 'warning' : 'info';
        const batchText = item.batch_number ? `Batch ${robotEscapeHtml(item.batch_number)}` : 'Run';
        const actionText = historicalCritical
            ? 'History'
            : item.action
                ? humanizeSnake(item.action)
                : 'Continue';
        const source = `${humanizeSnake(item.signal_type || 'system')} · ${humanizeSnake(item.window || 'run')}`;
        const isCritical = item.severity === 'critical';
        let reviewActionMode = null;
        if (!renderedDecisionActionCard && isCritical) {
            if (activeCritical && currentPhase === 'paused_critical') {
                reviewActionMode = 'paused';
            } else if (activeCritical && currentPhase === 'confirming' && (item.action === 'confirming' || item.action === 'auto_paused')) {
                reviewActionMode = 'confirming';
            }
        }
        if (reviewActionMode) renderedDecisionActionCard = true;
        const actionNoteId = `robot-callout-review-note-${index}`;
        const actionControls = reviewActionMode === 'paused'
            ? `
                    <div class="callout-actions">
                        <button class="btn btn-success btn-small" type="button" data-review-action="resume" data-review-note-id="${actionNoteId}">
                            Resume Scraper
                        </button>
                        <button class="btn btn-secondary btn-small" type="button" data-review-action="hold" data-review-note-id="${actionNoteId}">
                            Keep Paused
                        </button>
                    </div>
                    <div id="${actionNoteId}" class="callout-action-note">
                        Resume continues from the saved scraper position. Keep Paused disables cycle auto-restart until you manually start the system again.
                    </div>
                `
            : reviewActionMode === 'confirming'
                ? `
                    <div class="callout-actions">
                        <button class="btn btn-success btn-small" type="button" data-review-action="continue" data-review-note-id="${actionNoteId}">
                            Let It Continue
                        </button>
                        <button class="btn btn-secondary btn-small" type="button" data-review-action="pause_now" data-review-note-id="${actionNoteId}">
                            Pause Now
                        </button>
                    </div>
                    <div id="${actionNoteId}" class="callout-action-note">
                        Continue leaves the scraper running while Robot watches the next batch. Pause Now stops the scraper for review and disables cycle auto-restart.
                    </div>
                `
                : '';
        return `
            <div class="attention-item attn-${tone}">
                <span class="attn-dot"></span>
                <div class="attn-source">${robotEscapeHtml(source)}</div>
                <div class="attn-msg">${robotEscapeHtml(item.message || '')}</div>
                <div class="callout-meta">
                    <span class="callout-badge">${batchText}</span>
                    <span class="callout-badge">${robotEscapeHtml(actionText)}</span>
                    <span class="callout-age">${robotEscapeHtml(robotFmtAgo(item.timestamp))}</span>
                </div>
                ${historicalCritical ? `<div class="callout-action-note">This critical alert is historical. The current scraper phase is no longer in a live critical stop.</div>` : ''}
                ${actionControls}
            </div>
        `;
    });
    renderAttentionList(listEl, itemsHtml, { title: 'Scraper decisions' });
}

function renderRobotAttention(pipeline) {
    const listEl = document.getElementById('robot-attention-list');
    const chipEl = document.getElementById('robot-attention-chip');
    const attention = (pipeline && Array.isArray(pipeline.attention)) ? pipeline.attention : [];

    if (!attention.length) {
        robotSetChip(chipEl, 'No active issues', 'info');
        listEl.innerHTML = '<div class="attention-empty">No system attention flags right now.</div>';
        return;
    }

    const hasDanger = attention.some((item) => item.level === 'danger');
    const hasWarning = attention.some((item) => item.level === 'warning');
    robotSetChip(
        chipEl,
        hasDanger ? 'Critical attention needed' : hasWarning ? 'Warnings to review' : 'Informational prompts',
        hasDanger ? 'danger' : hasWarning ? 'warning' : 'info'
    );

    const itemsHtml = attention.map((item) => {
        const tone = item.level === 'danger' ? 'danger' : item.level === 'warning' ? 'warning' : 'info';
        return `
            <div class="attention-item attn-${tone}">
                <span class="attn-dot"></span>
                <div class="attn-source">${robotEscapeHtml(item.source || 'System')}</div>
                <div class="attn-msg">${robotEscapeHtml(item.message || '')}</div>
                <div class="callout-meta">
                    <span class="callout-badge">Live</span>
                    <span class="callout-age">now</span>
                </div>
            </div>
        `;
    });
    renderAttentionList(listEl, itemsHtml, { title: 'Robot attention' });
}

function renderAutomationMesh(data) {
    const process = data.process || {};
    const scheduler = data.scheduler || {};
    const pipeline = data.pipeline || {};
    const scraper = pipeline.maps_scraper || {};
    const watchers = pipeline.watchers || {};

    const mesh = [
        {
            id: 'process',
            label: process.running ? `Running${process.pid ? ` · PID ${robotFmtNum(process.pid)}` : ''}` : 'Stopped',
            tone: process.running ? 'active' : '',
        },
        {
            id: 'scheduler',
            label: scheduler.enabled ? humanizeSnake(scheduler.phase || 'running') : 'Disabled',
            tone: scheduler.enabled ? (scheduler.phase === 'RESTING' ? 'warning' : 'active') : '',
        },
        {
            id: 'scraper',
            label: humanizeSnake(scraper.current_phase || scraper.status || 'idle'),
            tone: robotDotClass(scraper.current_phase || scraper.status, scraper.current_phase === 'paused_critical'),
        },
        {
            id: 'enrichment',
            label: humanizeSnake(watchers.enrichment_watcher || 'idle'),
            tone: robotDotClass(watchers.enrichment_watcher),
        },
        {
            id: 'ultra',
            label: humanizeSnake(watchers.ultra_watcher || 'idle'),
            tone: robotDotClass(watchers.ultra_watcher),
        },
        {
            id: 'scraperv3',
            label: humanizeSnake(watchers.scraper_v3_watcher || 'idle'),
            tone: robotDotClass(watchers.scraper_v3_watcher),
        },
    ];

    mesh.forEach((item) => {
        const dot = document.getElementById(`robot-dot-${item.id}`);
        const value = document.getElementById(`robot-mesh-${item.id}`);
        if (dot) {
            dot.classList.remove('active', 'warning');
            if (item.tone) dot.classList.add(item.tone);
        }
        if (value) value.textContent = item.label;
    });

    const note = [];
    if (scraper.current_phase === 'paused_critical') {
        note.push('Maps scraper is paused by the robot until reviewed.');
    } else if (scraper.current_phase === 'confirming') {
        note.push('Maps scraper is confirming whether the last critical-looking batch was systemic.');
    } else if (process.running) {
        note.push('Primary automation path is live.');
    } else if (scheduler.enabled && scheduler.phase === 'RESTING') {
        note.push('Scheduler is intentionally resting between run windows.');
    } else {
        note.push('No automation lane is actively running right now.');
    }
    if (watchers.enrichment_watcher === 'stale' || watchers.ultra_watcher === 'stale' || watchers.scraper_v3_watcher === 'stale') {
        note.push('One or more watchers look stale and should be checked.');
    }
    document.getElementById('robot-mesh-note').textContent = note.join(' · ');
}

function renderRobotCategoryProgression(data) {
    const process = data.process || {};
    const scraper = data.pipeline?.maps_scraper || {};
    const progression = scraper.category_progression || {};
    const button = document.getElementById('btn-robot-approve-category');
    const buttonLabel = document.getElementById('robot-category-button-label');
    const noteEl = document.getElementById('robot-category-cta-note');
    const stateEl = document.getElementById('robot-category-state');
    const nextEl = document.getElementById('robot-category-next');
    const deadlineEl = document.getElementById('robot-category-deadline');
    if (!button || !buttonLabel || !noteEl || !stateEl || !nextEl || !deadlineEl) return;

    const awaitingApproval = Boolean(progression.awaiting_approval);
    const nextCategory = progression.next_category_number;
    const deadline = progression.auto_progress_deadline;
    const timeoutMinutes = progression.timeout_minutes;
    const overdue = Boolean(progression.auto_progress_overdue);
    const controllerRunning = Boolean(process.controller_running);

    if (awaitingApproval) {
        stateEl.textContent = overdue ? 'Auto-progress due' : 'Waiting for approval';
        nextEl.textContent = nextCategory != null ? `Category ${robotFmtNum(nextCategory)}` : '--';
        deadlineEl.textContent = deadline
            ? `${robotFmtRemaining(deadline)} · timeout ${robotFmtNum(timeoutMinutes)}m`
            : `timeout ${robotFmtNum(timeoutMinutes)}m`;
    } else if (progression.state === 'all_complete') {
        stateEl.textContent = 'All categories complete';
        nextEl.textContent = '--';
        deadlineEl.textContent = '--';
    } else {
        stateEl.textContent = 'No approval needed';
        nextEl.textContent = '--';
        deadlineEl.textContent = '--';
    }

    const enabled = awaitingApproval && !controllerRunning;
    button.disabled = !enabled;
    button.classList.toggle('btn-disabled', !enabled);

    robotCategoryUiState = {
        awaitingApproval,
        processRunning: controllerRunning,
        nextCategory,
        deadline,
        timeoutMinutes,
        overdue,
        allComplete: progression.state === 'all_complete',
    };
    updateRobotCategoryCountdownUi();
}

function updateRobotCategoryCountdownUi() {
    const buttonLabel = document.getElementById('robot-category-button-label');
    const noteEl = document.getElementById('robot-category-cta-note');
    const deadlineEl = document.getElementById('robot-category-deadline');
    if (!buttonLabel || !noteEl || !deadlineEl) return;

    const {
        awaitingApproval,
        processRunning,
        nextCategory,
        deadline,
        timeoutMinutes,
        overdue,
        allComplete,
    } = robotCategoryUiState;

    if (awaitingApproval) {
        const remaining = deadline ? robotFmtRemaining(deadline) : '--';
        buttonLabel.textContent = overdue ? 'Approve Next Category Now' : 'Approve Next Category';
        deadlineEl.textContent = deadline
            ? `${remaining} · timeout ${robotFmtNum(timeoutMinutes)}m`
            : `timeout ${robotFmtNum(timeoutMinutes)}m`;

        if (processRunning) {
            noteEl.textContent = nextCategory != null
                ? `Category ${robotFmtNum(nextCategory)} is queued. Approval unlocks once the current controller activity ends.`
                : 'Approval unlocks once the current controller activity ends.';
        } else if (overdue) {
            noteEl.textContent = 'Auto-progress is due now. You can approve here immediately, or the next Robot refresh should trigger it automatically.';
        } else {
            noteEl.textContent = nextCategory != null
                ? `Category ${robotFmtNum(nextCategory)} will auto-progress in ${remaining} if nobody approves it first.`
                : `Auto-progress will fire in ${remaining} if nobody approves it first.`;
        }
        return;
    }

    buttonLabel.textContent = 'Approve Next Category';
    deadlineEl.textContent = '--';
    if (allComplete) {
        noteEl.textContent = 'All scraper categories are complete. No further approval is needed.';
    } else {
        noteEl.textContent = 'Approval timing will appear here when a category finishes.';
    }
}

async function robotApproveNextCategory(autoTriggered = false, autoDeadline = null) {
    if (robotCategoryApprovalInFlight) return;
    if (!autoTriggered && !confirm(`Approve progression to the next scraper category?`)) return;
    robotCategoryApprovalInFlight = true;
    try {
        const result = await api('/control/scraper/approve-next-category', 'POST');
        if (autoTriggered && autoDeadline) {
            robotCategoryAutoApprovedDeadline = autoDeadline;
        }
        showToast(
            autoTriggered ? `Auto-progressed to the next category.` : result.message,
            result.success ? 'success' : 'error'
        );
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        robotCategoryApprovalInFlight = false;
    }
}

async function robotHandlePauseReviewAction(action, noteId) {
    if (robotPauseReviewActionInFlight) return;
    const noteEl = noteId ? document.getElementById(noteId) : null;
    const confirmMessage = action === 'hold'
        ? 'Keep the scraper paused for review and disable cycle auto-restart until you start it again?'
        : action === 'resume'
            ? 'Resume the scraper from its saved position now?'
            : action === 'pause_now'
                ? 'Pause the scraper now for review and disable cycle auto-restart until you start it again?'
                : 'Let the scraper continue while Robot keeps monitoring the next batch?';
    if (!confirm(confirmMessage)) return;

    robotPauseReviewActionInFlight = true;
    try {
        if (action === 'continue') {
            const message = 'Robot will keep monitoring the current confirmation window. No stop command was sent.';
            if (noteEl) noteEl.textContent = message;
            showToast(message, 'success');
            return;
        }

        if (noteEl) {
            noteEl.textContent = action === 'hold'
                ? 'Holding the pause and disabling cycle auto-restart...'
                : action === 'pause_now'
                    ? 'Stopping the scraper for review and disabling cycle auto-restart...'
                    : 'Resuming the scraper from its saved position...';
        }
        const endpoint = action === 'hold'
            ? '/control/scraper/review-hold'
            : action === 'pause_now'
                ? '/control/scraper/review-pause-now'
                : '/control/scraper/review-resume';
        const result = await api(endpoint, 'POST');
        const fallbackMessage = action === 'hold'
            ? 'Pause held.'
            : action === 'pause_now'
                ? 'Scraper stop requested for review.'
                : 'Scraper resumed.';
        if (noteEl) noteEl.textContent = result.message || fallbackMessage;
        showToast(result.message || fallbackMessage, 'success');
    } catch (error) {
        if (noteEl) noteEl.textContent = `Action failed: ${error.message}`;
        showToast(error.message, 'error');
    } finally {
        robotPauseReviewActionInFlight = false;
    }
}

function maybeAutoApproveCategory(data) {
    const process = data.process || {};
    const progression = data.pipeline?.maps_scraper?.category_progression || {};
    const deadline = progression.auto_progress_deadline || null;
    if (process.controller_running || !progression.awaiting_approval || !progression.auto_progress_overdue || !deadline) {
        return;
    }
    if (robotCategoryAutoApprovedDeadline === deadline) {
        return;
    }
    robotApproveNextCategory(true, deadline);
}

window.addEventListener('status-update', (e) => {
    const data = e.detail;
    renderRobotState(data);
    renderRobotCallouts(data.pipeline?.maps_scraper);
    renderRobotEnrichment(data.pipeline?.enrichment);
    renderRobotAttention(data.pipeline);
    renderAutomationMesh(data);
    renderRobotCategoryProgression(data);
    maybeAutoApproveCategory(data);
});

const robotApproveButton = document.getElementById('btn-robot-approve-category');
if (robotApproveButton) {
    robotApproveButton.addEventListener('click', () => robotApproveNextCategory(false));
}

const robotCalloutsList = document.getElementById('robot-callouts-list');
if (robotCalloutsList) {
    robotCalloutsList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-review-action]');
        if (!button) return;
        robotHandlePauseReviewAction(
            button.getAttribute('data-review-action'),
            button.getAttribute('data-review-note-id')
        );
    });
}

if (!robotCategoryCountdownTimer) {
    robotCategoryCountdownTimer = window.setInterval(() => {
        if (robotCategoryUiState.awaitingApproval) {
            updateRobotCategoryCountdownUi();
        }
    }, 1000);
}

lucide.createIcons();

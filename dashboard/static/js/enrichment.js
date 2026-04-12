/**
 * Enrichment Page - tabs and LinkedIn enrichment live surface
 * Depends on common.js (api, showToast, formatTimestamp)
 */

(function() {
    const tabs = Array.from(document.querySelectorAll('.enrichment-tab'));
    const panels = Array.from(document.querySelectorAll('.enrichment-tab-panel'));
    const STORAGE_KEY = 'enrichment_active_tab';
    const DEFAULT_TAB = 'linkedin';

    function fmtNum(value) {
        if (value === null || value === undefined || value === '') return '--';
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed.toLocaleString() : String(value);
    }

    function fmtPct(value) {
        if (value === null || value === undefined || value === '') return '--';
        const parsed = Number(value);
        return Number.isFinite(parsed) ? `${parsed.toFixed(1)}%` : String(value);
    }

    function fmtAgeMinutes(value) {
        if (value === null || value === undefined || value === '') return '--';
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return '--';
        if (parsed < 1) return '<1m';
        if (parsed < 60) return `${Math.round(parsed)}m`;
        return `${Math.floor(parsed / 60)}h ${Math.round(parsed % 60)}m`;
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

    function fmtSeconds(value) {
        if (value === null || value === undefined || value === '') return '--';
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return '--';
        if (parsed < 60) return `${parsed.toFixed(1)}s`;
        const mins = Math.floor(parsed / 60);
        const secs = Math.round(parsed % 60);
        return `${mins}m ${secs}s`;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value ?? '--';
    }

    function setHtml(id, value) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = value ?? '--';
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

    function setActiveTab(tabName, updateHash = true) {
        const safeTab = panels.some((panel) => panel.dataset.enrichmentPanel === tabName) ? tabName : DEFAULT_TAB;

        tabs.forEach((tab) => {
            const isActive = tab.dataset.enrichmentTab === safeTab;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        panels.forEach((panel) => {
            const isActive = panel.dataset.enrichmentPanel === safeTab;
            panel.classList.toggle('is-active', isActive);
            panel.hidden = !isActive;
        });

        localStorage.setItem(STORAGE_KEY, safeTab);
        if (updateHash) history.replaceState(null, '', `#${safeTab}`);
    }

    function watcherStateText(enrichment) {
        if (enrichment.is_stale) return 'Stale lock';
        if (enrichment.locked) return 'Draining';
        if (enrichment.trigger_state === 'timeout_ready') return 'Forced drain due';
        if (enrichment.trigger_state === 'threshold_ready') return 'Ready to drain';
        if ((enrichment.pending || 0) > 0) return 'Watching backlog';
        return 'Idle';
    }

    function renderEnrichmentStatus(data) {
        const pl = data.pipeline || {};
        const enrichment = pl.enrichment || {};
        const attention = Array.isArray(pl.attention) ? pl.attention : [];
        const sheetVolume = Array.isArray(pl.sheet_volume) ? pl.sheet_volume : [];
        const validatedSheet = sheetVolume.find((entry) => entry.key === 'validated') || {};

        const pending = enrichment.pending ?? null;
        const threshold = enrichment.trigger_threshold ?? null;
        const eligible = enrichment.eligible_live ?? null;
        const checked = enrichment.checked ?? null;
        const found = enrichment.found ?? null;
        const person = enrichment.person ?? null;
        const none = enrichment.none ?? null;
        const employees = enrichment.employees ?? null;
        const stateText = watcherStateText(enrichment);
        const runInitialPending = enrichment.run_initial_pending ?? null;
        const runProcessedRows = enrichment.run_processed_rows ?? null;
        const runRemainingEstimate = enrichment.run_remaining_estimate ?? null;
        const runBatchesCompleted = enrichment.run_batches_completed ?? null;
        const runCurrentBatchTotal = enrichment.run_current_batch_total ?? null;
        const runCurrentBatchProcessed = enrichment.run_current_batch_processed ?? null;

        setText('enrich-summary-state', stateText);
        setText('enrich-summary-backlog', pending != null && threshold != null ? `${fmtNum(pending)} / ${fmtNum(threshold)}` : fmtNum(pending));
        setText('enrich-summary-coverage', enrichment.coverage_rate != null ? fmtPct(enrichment.coverage_rate) : '--');
        setText('enrich-summary-employees', enrichment.employee_completion_rate != null ? fmtPct(enrichment.employee_completion_rate) : '--');

        const stateChipEl = document.getElementById('enrich-state-chip');
        const stateTone = enrichment.is_stale
            ? 'danger'
            : (enrichment.locked || enrichment.trigger_state === 'threshold_ready' || enrichment.trigger_state === 'timeout_ready'
                ? 'warning'
                : 'info');
        setChip(stateChipEl, stateText, stateTone);

        setText('enrich-pending', fmtNum(pending));
        setText(
            'enrich-backlog-share',
            eligible != null && eligible > 0 && pending != null
                ? fmtPct((Number(pending) / Number(eligible)) * 100)
                : '--'
        );
        setText(
            'enrich-wait-window',
            enrichment.waiting_minutes != null && enrichment.max_wait_minutes != null
                ? `${fmtAgeMinutes(enrichment.waiting_minutes)} / ${fmtAgeMinutes(enrichment.max_wait_minutes)}`
                : (enrichment.max_wait_minutes != null ? fmtAgeMinutes(enrichment.max_wait_minutes) : '--')
        );
        setText(
            'enrich-drain-progress',
            runInitialPending != null && runProcessedRows != null
                ? `${fmtNum(runProcessedRows)} / ${fmtNum(runInitialPending)}`
                : '--'
        );
        setText('enrich-drain-remaining', fmtNum(runRemainingEstimate));

        setText('enrich-checked', fmtNum(checked));
        setText('enrich-coverage-rate', enrichment.coverage_rate != null ? fmtPct(enrichment.coverage_rate) : '--');
        setHtml(
            'enrich-quality',
            (found != null || person != null || none != null)
                ? `<span class="pipeline-main">${fmtNum(found)} · ${fmtNum(person)} · ${fmtNum(none)}</span><span class="pipeline-sub">FOUND · PERSON_ONLY · NONE</span>`
                : '--'
        );
        setHtml(
            'enrich-employee-fill',
            (employees != null || found != null)
                ? `<span class="pipeline-main">${fmtNum(employees)} / ${fmtNum(found)}</span><span class="pipeline-sub">${enrichment.employee_completion_rate != null ? `${fmtPct(enrichment.employee_completion_rate)} of FOUND rows` : 'Employee completion unavailable'}</span>`
                : '--'
        );

        const qualityChipEl = document.getElementById('enrich-quality-chip');
        let qualityTone = 'info';
        let qualityText = 'Monitoring';
        if (enrichment.coverage_rate != null && enrichment.coverage_rate < 35) {
            qualityTone = 'warning';
            qualityText = 'Low coverage';
        } else if (none != null && checked != null && checked > 0 && (none / checked) >= 0.5) {
            qualityTone = 'warning';
            qualityText = 'High NONE rate';
        } else if (enrichment.employee_completion_rate != null && enrichment.employee_completion_rate < 40) {
            qualityTone = 'warning';
            qualityText = 'Employee gaps';
        }
        setChip(qualityChipEl, qualityText, qualityTone);

        const watcherChipEl = document.getElementById('enrich-watcher-chip');
        const watcherText = enrichment.locked
            ? 'Watcher active'
            : enrichment.is_stale
                ? 'Watcher stale'
                : enrichment.last_tick_at
                    ? `Last tick ${fmtAgo(enrichment.last_tick_at)}`
                    : 'Watcher idle';
        const watcherTone = enrichment.is_stale ? 'danger' : (enrichment.locked ? 'warning' : 'info');
        setChip(watcherChipEl, watcherText, watcherTone);

        setText('enrich-lock-age', enrichment.lock_age != null ? fmtAgeMinutes(enrichment.lock_age) : '--');
        setText('enrich-last-trigger', enrichment.last_trigger ? String(enrichment.last_trigger).replaceAll('_', ' ') : '--');
        setText('enrich-last-tick', enrichment.last_tick_at ? `${formatTimestamp(enrichment.last_tick_at)} (${fmtAgo(enrichment.last_tick_at)})` : '--');
        setText('enrich-last-launch', enrichment.last_launch_at ? `${formatTimestamp(enrichment.last_launch_at)} (${fmtAgo(enrichment.last_launch_at)})` : '--');
        setHtml(
            'enrich-watcher-runtime',
            `<span class="pipeline-main">${enrichment.reason ? escapeHtml(enrichment.reason) : 'watching'}</span><span class="pipeline-sub">${enrichment.pid != null ? `pid ${fmtNum(enrichment.pid)}` : 'pid unavailable'}</span>`
        );

        const thresholdPct = pending != null && threshold ? Math.max(0, Math.min((Number(pending) / Number(threshold)) * 100, 100)) : 0;
        const thresholdFillEl = document.getElementById('enrich-threshold-fill');
        setText('enrich-threshold-meta', pending != null && threshold != null ? `${fmtNum(pending)} / ${fmtNum(threshold)}` : '--');
        if (thresholdFillEl) {
            thresholdFillEl.style.width = `${thresholdPct}%`;
            thresholdFillEl.classList.remove('is-warning', 'is-danger');
            if (enrichment.is_stale || enrichment.trigger_state === 'timeout_ready') thresholdFillEl.classList.add('is-danger');
            else if (enrichment.trigger_state === 'threshold_ready' || thresholdPct >= 75) thresholdFillEl.classList.add('is-warning');
        }

        const batchPct = runCurrentBatchTotal != null && Number(runCurrentBatchTotal) > 0
            ? Math.max(0, Math.min((Number(runCurrentBatchProcessed || 0) / Number(runCurrentBatchTotal)) * 100, 100))
            : 0;
        setText(
            'enrich-batch-progress-meta',
            runCurrentBatchTotal != null && Number(runCurrentBatchTotal) > 0
                ? `${fmtNum(runCurrentBatchProcessed)} / ${fmtNum(runCurrentBatchTotal)}`
                : '--'
        );
        const batchFillEl = document.getElementById('enrich-batch-progress-fill');
        if (batchFillEl) {
            batchFillEl.style.width = `${batchPct}%`;
            batchFillEl.classList.remove('is-warning', 'is-danger');
            if (enrichment.run_active) batchFillEl.classList.add('is-warning');
        }

        let thresholdNote = 'Waiting for enrichment telemetry';
        if (enrichment.is_stale) {
            thresholdNote = `Stale lock for ${fmtAgeMinutes(enrichment.lock_age)}. Review the enricher before trusting backlog movement.`;
        } else if (enrichment.locked) {
            thresholdNote = `Enricher launched${enrichment.last_trigger ? ` via ${enrichment.last_trigger}` : ''}${enrichment.last_launch_at ? ` ${fmtAgo(enrichment.last_launch_at)}` : ''}.`;
        } else if (enrichment.trigger_state === 'threshold_ready') {
            thresholdNote = 'Backlog reached the drain threshold. The next watcher tick should launch enrichment.';
        } else if (enrichment.trigger_state === 'timeout_ready') {
            thresholdNote = `Backlog has waited ${fmtAgeMinutes(enrichment.waiting_minutes)}. The next watcher tick should force a drain.`;
        } else if ((enrichment.pending || 0) > 0 && enrichment.waiting_minutes != null && enrichment.max_wait_minutes != null) {
            thresholdNote = `Watching backlog for ${fmtAgeMinutes(enrichment.waiting_minutes)} of ${fmtAgeMinutes(enrichment.max_wait_minutes)} before forced drain.`;
        } else if ((enrichment.pending || 0) > 0) {
            thresholdNote = 'Pending live rows are building below the drain threshold.';
        } else if (enrichment.last_tick_at) {
            thresholdNote = `No live rows currently need first-pass LinkedIn lookup. Last checked ${fmtAgo(enrichment.last_tick_at)}.`;
        } else {
            thresholdNote = 'No live rows currently need first-pass LinkedIn lookup.';
        }
        setText('enrich-threshold-note', thresholdNote);

        let batchNote = 'Batch progress will appear during active drain runs.';
        if (enrichment.run_active && runCurrentBatchTotal != null && Number(runCurrentBatchTotal) > 0) {
            batchNote = `${fmtNum(runCurrentBatchProcessed)} of ${fmtNum(runCurrentBatchTotal)} rows committed in the current batch.`;
            if (runBatchesCompleted != null) {
                batchNote += ` ${fmtNum(runBatchesCompleted)} batches completed so far.`;
            }
        } else if (enrichment.run_active) {
            batchNote = 'Drain run is active. Waiting for the first batch-progress flush.';
        } else if (runProcessedRows != null && runInitialPending != null && runInitialPending > 0) {
            batchNote = `Last drain processed ${fmtNum(runProcessedRows)} of ${fmtNum(runInitialPending)} starting rows.`;
            if (runRemainingEstimate != null) {
                batchNote += ` Remaining estimate: ${fmtNum(runRemainingEstimate)}.`;
            }
        }
        setText('enrich-batch-progress-note', batchNote);

        const sheetChipEl = document.getElementById('enrich-sheet-chip');
        const sheetState = validatedSheet.state || 'normal';
        const sheetTone = sheetState === 'danger' ? 'danger' : (sheetState === 'warning' ? 'warning' : 'info');
        setChip(sheetChipEl, sheetState === 'danger' ? 'Near cap' : sheetState === 'warning' ? 'Watching cap' : 'Healthy rows', sheetTone);
        setText('enrich-sheet-tab', validatedSheet.tab_name || '--');
        setText('enrich-sheet-rows', validatedSheet.current_rows != null ? fmtNum(validatedSheet.current_rows) : '--');
        setText('enrich-sheet-data-rows', validatedSheet.data_rows != null ? fmtNum(validatedSheet.data_rows) : '--');
        setHtml(
            'enrich-sheet-note',
            `<span class="pipeline-main">${validatedSheet.note ? escapeHtml(validatedSheet.note) : 'No row-cap note'}</span><span class="pipeline-sub">${validatedSheet.stop_threshold != null ? `cap ${fmtNum(validatedSheet.stop_threshold)}` : 'Cap unavailable'}</span>`
        );

        const relevantAttention = attention.filter((item) => {
            const source = String(item.source || '').toLowerCase();
            return source.includes('enrichment');
        });
        const attentionListEl = document.getElementById('enrich-attention-list');
        if (attentionListEl) {
            if (!relevantAttention.length && !enrichment.is_stale && !enrichment.locked && !((enrichment.pending || 0) > 0)) {
                attentionListEl.innerHTML = '<div class="attention-empty">No enrichment-specific attention yet.</div>';
            } else {
                const synthetic = [];
                if (enrichment.is_stale) {
                    synthetic.push({ level: 'danger', source: 'Enrichment', message: 'Watcher lock is stale and needs operator review.' });
                } else if (enrichment.trigger_state === 'threshold_ready') {
                    synthetic.push({ level: 'warning', source: 'Enrichment', message: 'Backlog threshold reached; the next watcher tick should launch a drain.' });
                } else if (enrichment.trigger_state === 'timeout_ready') {
                    synthetic.push({ level: 'warning', source: 'Enrichment', message: 'Forced-drain timeout reached; the next watcher tick should launch enrichment.' });
                }
                const items = [...synthetic, ...relevantAttention];
                const itemsHtml = items.map((item) => `
                    <div class="attention-item attn-${item.level === 'danger' ? 'danger' : item.level === 'warning' ? 'warning' : 'info'}">
                        <span class="attn-dot"></span>
                        <span class="attn-source">${escapeHtml(item.source || 'Enrichment')}</span>
                        <span class="attn-msg">${escapeHtml(item.message || '')}</span>
                    </div>
                `);
                renderAttentionList(attentionListEl, itemsHtml, { title: 'Enrichment attention' });
            }
        }
    }

    function renderKeywordScoringStatus(data) {
        const pl = data.pipeline || {};
        const keyword = pl.keyword_detection || {};
        const scoring = pl.scoring || {};
        const attention = Array.isArray(pl.attention) ? pl.attention : [];
        const sheetVolume = Array.isArray(pl.sheet_volume) ? pl.sheet_volume : [];
        const ultraSheet = sheetVolume.find((entry) => entry.key === 'ultra') || {};

        const pending = Number(keyword.pending || 0);
        const cat1 = Number(keyword.cat1 || 0);
        const cat2 = Number(keyword.cat2 || 0);
        const cat3 = Number(keyword.cat3 || 0);
        const decided = cat1 + cat2 + cat3;
        const threshold = Number(keyword.threshold || 0);
        const ready = Number(scoring.ready || 0);
        const moved = Number(scoring.moved || 0);
        const alpha = Number(scoring.alpha || 0);
        const alphaHigh = Number(scoring.alpha_high || 0);
        const beta = Number(scoring.beta || 0);
        const betaHigh = Number(scoring.beta_high || 0);
        const gamma = Number(scoring.gamma || 0);
        const gammaHigh = Number(scoring.gamma_high || 0);
        const iGamma = Number(scoring.i_gamma || 0);
        const pbin = Number(scoring.pbin || 0);
        const hydro = Number(scoring.hydro || 0);
        const hydroFormula = Number(scoring.hydro_formula || 0);
        const watcherState = keyword.is_stale ? 'Stale lock' : keyword.locked ? 'Running' : pending >= threshold && threshold > 0 ? 'Ready to run' : pending > 0 ? 'Watching backlog' : 'Idle';

        setText('keyword-summary-state', watcherState);
        setText('keyword-summary-pending', fmtNum(pending));
        setText('keyword-summary-ready', fmtNum(ready));
        setText('keyword-summary-moved', fmtNum(moved));

        const stateChip = document.getElementById('keyword-state-chip');
        setChip(stateChip, watcherState, keyword.is_stale ? 'danger' : (keyword.locked || pending >= threshold) ? 'warning' : 'info');
        setText('keyword-pending', fmtNum(pending));
        setHtml(
            'keyword-outcomes',
            `<span class="pipeline-main">${fmtNum(cat1)} · ${fmtNum(cat2)} · ${fmtNum(cat3)}</span><span class="pipeline-sub">Cat 1 · Cat 2 · Cat 3</span>`
        );
        setText(
            'keyword-coverage',
            (pending + decided) > 0
                ? fmtPct((decided / (pending + decided)) * 100)
                : '--'
        );

        setText('keyword-threshold-meta', threshold > 0 ? `${fmtNum(pending)} / ${fmtNum(threshold)}` : fmtNum(pending));
        const thresholdPct = threshold > 0 ? Math.max(0, Math.min((pending / threshold) * 100, 100)) : 0;
        const thresholdFill = document.getElementById('keyword-threshold-fill');
        if (thresholdFill) {
            thresholdFill.style.width = `${thresholdPct}%`;
            thresholdFill.classList.remove('is-warning', 'is-danger');
            if (keyword.is_stale) thresholdFill.classList.add('is-danger');
            else if (pending >= threshold || thresholdPct >= 75) thresholdFill.classList.add('is-warning');
        }
        let thresholdNote = 'No pending keyword backlog right now.';
        if (keyword.is_stale) {
            thresholdNote = `Ultra watcher lock is stale${keyword.lock_age != null ? ` (${fmtAgeMinutes(keyword.lock_age)})` : ''}.`;
        } else if (keyword.locked) {
            thresholdNote = 'Keyword detector and scoring pipeline are running right now.';
        } else if (threshold > 0 && pending >= threshold) {
            thresholdNote = 'Keyword backlog reached the watcher threshold. The next tick should launch the ultra pipeline.';
        } else if (pending > 0 && threshold > 0) {
            thresholdNote = `${fmtNum(pending)} rows are waiting for keyword detection, below the ${fmtNum(threshold)}-row trigger.`;
        }
        setText('keyword-threshold-note', thresholdNote);

        const scoringChip = document.getElementById('keyword-scoring-chip');
        let scoringTone = 'info';
        let scoringText = 'Monitoring';
        if (ready > 0) {
            scoringTone = 'warning';
            scoringText = 'Ready to classify';
        } else if (moved > 0) {
            scoringText = 'Movement recorded';
        }
        setChip(scoringChip, scoringText, scoringTone);
        setText('keyword-ready', fmtNum(ready));
        setText('keyword-moved', fmtNum(moved));
        setHtml(
            'keyword-settlement',
            `<span class="pipeline-main">${fmtNum(alpha)} · ${fmtNum(beta)} · ${fmtNum(gamma)}</span><span class="pipeline-sub">Base</span><span class="pipeline-main">${fmtNum(alphaHigh)} · ${fmtNum(betaHigh)} · ${fmtNum(gammaHigh)}</span><span class="pipeline-sub">High confidence</span>`
        );
        setText(
            'keyword-moved-share',
            (ready + moved) > 0
                ? fmtPct((moved / (ready + moved)) * 100)
                : '--'
        );
        setChip(document.getElementById('keyword-dest-chip'), 'Sheet truth', 'info');
        setText('keyword-dest-alpha', fmtNum(alpha));
        setText('keyword-dest-alpha-high', fmtNum(alphaHigh));
        setText('keyword-dest-beta', fmtNum(beta));
        setText('keyword-dest-beta-high', fmtNum(betaHigh));
        setText('keyword-dest-gamma', fmtNum(gamma));
        setText('keyword-dest-gamma-high', fmtNum(gammaHigh));
        setText('keyword-dest-i-gamma', fmtNum(iGamma));
        setText('keyword-dest-pbin', fmtNum(pbin));
        setText('keyword-dest-hydro', fmtNum(hydro));
        setText('keyword-dest-hydro-formula', fmtNum(hydroFormula));

        const watcherChip = document.getElementById('keyword-watcher-chip');
        setChip(watcherChip, watcherState, keyword.is_stale ? 'danger' : keyword.locked ? 'warning' : 'info');
        setText('keyword-watcher-state', watcherState);
        setText('keyword-lock-age', keyword.lock_age != null ? fmtAgeMinutes(keyword.lock_age) : '--');
        setText('keyword-ultra-rows', ultraSheet.data_rows != null ? fmtNum(ultraSheet.data_rows) : '--');
        setHtml(
            'keyword-ultra-note',
            `<span class="pipeline-main">${ultraSheet.note ? escapeHtml(ultraSheet.note) : 'No ultra-sheet note'}</span><span class="pipeline-sub">${ultraSheet.tab_name ? escapeHtml(ultraSheet.tab_name) : 'Ultra_validated'}</span>`
        );

        const keywordAttention = attention.filter((item) => {
            const source = String(item.source || '').toLowerCase();
            return source.includes('keyword') || source.includes('ultra');
        });
        const attentionEl = document.getElementById('keyword-attention-list');
        const attentionChip = document.getElementById('keyword-attention-chip');
        const synthetic = [];
        if (keyword.is_stale) {
            synthetic.push({ level: 'danger', source: 'Keywords', message: 'Ultra watcher lock is stale and needs review.' });
        } else if (threshold > 0 && pending >= threshold && !keyword.locked) {
            synthetic.push({ level: 'warning', source: 'Keywords', message: 'Keyword backlog reached threshold; the next watcher tick should run the pipeline.' });
        } else if (ready > 0) {
            synthetic.push({ level: 'info', source: 'Scoring', message: `${fmtNum(ready)} classified rows are ready to move and mark as MOVED.` });
        }
        const items = [...synthetic, ...keywordAttention];
        if (!items.length) {
            setChip(attentionChip, 'No active issues', 'info');
            if (attentionEl) attentionEl.innerHTML = '<div class="attention-empty">No keyword/scoring attention yet.</div>';
            return;
        }
        const hasDanger = items.some((item) => item.level === 'danger');
        const hasWarning = items.some((item) => item.level === 'warning');
        setChip(attentionChip, hasDanger ? 'Critical attention needed' : hasWarning ? 'Warnings to review' : 'Informational prompts', hasDanger ? 'danger' : hasWarning ? 'warning' : 'info');
        const itemsHtml = items.map((item) => `
            <div class="attention-item attn-${item.level === 'danger' ? 'danger' : item.level === 'warning' ? 'warning' : 'info'}">
                <span class="attn-dot"></span>
                <span class="attn-source">${escapeHtml(item.source || 'Keywords')}</span>
                <span class="attn-msg">${escapeHtml(item.message || '')}</span>
            </div>
        `);
        renderAttentionList(attentionEl, itemsHtml, { title: 'Keyword & scoring attention' });
    }

    function renderScraperV3Status(data) {
        const pl = data.pipeline || {};
        const scraperV3 = pl.scraper_v3 || {};
        const attention = Array.isArray(pl.attention) ? pl.attention : [];

        const alpha = Number(scraperV3.alpha || 0);
        const alphaHigh = Number(scraperV3.alpha_high || 0);
        const beta = Number(scraperV3.beta || 0);
        const betaHigh = Number(scraperV3.beta_high || 0);
        const gamma = Number(scraperV3.gamma || 0);
        const gammaHigh = Number(scraperV3.gamma_high || 0);
        const alphaFamily = Number(scraperV3.alpha_family || 0);
        const betaFamily = Number(scraperV3.beta_family || 0);
        const gammaFamily = Number(scraperV3.gamma_family || 0);
        const alphaBeta = Number(scraperV3.alpha_beta || 0);
        const total = Number(scraperV3.total || 0);
        const threshold = Number(scraperV3.threshold || 0);

        const stateText = scraperV3.is_stale
            ? 'Stale lock'
            : scraperV3.locked
                ? 'Running'
                : alphaBeta >= threshold && threshold > 0
                    ? 'Ready to run'
                    : total > 0
                        ? 'Watching backlog'
                        : 'Idle';
        const stateTone = scraperV3.is_stale ? 'danger' : (scraperV3.locked || alphaBeta >= threshold) ? 'warning' : 'info';

        setText('scraperv3-summary-state', stateText);
        setText(
            'scraperv3-summary-alpha-beta',
            threshold > 0 ? `${fmtNum(alphaBeta)} / ${fmtNum(threshold)}` : fmtNum(alphaBeta)
        );
        setText('scraperv3-summary-total', fmtNum(total));
        setText('scraperv3-summary-written', fmtNum(scraperV3.last_written));

        setChip(document.getElementById('scraperv3-state-chip'), stateText, stateTone);
        setHtml(
            'scraperv3-alpha-family',
            `<span class="pipeline-main">${fmtNum(alphaFamily)}</span><span class="pipeline-sub">${fmtNum(alpha)} base · ${fmtNum(alphaHigh)} high</span>`
        );
        setHtml(
            'scraperv3-beta-family',
            `<span class="pipeline-main">${fmtNum(betaFamily)}</span><span class="pipeline-sub">${fmtNum(beta)} base · ${fmtNum(betaHigh)} high</span>`
        );
        setHtml(
            'scraperv3-gamma-family',
            `<span class="pipeline-main">${fmtNum(gammaFamily)}</span><span class="pipeline-sub">${fmtNum(gamma)} base · ${fmtNum(gammaHigh)} high</span>`
        );

        setText(
            'scraperv3-threshold-meta',
            threshold > 0
                ? `${fmtNum(alphaBeta)} / ${fmtNum(threshold)} A+B${total > 0 ? ` · ${fmtNum(total)} total` : ''}`
                : `${fmtNum(alphaBeta)}${total > 0 ? ` A+B · ${fmtNum(total)} total` : ''}`
        );
        const thresholdPct = threshold > 0 ? Math.max(0, Math.min((alphaBeta / threshold) * 100, 100)) : 0;
        const thresholdFill = document.getElementById('scraperv3-threshold-fill');
        if (thresholdFill) {
            thresholdFill.style.width = `${thresholdPct}%`;
            thresholdFill.classList.remove('is-warning', 'is-danger');
            if (scraperV3.is_stale) thresholdFill.classList.add('is-danger');
            else if (scraperV3.locked || alphaBeta >= threshold || thresholdPct >= 75) thresholdFill.classList.add('is-warning');
        }
        let thresholdNote = 'No source backlog is waiting for Scraper V3.';
        if (scraperV3.source_webhook_ok === false) {
            thresholdNote = `Source webhook unreachable — counts unavailable. Error: ${scraperV3.source_webhook_error || 'unknown'}`;
        } else if (scraperV3.is_stale) {
            thresholdNote = `Scraper V3 lock is stale${scraperV3.lock_age != null ? ` (${fmtAgeMinutes(scraperV3.lock_age)})` : ''}.`;
        } else if (scraperV3.locked) {
            thresholdNote = 'Scraper V3 is currently running against the source sheet backlog.';
        } else if (threshold > 0 && alphaBeta >= threshold) {
            thresholdNote = 'Alpha+Beta family backlog reached the launch threshold. The next watcher tick should trigger Scraper V3.';
        } else if (total > 0) {
            thresholdNote = `Threshold uses Alpha+Beta only: ${fmtNum(alphaBeta)} / ${fmtNum(threshold)}. Gamma rows (${fmtNum(gammaFamily)}) join the batch after a run starts, but do not trigger it by themselves.`;
        }
        setText('scraperv3-threshold-note', thresholdNote);

        const outputTone = scraperV3.last_finished
            ? (
                Number(scraperV3.last_scraped_error || 0) > 0 ||
                Number(scraperV3.last_timeout_count || 0) > 0 ||
                Number(scraperV3.last_write_failed || 0) > 0 ||
                Number(scraperV3.last_mark_failed || 0) > 0 ||
                Number(scraperV3.last_marked_error || 0) > 0
                    ? 'warning'
                    : 'info'
            )
            : 'info';
        setChip(
            document.getElementById('scraperv3-output-chip'),
            scraperV3.last_finished ? `Finished ${fmtAgo(scraperV3.last_finished)}` : 'No recent run',
            outputTone
        );
        setText(
            'scraperv3-last-finished',
            scraperV3.last_finished ? `${formatTimestamp(scraperV3.last_finished)} (${fmtAgo(scraperV3.last_finished)})` : '--'
        );
        setHtml(
            'scraperv3-batch-written',
            `<span class="pipeline-main">${fmtNum(scraperV3.last_batch)} batch · ${fmtNum(scraperV3.last_written)} written</span><span class="pipeline-sub">${fmtNum(scraperV3.last_attempted)} attempted · ${fmtNum(scraperV3.last_resumed_mark_only)} mark-only resumes</span>`
        );
        setHtml(
            'scraperv3-write-mark-health',
            `<span class="pipeline-main">${fmtNum(scraperV3.last_write_failed)} write fail · ${fmtNum(scraperV3.last_mark_failed)} mark fail</span><span class="pipeline-sub">${fmtNum(scraperV3.last_marked_done)} marked done · ${fmtNum(scraperV3.last_marked_error)} marked error · ${scraperV3.last_graceful_shutdown ? 'graceful shutdown' : 'full completion'}</span>`
        );
        setHtml(
            'scraperv3-scraped-outcome',
            `<span class="pipeline-main">${fmtNum(scraperV3.last_scraped_ok)} ok · ${fmtNum(scraperV3.last_scraped_error)} error</span><span class="pipeline-sub">${fmtNum(scraperV3.last_timeout_count)} timeouts · ${fmtNum(scraperV3.last_blocked_count)} blocked</span>`
        );
        setHtml(
            'scraperv3-extracts',
            `<span class="pipeline-main">${fmtNum(scraperV3.last_linkedin_urls)} LI · ${fmtNum(scraperV3.last_emails)} emails · ${fmtNum(scraperV3.last_persons_extracted)} persons</span><span class="pipeline-sub">Latest run summary</span>`
        );

        setChip(document.getElementById('scraperv3-watcher-chip'), stateText, stateTone);
        setText('scraperv3-lock-age', scraperV3.lock_age != null ? fmtAgeMinutes(scraperV3.lock_age) : '--');
        setHtml(
            'scraperv3-reason',
            `<span class="pipeline-main">${scraperV3.reason ? escapeHtml(scraperV3.reason) : 'watching_source_backlog'}</span><span class="pipeline-sub">${scraperV3.locked ? 'active process lock' : 'no active lock'}</span>`
        );
        setHtml(
            'scraperv3-timing',
            `<span class="pipeline-main">${fmtSeconds(scraperV3.last_elapsed_seconds)} total</span><span class="pipeline-sub">${fmtSeconds(scraperV3.last_per_site_seconds)} per site</span>`
        );
        setHtml(
            'scraperv3-watch-window',
            `<span class="pipeline-main">${fmtAgeMinutes(scraperV3.max_wait_minutes)} forced wait</span><span class="pipeline-sub">${fmtAgeMinutes(scraperV3.watcher_interval_minutes)} watcher interval · stale after ${fmtAgeMinutes(scraperV3.stale_lock_timeout_minutes)}</span>`
        );

        setChip(document.getElementById('scraperv3-lanes-chip'), 'Source Apps Script', 'info');
        setText('scraperv3-alpha-split', `${fmtNum(alpha)} / ${fmtNum(alphaHigh)}`);
        setText('scraperv3-beta-split', `${fmtNum(beta)} / ${fmtNum(betaHigh)}`);
        setText('scraperv3-gamma-split', `${fmtNum(gamma)} / ${fmtNum(gammaHigh)}`);
        setHtml(
            'scraperv3-mark-only',
            `<span class="pipeline-main">${fmtNum(scraperV3.last_resumed_mark_only)}</span><span class="pipeline-sub">Rows resumed directly into mark-processed flow on the latest run</span>`
        );

        const relevantAttention = attention.filter((item) => {
            const source = String(item.source || '').toLowerCase();
            return source.includes('scraper v3');
        });
        const attentionEl = document.getElementById('scraperv3-attention-list');
        const attentionChip = document.getElementById('scraperv3-attention-chip');
        const synthetic = [];
        if (scraperV3.source_webhook_ok === false) {
            synthetic.push({ level: 'danger', source: 'Scraper V3', message: `Source webhook unreachable — backlog counts are stale. Error: ${scraperV3.source_webhook_error || 'unknown'}` });
        }
        if (scraperV3.is_stale) {
            synthetic.push({ level: 'danger', source: 'Scraper V3', message: 'Watcher lock is stale and needs operator review.' });
        } else if (threshold > 0 && alphaBeta >= threshold && !scraperV3.locked) {
            synthetic.push({ level: 'warning', source: 'Scraper V3', message: 'Alpha+Beta family backlog reached the launch threshold.' });
        } else if (total > 0 && alphaBeta < threshold) {
            synthetic.push({ level: 'info', source: 'Scraper V3', message: 'Source backlog exists below threshold; watcher is waiting for threshold or timeout.' });
        }
        if (Number(scraperV3.last_scraped_error || 0) > 0 || Number(scraperV3.last_timeout_count || 0) > 0) {
            synthetic.push({ level: 'warning', source: 'Scraper V3', message: `Latest run had ${fmtNum(scraperV3.last_scraped_error)} scrape errors and ${fmtNum(scraperV3.last_timeout_count)} timeouts.` });
        }
        if (Number(scraperV3.last_write_failed || 0) > 0 || Number(scraperV3.last_mark_failed || 0) > 0 || Number(scraperV3.last_marked_error || 0) > 0) {
            synthetic.push({ level: 'warning', source: 'Scraper V3', message: `Latest run had ${fmtNum(scraperV3.last_write_failed)} write failures, ${fmtNum(scraperV3.last_mark_failed)} mark failures, and ${fmtNum(scraperV3.last_marked_error)} mark-error statuses.` });
        }
        if (scraperV3.last_graceful_shutdown) {
            synthetic.push({ level: 'info', source: 'Scraper V3', message: 'Latest run exited via graceful shutdown after draining active sites.' });
        }
        const items = [...synthetic, ...relevantAttention];
        if (!items.length) {
            setChip(attentionChip, 'No active issues', 'info');
            if (attentionEl) attentionEl.innerHTML = '<div class="attention-empty">No Scraper V3-specific attention yet.</div>';
            return;
        }
        const hasDanger = items.some((item) => item.level === 'danger');
        const hasWarning = items.some((item) => item.level === 'warning');
        setChip(attentionChip, hasDanger ? 'Critical attention needed' : hasWarning ? 'Warnings to review' : 'Informational prompts', hasDanger ? 'danger' : hasWarning ? 'warning' : 'info');
        const itemsHtml = items.map((item) => `
            <div class="attention-item attn-${item.level === 'danger' ? 'danger' : item.level === 'warning' ? 'warning' : 'info'}">
                <span class="attn-dot"></span>
                <span class="attn-source">${escapeHtml(item.source || 'Scraper V3')}</span>
                <span class="attn-msg">${escapeHtml(item.message || '')}</span>
            </div>
        `);
        renderAttentionList(attentionEl, itemsHtml, { title: 'Scraper V3 attention' });
    }

    async function refreshEnrichmentLogs() {
        const contentEl = document.getElementById('enrich-log-content');
        if (!contentEl) return;
        try {
            const data = await api('/logs?lines=400');
            const lines = Array.isArray(data.lines) ? data.lines : [];
            const filtered = lines.filter((line) => /enrich|linkedin|validated|watcher/i.test(line)).slice(-80);
            if (data.error) {
                contentEl.textContent = `Error: ${data.error}`;
            } else if (!filtered.length) {
                contentEl.textContent = 'No recent enrichment-specific log lines.';
            } else {
                contentEl.textContent = filtered.join('\n');
                const viewer = contentEl.parentElement;
                if (viewer) viewer.scrollTop = viewer.scrollHeight;
            }
        } catch (error) {
            contentEl.textContent = `Failed to load enrichment logs: ${error.message}`;
        }
    }

    async function runEnrichmentControl(endpoint, noteId, successMessage, confirmMessage = null) {
        const note = document.getElementById(noteId);
        if (confirmMessage && !confirmAction(confirmMessage)) return;
        try {
            if (note) note.textContent = 'Running LinkedIn enrichment action...';
            const result = await api(endpoint, 'POST');
            if (note) {
                note.textContent = result.output
                    ? `${result.message || successMessage} ${result.output}`
                    : (result.message || successMessage);
            }
            showToast(result.message || successMessage, 'success');
        } catch (error) {
            if (note) note.textContent = `Action failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    tabs.forEach((tab) => {
        tab.addEventListener('click', () => setActiveTab(tab.dataset.enrichmentTab));
    });

    const refreshLogsBtn = document.getElementById('btn-refresh-enrichment-logs');
    if (refreshLogsBtn) {
        refreshLogsBtn.addEventListener('click', async () => {
            await refreshEnrichmentLogs();
            showToast('Enrichment logs refreshed', 'success');
        });
    }

    document.getElementById('btn-enrich-watcher-check')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/linkedin/watcher-check',
        'enrichment-linkedin-control-note',
        'LinkedIn watcher check completed.'
    ));
    document.getElementById('btn-enrich-force-drain')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/linkedin/force-drain',
        'enrichment-linkedin-control-note',
        'Force-drain check completed.'
    ));
    document.getElementById('btn-enrich-reset-pointer')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/linkedin/reset-pointer',
        'enrichment-linkedin-control-note',
        'LinkedIn pointer reset.',
        'Reset the LinkedIn enrichment pointer back to row 2?\n\nThis keeps existing enrichment results but changes where future drain runs resume from.'
    ));
    document.getElementById('btn-enrich-hard-reset')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/linkedin/reset-hard',
        'enrichment-linkedin-control-note',
        'LinkedIn hard reset completed.',
        'HARD RESET LINKEDIN ENRICHMENT\n\nThis will reset the pointer and clear LinkedIn status, Last checked, and employee-related enrichment fields.\n\nProceed?'
    ));
    document.getElementById('btn-scraperv3-watcher-check')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/scraper-v3/watcher-check',
        'enrichment-scraperv3-control-note',
        'Scraper V3 watcher check completed.'
    ));
    document.getElementById('btn-scraperv3-force-run')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/scraper-v3/force-run',
        'enrichment-scraperv3-control-note',
        'Scraper V3 force-run check completed.',
        'Force Scraper V3 to launch from any current source backlog?\n\nThis temporarily bypasses the normal Alpha+Beta threshold for this one check.'
    ));
    document.getElementById('btn-scraperv3-reset-checkpoint')?.addEventListener('click', () => runEnrichmentControl(
        '/control/enrichment/scraper-v3/reset-checkpoint',
        'enrichment-scraperv3-control-note',
        'Scraper V3 resume memory cleared.',
        'Clear the latest Scraper V3 resume checkpoint?\n\nThis prevents the next run from inheriting stale completed-row memory.'
    ));

    window.addEventListener('status-update', (e) => {
        renderEnrichmentStatus(e.detail);
        renderKeywordScoringStatus(e.detail);
        renderScraperV3Status(e.detail);
    });

    const hashTab = window.location.hash ? window.location.hash.slice(1) : '';
    const storedTab = localStorage.getItem(STORAGE_KEY);
    setActiveTab(hashTab || storedTab || DEFAULT_TAB, Boolean(hashTab));
    refreshEnrichmentLogs();
})();

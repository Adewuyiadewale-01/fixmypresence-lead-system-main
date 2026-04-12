/**
 * Config Page - tabs plus scraper config editing
 */

(function() {
    const tabs = Array.from(document.querySelectorAll('.config-tab'));
    const panels = Array.from(document.querySelectorAll('.config-tab-panel'));
    const enrichmentSubtabs = Array.from(document.querySelectorAll('.config-subtab'));
    const enrichmentSubpanels = Array.from(document.querySelectorAll('.config-subtab-panel'));
    const sheetsSubtabs = Array.from(document.querySelectorAll('.config-sheets-subtab'));
    const sheetsSubpanels = Array.from(document.querySelectorAll('.config-sheets-subtab-panel'));
    const activeChip = document.getElementById('config-active-chip');
    const activeLabel = document.getElementById('config-active-label');
    const STORAGE_KEY = 'config_active_tab';
    const ENRICHMENT_SUBTAB_STORAGE_KEY = 'config_enrichment_active_subtab';
    const SHEETS_SUBTAB_STORAGE_KEY = 'config_sheets_active_subtab';
    const DEFAULT_TAB = 'scraper';
    const DEFAULT_ENRICHMENT_SUBTAB = 'linkedin';
    const DEFAULT_SHEETS_SUBTAB = 'pipeline';
    let scraperConfigSnapshot = null;
    let systemConfigSnapshot = null;
    let schedulerConfigSnapshot = null;

    function labelForTab(tabName) {
        const map = {
            scraper: 'Maps Scraper',
            controller: 'Controller / Watchers',
            enrichment: 'Enrichment',
            locations: 'Locations',
            sheets: 'Google Sheets',
        };
        return map[tabName] || 'Configuration';
    }

    function setActiveTab(tabName, updateHash = true) {
        const safeTab = panels.some((panel) => panel.dataset.configPanel === tabName) ? tabName : DEFAULT_TAB;

        tabs.forEach((tab) => {
            const isActive = tab.dataset.configTab === safeTab;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        panels.forEach((panel) => {
            const isActive = panel.dataset.configPanel === safeTab;
            panel.classList.toggle('is-active', isActive);
            panel.hidden = !isActive;
        });

        const label = labelForTab(safeTab);
        if (activeChip) activeChip.textContent = label;
        if (activeLabel) activeLabel.textContent = label;

        localStorage.setItem(STORAGE_KEY, safeTab);
        if (updateHash) history.replaceState(null, '', `#${safeTab}`);
    }

    function setActiveEnrichmentSubtab(tabName) {
        const safeTab = enrichmentSubpanels.some((panel) => panel.dataset.configSubpanel === tabName)
            ? tabName
            : DEFAULT_ENRICHMENT_SUBTAB;

        enrichmentSubtabs.forEach((tab) => {
            const isActive = tab.dataset.configSubtab === safeTab;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        enrichmentSubpanels.forEach((panel) => {
            const isActive = panel.dataset.configSubpanel === safeTab;
            panel.classList.toggle('is-active', isActive);
            panel.hidden = !isActive;
        });

        localStorage.setItem(ENRICHMENT_SUBTAB_STORAGE_KEY, safeTab);
    }

    function setActiveSheetsSubtab(tabName) {
        const safeTab = sheetsSubpanels.some((panel) => panel.dataset.configSheetsSubpanel === tabName)
            ? tabName
            : DEFAULT_SHEETS_SUBTAB;

        sheetsSubtabs.forEach((tab) => {
            const isActive = tab.dataset.configSheetsSubtab === safeTab;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        sheetsSubpanels.forEach((panel) => {
            const isActive = panel.dataset.configSheetsSubpanel === safeTab;
            panel.classList.toggle('is-active', isActive);
            panel.hidden = !isActive;
        });

        localStorage.setItem(SHEETS_SUBTAB_STORAGE_KEY, safeTab);
    }

    function setValue(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value ?? '';
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value ?? '--';
    }

    function setChecked(id, value) {
        const el = document.getElementById(id);
        if (el) el.checked = Boolean(value);
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function generateRegistryKey(prefix) {
        if (window.crypto?.randomUUID) {
            return `${prefix}_${window.crypto.randomUUID()}`;
        }
        const timestamp = Date.now().toString(36);
        const random = Math.random().toString(36).slice(2, 10);
        return `${prefix}_${timestamp}_${random}`;
    }

    async function copyValueFrom(id, successMessage) {
        const value = document.getElementById(id)?.value || '';
        if (!value) {
            showToast('Nothing to copy yet.', 'error');
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            showToast(successMessage, 'success');
        } catch (_error) {
            showToast('Copy failed. You can still select the text manually.', 'error');
        }
    }

    function listToTextarea(items) {
        return Array.isArray(items) ? items.join('\n') : '';
    }

    function textareaToList(id) {
        const value = document.getElementById(id)?.value || '';
        return value.split('\n').map((item) => item.trim()).filter(Boolean);
    }

    function getLocationSourceMode() {
        return document.querySelector('input[name="scraper-location-source-mode"]:checked')?.value || 'inline';
    }

    function setLocationSourceMode(mode) {
        document.querySelectorAll('input[name="scraper-location-source-mode"]').forEach((input) => {
            input.checked = input.value === mode;
        });
        const inlinePanel = document.getElementById('scraper-location-inline-panel');
        const sheetPanel = document.getElementById('scraper-location-sheet-panel');
        if (inlinePanel) {
            const active = mode === 'inline';
            inlinePanel.hidden = !active;
            inlinePanel.classList.toggle('is-active', active);
        }
        if (sheetPanel) {
            const active = mode === 'sheet_csv';
            sheetPanel.hidden = !active;
            sheetPanel.classList.toggle('is-active', active);
        }
    }

    function updateScraperMeta(config) {
        const runtime = config.runtime || {};
        const guardrails = config.guardrails || {};
        const inlineLocations = Array.isArray(config.locations) ? config.locations : [];
        const effectiveLocations = Array.isArray(config.effective_locations) ? config.effective_locations : inlineLocations;
        const source = config.location_source || {};
        const keywordCategories = Array.isArray(config.keyword_categories) ? config.keyword_categories : [];
        const keywordCounts = keywordCategories.map((group) => Array.isArray(group) ? group.length : 0);
        const keywordTotal = keywordCounts.reduce((sum, count) => sum + count, 0);

        setText(
            'scraper-config-summary-runtime',
            `${runtime.num_workers ?? '--'} workers · batch ${runtime.batch_size ?? '--'} · ${runtime.website_status_workers ?? '--'} website workers`
        );
        setText(
            'scraper-config-summary-locations',
            source.mode === 'sheet_csv'
                ? `${effectiveLocations.length} active · ${Math.max((source.tab_names || []).length, source.tab_name ? 1 : 0)} tabs · ${source.active_tab_name || source.tab_name || 'no active tab'}`
                : `${effectiveLocations.length} active · inline list`
        );
        setText(
            'scraper-config-summary-keywords',
            `${keywordTotal} keywords across ${keywordCategories.length || 4} categories`
        );
        setText(
            'scraper-config-summary-guardrails',
            `error ${guardrails.batch_warning_error_rate ?? '--'}-${guardrails.batch_critical_error_rate ?? '--'}% · yield ${guardrails.batch_warning_yield ?? '--'}-${guardrails.batch_normal_yield ?? '--'}`
        );

        setText('scraper-locations-meta', `${inlineLocations.length} inline locations`);
        setText(
            'scraper-location-resolved-meta',
            source.mode === 'sheet_csv'
                ? `${effectiveLocations.length} resolved locations${source.last_synced_at ? ` · last sync ${new Date(source.last_synced_at).toLocaleString()}` : ''}${source.active_tab_name ? ` · active ${source.active_tab_name}` : ''}`
                : `${effectiveLocations.length} active locations from inline mode`
        );
        for (let i = 0; i < 4; i += 1) {
            setText(`scraper-keywords-${i + 1}-meta`, `${keywordCounts[i] || 0} keywords`);
        }
    }

    function populateScraperForm(config) {
        const runtime = config.runtime || {};
        const guardrails = config.guardrails || {};
        const keywordCategories = Array.isArray(config.keyword_categories) ? config.keyword_categories : [];
        const source = config.location_source || {};
        const meta = config.meta || {};

        setValue('scraper-num-workers', runtime.num_workers);
        setValue('scraper-batch-size', runtime.batch_size);
        setValue('scraper-website-workers', runtime.website_status_workers);
        setValue('scraper-captcha-backoff', runtime.captcha_backoff_s);
        setValue('scraper-delay-min', runtime.inter_request_delay_min);
        setValue('scraper-delay-max', runtime.inter_request_delay_max);
        setValue('scraper-stagger-min', runtime.worker_stagger_delay_min);
        setValue('scraper-stagger-max', runtime.worker_stagger_delay_max);
        setValue('scraper-category-timeout', runtime.category_advance_timeout_minutes);

        setValue('scraper-warn-error-rate', guardrails.batch_warning_error_rate);
        setValue('scraper-critical-error-rate', guardrails.batch_critical_error_rate);
        setValue('scraper-warn-website-rate', guardrails.batch_warning_website_rate);
        setValue('scraper-normal-website-rate', guardrails.batch_normal_website_rate);
        setValue('scraper-warn-yield', guardrails.batch_warning_yield);
        setValue('scraper-normal-yield', guardrails.batch_normal_yield);

        setValue('scraper-locations-list', listToTextarea(config.locations));
        setLocationSourceMode(source.mode || 'inline');
        setValue('scraper-location-service-email', meta.service_account_email || '');
        setValue('scraper-location-sheet-url', source.sheet_url);
        setValue('scraper-location-tab-names', listToTextarea(source.tab_names || (source.tab_name ? [source.tab_name] : [])));
        setValue('scraper-location-active-tab', source.active_tab_name || source.tab_name || '');
        setValue('scraper-location-column-name', source.column_name);
        setChecked('scraper-location-auto-advance', source.auto_advance_enabled);
        setValue('scraper-location-advance-delay', source.auto_advance_delay_minutes);
        setValue('scraper-location-csv-url', source.csv_export_url);
        setValue('scraper-location-resolved-preview', listToTextarea(config.effective_locations || config.location_resolved_locations || []));

        for (let i = 0; i < 4; i += 1) {
            setValue(`scraper-keywords-${i + 1}`, listToTextarea(keywordCategories[i] || []));
        }

        updateScraperMeta(config);
    }

    function getLocationSourcePayload() {
        const tabNames = textareaToList('scraper-location-tab-names');
        const snapshotSource = scraperConfigSnapshot?.location_source || {};
        const activeTab = document.getElementById('scraper-location-active-tab')?.value || snapshotSource.active_tab_name || snapshotSource.tab_name || tabNames[0] || '';
        const activeIndex = tabNames.length ? Math.max(0, tabNames.indexOf(activeTab)) : 0;
        return {
            mode: getLocationSourceMode(),
            sheet_url: document.getElementById('scraper-location-sheet-url')?.value || '',
            tab_names: tabNames,
            tab_name: tabNames[0] || '',
            active_tab_index: activeIndex,
            active_tab_name: activeTab,
            auto_advance_enabled: Boolean(document.getElementById('scraper-location-auto-advance')?.checked),
            auto_advance_delay_minutes: Number(document.getElementById('scraper-location-advance-delay')?.value || 30),
            column_name: document.getElementById('scraper-location-column-name')?.value || '',
            csv_export_url: document.getElementById('scraper-location-csv-url')?.value || '',
        };
    }

    function collectScraperPayload() {
        return {
            runtime: {
                num_workers: Number(document.getElementById('scraper-num-workers')?.value || 0),
                batch_size: Number(document.getElementById('scraper-batch-size')?.value || 0),
                website_status_workers: Number(document.getElementById('scraper-website-workers')?.value || 0),
                captcha_backoff_s: Number(document.getElementById('scraper-captcha-backoff')?.value || 0),
                inter_request_delay_min: Number(document.getElementById('scraper-delay-min')?.value || 0),
                inter_request_delay_max: Number(document.getElementById('scraper-delay-max')?.value || 0),
                worker_stagger_delay_min: Number(document.getElementById('scraper-stagger-min')?.value || 0),
                worker_stagger_delay_max: Number(document.getElementById('scraper-stagger-max')?.value || 0),
                category_advance_timeout_minutes: Number(document.getElementById('scraper-category-timeout')?.value || 0),
            },
            guardrails: {
                batch_warning_error_rate: Number(document.getElementById('scraper-warn-error-rate')?.value || 0),
                batch_critical_error_rate: Number(document.getElementById('scraper-critical-error-rate')?.value || 0),
                batch_warning_website_rate: Number(document.getElementById('scraper-warn-website-rate')?.value || 0),
                batch_normal_website_rate: Number(document.getElementById('scraper-normal-website-rate')?.value || 0),
                batch_warning_yield: Number(document.getElementById('scraper-warn-yield')?.value || 0),
                batch_normal_yield: Number(document.getElementById('scraper-normal-yield')?.value || 0),
            },
            locations: textareaToList('scraper-locations-list'),
            location_source: getLocationSourcePayload(),
            keyword_categories: [
                textareaToList('scraper-keywords-1'),
                textareaToList('scraper-keywords-2'),
                textareaToList('scraper-keywords-3'),
                textareaToList('scraper-keywords-4'),
            ],
            effective_locations: getLocationSourceMode() === 'sheet_csv'
                ? textareaToList('scraper-location-resolved-preview')
                : textareaToList('scraper-locations-list'),
        };
    }

    async function loadScraperConfig() {
        const note = document.getElementById('scraper-config-note');
        try {
            const result = await api('/config/scraper');
            scraperConfigSnapshot = result.config || {};
            populateScraperForm(scraperConfigSnapshot);
            if (note) note.textContent = 'Changes here are saved as overrides and will apply to future scraper starts.';
        } catch (error) {
            if (note) note.textContent = `Failed to load scraper config: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    async function saveScraperConfig() {
        const note = document.getElementById('scraper-config-note');
        try {
            const result = await api('/config/scraper', 'POST', collectScraperPayload());
            scraperConfigSnapshot = result.config || {};
            populateScraperForm(scraperConfigSnapshot);
            if (note) note.textContent = result.message || 'Scraper config saved.';
            showToast(result.message || 'Scraper config saved', 'success');
        } catch (error) {
            if (note) note.textContent = `Save failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    async function syncSheetLocations() {
        const syncNote = document.getElementById('scraper-location-sync-note');
        try {
            const source = scraperConfigSnapshot?.location_source || {};
            const tabNames = textareaToList('scraper-location-tab-names');
            const activeTab = document.getElementById('scraper-location-active-tab')?.value || source.active_tab_name || source.tab_name || tabNames[0] || '';
            const activeIndex = Math.max(0, tabNames.indexOf(activeTab));
            const payload = {
                sheet_url: document.getElementById('scraper-location-sheet-url')?.value || '',
                tab_names: tabNames,
                column_name: document.getElementById('scraper-location-column-name')?.value || '',
                active_tab_index: activeIndex,
                auto_advance_enabled: Boolean(document.getElementById('scraper-location-auto-advance')?.checked),
                auto_advance_delay_minutes: Number(document.getElementById('scraper-location-advance-delay')?.value || 30),
                advance: false,
            };
            if (syncNote) syncNote.textContent = 'Syncing locations from sheet...';
            const result = await api('/config/scraper/locations/sync', 'POST', payload);
            scraperConfigSnapshot = result.config || {};
            populateScraperForm(scraperConfigSnapshot);
            if (syncNote) syncNote.textContent = result.message || 'Location sync complete.';
            showToast(result.message || 'Locations synced', 'success');
        } catch (error) {
            if (syncNote) syncNote.textContent = `Sync failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    async function advanceSheetLocations() {
        const syncNote = document.getElementById('scraper-location-sync-note');
        try {
            const source = scraperConfigSnapshot?.location_source || {};
            const tabNames = textareaToList('scraper-location-tab-names');
            const activeTab = document.getElementById('scraper-location-active-tab')?.value || source.active_tab_name || source.tab_name || tabNames[0] || '';
            const activeIndex = Math.max(0, tabNames.indexOf(activeTab));
            const payload = {
                sheet_url: document.getElementById('scraper-location-sheet-url')?.value || '',
                tab_names: tabNames,
                column_name: document.getElementById('scraper-location-column-name')?.value || '',
                active_tab_index: activeIndex,
                auto_advance_enabled: Boolean(document.getElementById('scraper-location-auto-advance')?.checked),
                auto_advance_delay_minutes: Number(document.getElementById('scraper-location-advance-delay')?.value || 30),
                advance: true,
            };
            if (syncNote) syncNote.textContent = 'Advancing to the next tab and syncing...';
            const result = await api('/config/scraper/locations/advance', 'POST', payload);
            scraperConfigSnapshot = result.config || {};
            populateScraperForm(scraperConfigSnapshot);
            if (syncNote) syncNote.textContent = result.message || 'Advanced and synced.';
            showToast(result.message || 'Advanced to next tab', 'success');
        } catch (error) {
            if (syncNote) syncNote.textContent = `Advance failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    function resetScraperConfig() {
        if (!scraperConfigSnapshot) return;
        populateScraperForm(scraperConfigSnapshot);
        const note = document.getElementById('scraper-config-note');
        if (note) note.textContent = 'Form reset to the last loaded scraper configuration.';
    }

    function renderDeliverySheets(systemConfig) {
        const container = document.getElementById('pipeline-sheet-list');
        const select = document.getElementById('system-active-pipeline-sheet');
        if (!container || !select) return;

        const delivery = systemConfig.pipeline_sheets || systemConfig.delivery_sheets || {};
        const sheets = Array.isArray(delivery.sheets) ? delivery.sheets : [];
        const activeKey = delivery.active_sheet_key || '';

        container.innerHTML = sheets.map((sheet, index) => {
            const status = sheet.status || {};
            const statusText = status.ok
                ? `${status.current_rows ?? '--'} rows · stop ${status.stop_row_threshold ?? '--'}`
                : (status.error || 'No live status');
            return `
                <section class="delivery-sheet-card" data-delivery-index="${index}" data-sheet-key="${escapeHtml(sheet.key || `sheet_${index + 1}`)}">
                    <div class="config-form-grid">
                        <label class="config-field">
                            <span class="config-field-label">Label</span>
                            <input class="delivery-sheet-label" type="text" value="${escapeHtml(sheet.label || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Sheet URL or ID</span>
                            <input class="delivery-sheet-ref" type="text" value="${escapeHtml(sheet.sheet_ref || sheet.sheet_id || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Inbox Tab</span>
                            <input class="delivery-sheet-tab" type="text" value="${escapeHtml(sheet.inbox_tab || sheet.tab_name || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Validated Tab</span>
                            <input class="pipeline-sheet-validated" type="text" value="${escapeHtml(sheet.validated_tab || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Ultra Tab</span>
                            <input class="pipeline-sheet-ultra" type="text" value="${escapeHtml(sheet.ultra_tab || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Config Tab</span>
                            <input class="pipeline-sheet-config" type="text" value="${escapeHtml(sheet.config_tab || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Inbox Quota</span>
                            <input class="pipeline-sheet-inbox-quota" type="number" min="1" value="${escapeHtml(sheet.inbox_quota || 5)}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Enrich Quota</span>
                            <input class="pipeline-sheet-enrich-quota" type="number" min="1" value="${escapeHtml(sheet.enrich_quota || 5)}">
                        </label>
                        <div class="config-field">
                            <span class="config-field-label">Enabled</span>
                            <label class="config-checkbox-card" for="delivery-sheet-enabled-${index}">
                                <span class="config-checkbox-copy">
                                    <span class="config-checkbox-title">Available for delivery</span>
                                    <span class="config-checkbox-description">${escapeHtml(statusText)}</span>
                                </span>
                                <input id="delivery-sheet-enabled-${index}" class="delivery-sheet-enabled" type="checkbox" ${sheet.enabled ? 'checked' : ''}>
                            </label>
                        </div>
                    </div>
                    <div class="config-actions-inline">
                        <button class="btn btn-secondary btn-small btn-remove-delivery-sheet" type="button">Remove</button>
                        <div class="config-inline-note">${sheet.key === activeKey ? 'Active pipeline target' : 'Registered pipeline sheet'}</div>
                    </div>
                </section>
            `;
        }).join('');

        select.innerHTML = sheets.map((sheet) => (
            `<option value="${escapeHtml(sheet.key)}" ${sheet.key === activeKey ? 'selected' : ''}>${escapeHtml(sheet.label)} · ${escapeHtml(sheet.inbox_tab || sheet.tab_name || '')}</option>`
        )).join('');

        container.querySelectorAll('.btn-remove-delivery-sheet').forEach((button) => {
            button.addEventListener('click', () => {
                const card = button.closest('.delivery-sheet-card');
                card?.remove();
                updateSystemDeliverySummary();
            });
        });
    }

    function managedModeLabel(mode) {
        const map = {
            active_pipeline_inbox: 'Active Pipeline Inbox',
            active_pipeline_validated: 'Active Pipeline Validated',
            active_pipeline_ultra: 'Active Pipeline Ultra',
            active_output_sheet: 'Active Output Sheet',
            static: 'Static Sheet',
        };
        return map[mode] || 'Static Sheet';
    }

    function renderManagedSheets(systemConfig) {
        const container = document.getElementById('managed-sheet-list');
        if (!container) return;

        const managed = systemConfig.managed_sheets || {};
        const entries = Array.isArray(managed.entries) ? managed.entries : [];
        container.innerHTML = entries.map((entry, index) => {
            const status = entry.status || {};
            const mode = entry.mode || 'static';
            const isStatic = mode === 'static';
            const statusText = status.ok
                ? `${status.current_rows ?? '--'} rows · stop ${status.stop_row_threshold ?? '--'}`
                : (status.error || 'No live status');
            const resolvedSheet = isStatic
                ? (entry.sheet_ref || entry.sheet_id || '')
                : 'Follows active pipeline sheet';
            const resolvedTab = isStatic
                ? (entry.tab_name || '')
                : (status.tab_name || entry.tab_name || '--');

            return `
                <section class="delivery-sheet-card managed-sheet-card" data-managed-index="${index}" data-managed-key="${escapeHtml(entry.key || `managed_sheet_${index + 1}`)}">
                    <div class="config-form-grid">
                        <label class="config-field">
                            <span class="config-field-label">Label</span>
                            <input class="managed-sheet-label" type="text" value="${escapeHtml(entry.label || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Sheet Role</span>
                            <input class="managed-sheet-mode-label" type="text" value="${escapeHtml(managedModeLabel(mode))}" readonly>
                            <input class="managed-sheet-mode" type="hidden" value="${escapeHtml(mode)}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Sheet URL or ID</span>
                            <input class="managed-sheet-ref" type="text" value="${escapeHtml(resolvedSheet)}" ${isStatic ? '' : 'readonly'}>
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Tab Name</span>
                            <input class="managed-sheet-tab" type="text" value="${escapeHtml(resolvedTab)}" ${isStatic ? '' : 'readonly'}>
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Warning Threshold</span>
                            <input class="managed-sheet-warning" type="number" min="1" value="${escapeHtml(entry.warning_row_threshold || '')}">
                        </label>
                        <label class="config-field">
                            <span class="config-field-label">Hard Stop Threshold</span>
                            <input class="managed-sheet-stop" type="number" min="1" value="${escapeHtml(entry.stop_row_threshold || '')}">
                        </label>
                        <div class="config-field">
                            <span class="config-field-label">Enabled</span>
                            <label class="config-checkbox-card" for="managed-sheet-enabled-${index}">
                                <span class="config-checkbox-copy">
                                    <span class="config-checkbox-title">Protect this sheet</span>
                                    <span class="config-checkbox-description">${escapeHtml(statusText)}</span>
                                </span>
                                <input id="managed-sheet-enabled-${index}" class="managed-sheet-enabled" type="checkbox" ${entry.enabled ? 'checked' : ''}>
                            </label>
                        </div>
                    </div>
                </section>
            `;
        }).join('');
    }

    function renderSourceSettlementSheets(systemConfig) {
        const container = document.getElementById('source-settlement-sheet-list');
        const select = document.getElementById('system-active-source-settlement-sheet');
        const statusEl = document.getElementById('system-active-source-settlement-status');
        if (!container || !select) return;

        const registry = systemConfig.source_settlement || {};
        const sheets = Array.isArray(registry.sheets) ? registry.sheets : [];
        const activeKey = registry.active_sheet_key || '';

        container.innerHTML = sheets.map((sheet, index) => `
            <section class="delivery-sheet-card" data-source-settlement-index="${index}" data-source-settlement-key="${escapeHtml(sheet.key || `source_sheet_${index + 1}`)}">
                <div class="config-form-grid">
                    <label class="config-field">
                        <span class="config-field-label">Label</span>
                        <input class="source-settlement-label" type="text" value="${escapeHtml(sheet.label || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Sheet URL or ID</span>
                        <input class="source-settlement-ref" type="text" value="${escapeHtml(sheet.sheet_ref || sheet.sheet_id || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Source Webhook URL</span>
                        <input class="source-settlement-webhook-url" type="url" value="${escapeHtml(sheet.source_webhook_url || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Alpha Tab</span>
                        <input class="source-settlement-alpha-tab" type="text" value="${escapeHtml(sheet.alpha_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Alpha High Tab</span>
                        <input class="source-settlement-alpha-high-tab" type="text" value="${escapeHtml(sheet.alpha_high_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Beta Tab</span>
                        <input class="source-settlement-beta-tab" type="text" value="${escapeHtml(sheet.beta_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Beta High Tab</span>
                        <input class="source-settlement-beta-high-tab" type="text" value="${escapeHtml(sheet.beta_high_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Gamma Tab</span>
                        <input class="source-settlement-gamma-tab" type="text" value="${escapeHtml(sheet.gamma_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Gamma High Tab</span>
                        <input class="source-settlement-gamma-high-tab" type="text" value="${escapeHtml(sheet.gamma_high_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">I-gamma Tab</span>
                        <input class="source-settlement-i-gamma-tab" type="text" value="${escapeHtml(sheet.i_gamma_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">pBin Tab</span>
                        <input class="source-settlement-pbin-tab" type="text" value="${escapeHtml(sheet.pbin_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Hydro Tab</span>
                        <input class="source-settlement-hydro-tab" type="text" value="${escapeHtml(sheet.hydro_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Hydro Formula Tab</span>
                        <input class="source-settlement-hydro-formula-tab" type="text" value="${escapeHtml(sheet.hydro_formula_tab || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Config Tab</span>
                        <input class="source-settlement-config-tab" type="text" value="${escapeHtml(sheet.config_tab || '')}">
                    </label>
                    <div class="config-field">
                        <span class="config-field-label">Enabled</span>
                        <label class="config-checkbox-card" for="source-settlement-enabled-${index}">
                            <span class="config-checkbox-copy">
                                <span class="config-checkbox-title">Available for source routing</span>
                                <span class="config-checkbox-description">${sheet.key === activeKey ? 'Active source settlement sheet' : 'Registered source settlement sheet'}</span>
                            </span>
                            <input id="source-settlement-enabled-${index}" class="source-settlement-enabled" type="checkbox" ${sheet.enabled ? 'checked' : ''}>
                        </label>
                    </div>
                </div>
                <div class="config-actions-inline">
                    <button class="btn btn-secondary btn-small btn-remove-source-settlement-sheet" type="button">Remove</button>
                    <div class="config-inline-note">${sheet.key === activeKey ? 'Active settlement target' : 'Historical / standby settlement target'}</div>
                </div>
            </section>
        `).join('');

        select.innerHTML = sheets.map((sheet) => (
            `<option value="${escapeHtml(sheet.key)}" ${sheet.key === activeKey ? 'selected' : ''}>${escapeHtml(sheet.label)} · ${escapeHtml(sheet.alpha_tab || 'Alpha')}</option>`
        )).join('');
        if (statusEl) {
            const active = sheets.find((sheet) => sheet.key === activeKey) || sheets[0];
            statusEl.textContent = active
                ? `${active.label} · ${active.alpha_tab || 'Alpha'} / ${active.beta_tab || 'Beta'} / ${active.gamma_tab || 'Gamma'}`
                : '--';
        }
        container.querySelectorAll('.btn-remove-source-settlement-sheet').forEach((button) => {
            button.addEventListener('click', () => button.closest('.delivery-sheet-card')?.remove());
        });
    }

    function renderOutputTargetSheets(systemConfig) {
        const container = document.getElementById('output-target-sheet-list');
        const select = document.getElementById('system-active-output-target-sheet');
        const statusEl = document.getElementById('system-active-output-target-status');
        if (!container || !select) return;

        const registry = systemConfig.output_target || {};
        const sheets = Array.isArray(registry.sheets) ? registry.sheets : [];
        const activeKey = registry.active_sheet_key || '';

        container.innerHTML = sheets.map((sheet, index) => `
            <section class="delivery-sheet-card" data-output-target-index="${index}" data-output-target-key="${escapeHtml(sheet.key || `output_sheet_${index + 1}`)}">
                <div class="config-form-grid">
                    <label class="config-field">
                        <span class="config-field-label">Label</span>
                        <input class="output-target-label" type="text" value="${escapeHtml(sheet.label || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Sheet URL or ID</span>
                        <input class="output-target-ref" type="text" value="${escapeHtml(sheet.sheet_ref || sheet.sheet_id || '')}">
                    </label>
                    <label class="config-field">
                        <span class="config-field-label">Output Tab</span>
                        <input class="output-target-tab-name" type="text" value="${escapeHtml(sheet.tab_name || '')}">
                    </label>
                    <div class="config-field">
                        <span class="config-field-label">Enabled</span>
                        <label class="config-checkbox-card" for="output-target-enabled-${index}">
                            <span class="config-checkbox-copy">
                                <span class="config-checkbox-title">Available for output delivery</span>
                                <span class="config-checkbox-description">${sheet.key === activeKey ? 'Active output sheet' : 'Registered output sheet'}</span>
                            </span>
                            <input id="output-target-enabled-${index}" class="output-target-enabled" type="checkbox" ${sheet.enabled ? 'checked' : ''}>
                        </label>
                    </div>
                </div>
                <div class="config-actions-inline">
                    <button class="btn btn-secondary btn-small btn-remove-output-target-sheet" type="button">Remove</button>
                    <div class="config-inline-note">${sheet.key === activeKey ? 'Active output target' : 'Historical / standby output target'}</div>
                </div>
            </section>
        `).join('');

        select.innerHTML = sheets.map((sheet) => (
            `<option value="${escapeHtml(sheet.key)}" ${sheet.key === activeKey ? 'selected' : ''}>${escapeHtml(sheet.label)} · ${escapeHtml(sheet.tab_name || '')}</option>`
        )).join('');
        if (statusEl) {
            const active = sheets.find((sheet) => sheet.key === activeKey) || sheets[0];
            statusEl.textContent = active ? `${active.label} · ${active.tab_name || '--'}` : '--';
        }
        container.querySelectorAll('.btn-remove-output-target-sheet').forEach((button) => {
            button.addEventListener('click', () => button.closest('.delivery-sheet-card')?.remove());
        });
    }

    function renderSheetRotationState(systemConfig) {
        const rotation = ((systemConfig.meta || {}).sheet_rotation || {});
        const registries = rotation.registries || {};
        const targets = [
            ['pipeline_sheets', 'system-pipeline-rotation-status', 'btn-approve-pipeline-rotation', 'pipeline'],
            ['source_settlement', 'system-source-rotation-status', 'btn-approve-source-rotation', 'source settlement'],
            ['output_target', 'system-output-rotation-status', 'btn-approve-output-rotation', 'final output'],
        ];
        targets.forEach(([registryKey, statusId, buttonId, fallbackLabel]) => {
            const statusEl = document.getElementById(statusId);
            const button = document.getElementById(buttonId);
            const entry = registries[registryKey] || {};
            const live = entry.live_status || {};
            const rotationStatus = entry.rotation_status || '';
            const activeLabel = live.active_label || entry.active_label || fallbackLabel;
            const pendingLabel = entry.pending_sheet_label || 'the prepared replacement';
            const deadline = entry.approval_deadline_at ? fmtTime(entry.approval_deadline_at) : '';
            let text = `No pending ${fallbackLabel} rotation.`;
            let showButton = false;

            if (rotationStatus === 'paused_pending_approval') {
                text = `${activeLabel} is paused at full capacity. Pending move to ${pendingLabel}${deadline ? ` · auto-continue ${deadline}` : ''}.`;
                showButton = true;
            } else if (rotationStatus === 'prepared') {
                text = `${activeLabel} prepared ${pendingLabel} at ${live.percent_used ?? '--'}% capacity.`;
                showButton = true;
            } else if (live.phase === 'warning') {
                text = `${activeLabel} crossed ${live.percent_used ?? '--'}% capacity. Red notice active.`;
            } else if (live.phase === 'prepare') {
                text = `${activeLabel} crossed ${live.percent_used ?? '--'}% capacity. Replacement preparation active.`;
            } else if (live.phase === 'blocked') {
                text = `${activeLabel} is at stop threshold.`;
            }

            if (statusEl) statusEl.textContent = text;
            if (button) button.hidden = !showButton;
        });
    }

    function fmtTime(value) {
        if (!value) return '';
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return '';
        return dt.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }

    function updateSystemDeliverySummary() {
        const select = document.getElementById('system-active-pipeline-sheet');
        const statusEl = document.getElementById('system-active-pipeline-status');
        const container = document.getElementById('pipeline-sheet-list');
        if (!select || !statusEl || !container) return;
        const activeKey = select.value;
        const cards = Array.from(container.querySelectorAll('.delivery-sheet-card'));
        const idx = cards.findIndex((card) => card.dataset.sheetKey === activeKey);
        const activeCard = idx >= 0 ? cards[idx] : null;
        const label = activeCard?.querySelector('.delivery-sheet-label')?.value || select.selectedOptions[0]?.textContent || '--';
        statusEl.textContent = `Active pipeline: ${label}`;
    }

    function collectDeliverySheets() {
        const container = document.getElementById('pipeline-sheet-list');
        if (!container) return [];
        return Array.from(container.querySelectorAll('.delivery-sheet-card')).map((card, index) => ({
            key: card.dataset.sheetKey || generateRegistryKey('sheet'),
            label: card.querySelector('.delivery-sheet-label')?.value || `Pipeline Sheet ${index + 1}`,
            sheet_ref: card.querySelector('.delivery-sheet-ref')?.value || '',
            inbox_tab: card.querySelector('.delivery-sheet-tab')?.value || '',
            tab_name: card.querySelector('.delivery-sheet-tab')?.value || '',
            validated_tab: card.querySelector('.pipeline-sheet-validated')?.value || 'Validated_leads',
            ultra_tab: card.querySelector('.pipeline-sheet-ultra')?.value || 'Ultra_validated',
            config_tab: card.querySelector('.pipeline-sheet-config')?.value || 'config',
            inbox_quota: Number(card.querySelector('.pipeline-sheet-inbox-quota')?.value || 5),
            enrich_quota: Number(card.querySelector('.pipeline-sheet-enrich-quota')?.value || 5),
            enabled: Boolean(card.querySelector('.delivery-sheet-enabled')?.checked),
        })).filter((item) => item.sheet_ref.trim() && item.inbox_tab.trim());
    }

    function populateSystemForm(config, schedulerConfig = null) {
        const meta = config.meta || {};
        const watchers = (config.watchers || {}).delivery_sheet_capacity || {};
        const enrichment = ((config.enrichment || {}).linkedin || {});
        const enrichmentRuntime = enrichment.runtime || {};
        const enrichmentWatcher = enrichment.watcher || {};
        const keywordScoring = ((config.enrichment || {}).keyword_scoring || {});
        const keywordDetector = keywordScoring.keyword_detector || {};
        const keywordWatcher = keywordScoring.watcher || {};
        const scoring = keywordScoring.scoring || {};
        const scraperV3 = ((config.enrichment || {}).scraper_v3 || {});
        const scraperV3Runtime = scraperV3.runtime || {};
        const scraperV3Watcher = scraperV3.watcher || {};
        schedulerConfigSnapshot = schedulerConfig || schedulerConfigSnapshot;
        setValue('system-sheet-service-email', meta.service_account_email || '');
        setValue('system-run-hours', schedulerConfigSnapshot?.run_hours ?? '');
        setValue('system-rest-hours', schedulerConfigSnapshot?.rest_hours ?? '');
        setChecked('system-watch-delivery-enabled', watchers.enabled);
        setValue('system-watch-warning-rows', watchers.warning_row_threshold);
        setValue('system-watch-stop-rows', watchers.stop_row_threshold);
        setValue('enrich-batch-size', enrichmentRuntime.batch_size);
        setValue('enrich-timeout-seconds', enrichmentRuntime.request_timeout_seconds);
        setValue('enrich-website-workers', enrichmentRuntime.website_workers);
        setValue('enrich-linkedin-workers', enrichmentRuntime.linkedin_workers);
        setValue('enrich-flush-interval', enrichmentRuntime.writer_flush_interval_seconds);
        setValue('enrich-flush-batch', enrichmentRuntime.writer_flush_batch);
        setValue('enrich-delay-min', enrichmentRuntime.linkedin_delay_min_seconds);
        setValue('enrich-delay-max', enrichmentRuntime.linkedin_delay_max_seconds);
        setValue('enrich-max-browsers', enrichmentRuntime.max_browsers);
        setValue('enrich-drain-threshold', enrichmentWatcher.min_backlog_to_drain);
        setValue('enrich-max-wait-minutes', enrichmentWatcher.max_wait_before_forced_drain_minutes);
        setValue('enrich-watcher-interval', enrichmentWatcher.watcher_interval_minutes);
        setValue('keyword-batch-size', keywordDetector.batch_size);
        setValue('keyword-timeout-seconds', keywordDetector.request_timeout_seconds);
        setValue('keyword-qualifier-min-matches', keywordDetector.qualifier_min_matches);
        setValue('keyword-trigger-threshold', keywordWatcher.trigger_threshold);
        setValue('keyword-watcher-interval', keywordWatcher.watcher_interval_minutes);
        setValue('keyword-lock-timeout', keywordWatcher.lock_timeout_minutes);
        setValue('keyword-relevance-keywords', listToTextarea(keywordDetector.relevance_keywords || []));
        setValue('keyword-qualifier-keywords', listToTextarea(keywordDetector.qualifier_keywords || []));
        setValue('scoring-batch-size', scoring.batch_size);
        setValue('scraperv3-site-timeout', scraperV3Runtime.site_timeout_seconds);
        setValue('scraperv3-concurrent-sites', scraperV3Runtime.concurrent_sites);
        setValue('scraperv3-playwright-limit', scraperV3Runtime.playwright_limit);
        setValue('scraperv3-playwright-concurrency', scraperV3Runtime.playwright_concurrency);
        setValue('scraperv3-subpage-limit', scraperV3Runtime.subpage_limit);
        setValue('scraperv3-level2-limit', scraperV3Runtime.level2_limit);
        setValue('scraperv3-evidence-probe-limit', scraperV3Runtime.evidence_probe_limit);
        setValue('scraperv3-output-chunk-size', scraperV3Runtime.output_write_chunk_size);
        setValue('scraperv3-trigger-threshold', scraperV3Watcher.alpha_beta_trigger_threshold);
        setValue('scraperv3-max-wait-minutes', scraperV3Watcher.max_wait_before_forced_run_minutes);
        setValue('scraperv3-watcher-interval', scraperV3Watcher.watcher_interval_minutes);
        setValue('scraperv3-stale-lock-timeout', scraperV3Watcher.stale_lock_timeout_minutes);
        renderDeliverySheets(config);
        renderManagedSheets(config);
        renderSourceSettlementSheets(config);
        renderOutputTargetSheets(config);
        renderSheetRotationState(config);
        updateSystemDeliverySummary();

        const activeStatus = ((config.pipeline_sheets || config.delivery_sheets || {}).active_status) || {};
        const statusEl = document.getElementById('system-active-pipeline-status');
        if (statusEl) {
            if (activeStatus.ok) {
                statusEl.textContent = `${activeStatus.sheet_label || 'Active sheet'} · ${activeStatus.current_rows} rows in ${activeStatus.tab_name || 'Leads'} · ${activeStatus.rows_remaining_to_stop} rows left before stop`;
            } else {
                statusEl.textContent = activeStatus.error || 'No live sheet status yet.';
            }
        }
    }

    function collectSystemPayload() {
        return {
            watchers: {
                delivery_sheet_capacity: {
                    enabled: Boolean(document.getElementById('system-watch-delivery-enabled')?.checked),
                    warning_row_threshold: Number(document.getElementById('system-watch-warning-rows')?.value || 0),
                    stop_row_threshold: Number(document.getElementById('system-watch-stop-rows')?.value || 0),
                },
            },
            enrichment: {
                linkedin: {
                    runtime: {
                        batch_size: Number(document.getElementById('enrich-batch-size')?.value || 0),
                        request_timeout_seconds: Number(document.getElementById('enrich-timeout-seconds')?.value || 0),
                        website_workers: Number(document.getElementById('enrich-website-workers')?.value || 0),
                        linkedin_workers: Number(document.getElementById('enrich-linkedin-workers')?.value || 0),
                        writer_flush_interval_seconds: Number(document.getElementById('enrich-flush-interval')?.value || 0),
                        writer_flush_batch: Number(document.getElementById('enrich-flush-batch')?.value || 0),
                        linkedin_delay_min_seconds: Number(document.getElementById('enrich-delay-min')?.value || 0),
                        linkedin_delay_max_seconds: Number(document.getElementById('enrich-delay-max')?.value || 0),
                        max_browsers: Number(document.getElementById('enrich-max-browsers')?.value || 0),
                    },
                    watcher: {
                        min_backlog_to_drain: Number(document.getElementById('enrich-drain-threshold')?.value || 0),
                        max_wait_before_forced_drain_minutes: Number(document.getElementById('enrich-max-wait-minutes')?.value || 0),
                        watcher_interval_minutes: Number(document.getElementById('enrich-watcher-interval')?.value || 0),
                    },
                },
                keyword_scoring: {
                    keyword_detector: {
                        batch_size: Number(document.getElementById('keyword-batch-size')?.value || 0),
                        request_timeout_seconds: Number(document.getElementById('keyword-timeout-seconds')?.value || 0),
                        qualifier_min_matches: Number(document.getElementById('keyword-qualifier-min-matches')?.value || 0),
                        relevance_keywords: textareaToList('keyword-relevance-keywords'),
                        qualifier_keywords: textareaToList('keyword-qualifier-keywords'),
                    },
                    watcher: {
                        trigger_threshold: Number(document.getElementById('keyword-trigger-threshold')?.value || 0),
                        watcher_interval_minutes: Number(document.getElementById('keyword-watcher-interval')?.value || 0),
                        lock_timeout_minutes: Number(document.getElementById('keyword-lock-timeout')?.value || 0),
                    },
                    scoring: {
                        batch_size: Number(document.getElementById('scoring-batch-size')?.value || 0),
                    },
                },
                scraper_v3: {
                    runtime: {
                        site_timeout_seconds: Number(document.getElementById('scraperv3-site-timeout')?.value || 0),
                        concurrent_sites: Number(document.getElementById('scraperv3-concurrent-sites')?.value || 0),
                        playwright_limit: Number(document.getElementById('scraperv3-playwright-limit')?.value || 0),
                        playwright_concurrency: Number(document.getElementById('scraperv3-playwright-concurrency')?.value || 0),
                        subpage_limit: Number(document.getElementById('scraperv3-subpage-limit')?.value || 0),
                        level2_limit: Number(document.getElementById('scraperv3-level2-limit')?.value || 0),
                        evidence_probe_limit: Number(document.getElementById('scraperv3-evidence-probe-limit')?.value || 0),
                        output_write_chunk_size: Number(document.getElementById('scraperv3-output-chunk-size')?.value || 0),
                    },
                    watcher: {
                        alpha_beta_trigger_threshold: Number(document.getElementById('scraperv3-trigger-threshold')?.value || 0),
                        max_wait_before_forced_run_minutes: Number(document.getElementById('scraperv3-max-wait-minutes')?.value || 0),
                        watcher_interval_minutes: Number(document.getElementById('scraperv3-watcher-interval')?.value || 0),
                        stale_lock_timeout_minutes: Number(document.getElementById('scraperv3-stale-lock-timeout')?.value || 0),
                    },
                },
            },
            pipeline_sheets: {
                active_sheet_key: document.getElementById('system-active-pipeline-sheet')?.value || '',
                sheets: collectDeliverySheets(),
            },
            source_settlement: collectSourceSettlementSheets(),
            output_target: collectOutputTargetSheets(),
            managed_sheets: {
                entries: collectManagedSheets(),
            },
        };
    }

    function collectSourceSettlementSheets() {
        const container = document.getElementById('source-settlement-sheet-list');
        if (!container) return { active_sheet_key: '', sheets: [] };
        return {
            active_sheet_key: document.getElementById('system-active-source-settlement-sheet')?.value || '',
            sheets: Array.from(container.querySelectorAll('.delivery-sheet-card')).map((card, index) => ({
                key: card.dataset.sourceSettlementKey || generateRegistryKey('source_sheet'),
                label: card.querySelector('.source-settlement-label')?.value || `Source Settlement ${index + 1}`,
                sheet_ref: card.querySelector('.source-settlement-ref')?.value || '',
                source_webhook_url: card.querySelector('.source-settlement-webhook-url')?.value || '',
                alpha_tab: card.querySelector('.source-settlement-alpha-tab')?.value || 'Alpha',
                alpha_high_tab: card.querySelector('.source-settlement-alpha-high-tab')?.value || 'Alpha_high',
                beta_tab: card.querySelector('.source-settlement-beta-tab')?.value || 'Beta',
                beta_high_tab: card.querySelector('.source-settlement-beta-high-tab')?.value || 'Beta_high',
                gamma_tab: card.querySelector('.source-settlement-gamma-tab')?.value || 'Gamma',
                gamma_high_tab: card.querySelector('.source-settlement-gamma-high-tab')?.value || 'Gamma_high',
                i_gamma_tab: card.querySelector('.source-settlement-i-gamma-tab')?.value || 'I-gamma',
                pbin_tab: card.querySelector('.source-settlement-pbin-tab')?.value || 'pBin',
                hydro_tab: card.querySelector('.source-settlement-hydro-tab')?.value || 'Hydro',
                hydro_formula_tab: card.querySelector('.source-settlement-hydro-formula-tab')?.value || 'Hydro_formula',
                config_tab: card.querySelector('.source-settlement-config-tab')?.value || 'config',
                enabled: Boolean(card.querySelector('.source-settlement-enabled')?.checked),
            })).filter((item) => item.sheet_ref.trim()),
        };
    }

    function collectOutputTargetSheets() {
        const container = document.getElementById('output-target-sheet-list');
        if (!container) return { active_sheet_key: '', sheets: [] };
        return {
            active_sheet_key: document.getElementById('system-active-output-target-sheet')?.value || '',
            sheets: Array.from(container.querySelectorAll('.delivery-sheet-card')).map((card, index) => ({
                key: card.dataset.outputTargetKey || generateRegistryKey('output_sheet'),
                label: card.querySelector('.output-target-label')?.value || `Output Sheet ${index + 1}`,
                sheet_ref: card.querySelector('.output-target-ref')?.value || '',
                tab_name: card.querySelector('.output-target-tab-name')?.value || 'Employee_db',
                enabled: Boolean(card.querySelector('.output-target-enabled')?.checked),
            })).filter((item) => item.sheet_ref.trim() && item.tab_name.trim()),
        };
    }

    function collectManagedSheets() {
        const container = document.getElementById('managed-sheet-list');
        if (!container) return [];
        return Array.from(container.querySelectorAll('.managed-sheet-card')).map((card, index) => {
            const mode = card.querySelector('.managed-sheet-mode')?.value || 'static';
            const payload = {
                key: card.dataset.managedKey || generateRegistryKey('managed_sheet'),
                label: card.querySelector('.managed-sheet-label')?.value || `Managed Sheet ${index + 1}`,
                mode,
                enabled: Boolean(card.querySelector('.managed-sheet-enabled')?.checked),
                warning_row_threshold: Number(card.querySelector('.managed-sheet-warning')?.value || 0),
                stop_row_threshold: Number(card.querySelector('.managed-sheet-stop')?.value || 0),
            };
            if (mode === 'static') {
                payload.sheet_ref = card.querySelector('.managed-sheet-ref')?.value || '';
                payload.tab_name = card.querySelector('.managed-sheet-tab')?.value || '';
            }
            return payload;
        }).filter((item) => item.mode !== 'static' || (String(item.sheet_ref || '').trim() && String(item.tab_name || '').trim()));
    }

    async function loadSystemConfig() {
        try {
            const [systemResult, schedulerResult] = await Promise.all([
                api('/config/system'),
                api('/config/scheduler'),
            ]);
            systemConfigSnapshot = systemResult.config || {};
            schedulerConfigSnapshot = schedulerResult.config || schedulerResult.state?.config || {};
            populateSystemForm(systemConfigSnapshot, schedulerConfigSnapshot);
        } catch (error) {
            showToast(`Failed to load system config: ${error.message}`, 'error');
        }
    }

    async function saveSystemConfig(noteId) {
        const note = document.getElementById(noteId);
        try {
            const schedulerPayload = {
                run_hours: Number(document.getElementById('system-run-hours')?.value || 0),
                rest_hours: Number(document.getElementById('system-rest-hours')?.value || 0),
            };
            await api('/config', 'POST', schedulerPayload);
            schedulerConfigSnapshot = schedulerPayload;
            const result = await api('/config/system', 'POST', collectSystemPayload());
            systemConfigSnapshot = result.config || {};
            populateSystemForm(systemConfigSnapshot, schedulerPayload);
            if (note) note.textContent = result.message || 'System config saved.';
            showToast(result.message || 'System config saved', 'success');
        } catch (error) {
            if (note) note.textContent = `Save failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    function resetSystemConfig(noteId) {
        if (!systemConfigSnapshot) return;
        populateSystemForm(systemConfigSnapshot, schedulerConfigSnapshot);
        const note = document.getElementById(noteId);
        if (note) note.textContent = 'Form reset to the last loaded system configuration.';
    }

    function addDeliverySheetRow() {
        const current = systemConfigSnapshot || {};
        const delivery = current.pipeline_sheets || current.delivery_sheets || {};
        const sheets = Array.isArray(delivery.sheets) ? delivery.sheets.slice() : [];
        sheets.push({
            key: generateRegistryKey('sheet'),
            label: `Pipeline Sheet ${sheets.length + 1}`,
            sheet_ref: '',
            inbox_tab: 'Leads',
            validated_tab: 'Validated_leads',
            ultra_tab: 'Ultra_validated',
            config_tab: 'config',
            inbox_quota: 5,
            enrich_quota: 5,
            enabled: true,
            status: {},
        });
        renderDeliverySheets({
            ...current,
            pipeline_sheets: {
                ...delivery,
                sheets,
                active_sheet_key: delivery.active_sheet_key || sheets[0]?.key || '',
            },
        });
    }

    function addSourceSettlementSheetRow() {
        const current = systemConfigSnapshot || {};
        const registry = current.source_settlement || {};
        const sheets = Array.isArray(registry.sheets) ? registry.sheets.slice() : [];
        sheets.push({
            key: generateRegistryKey('source_sheet'),
            label: `Source Settlement ${sheets.length + 1}`,
            sheet_ref: '',
            source_webhook_url: '',
            alpha_tab: 'Alpha',
            alpha_high_tab: 'Alpha_high',
            beta_tab: 'Beta',
            beta_high_tab: 'Beta_high',
            gamma_tab: 'Gamma',
            gamma_high_tab: 'Gamma_high',
            i_gamma_tab: 'I-gamma',
            pbin_tab: 'pBin',
            hydro_tab: 'Hydro',
            hydro_formula_tab: 'Hydro_formula',
            config_tab: 'config',
            enabled: true,
        });
        renderSourceSettlementSheets({
            ...current,
            source_settlement: {
                ...registry,
                sheets,
                active_sheet_key: registry.active_sheet_key || sheets[0]?.key || '',
            },
        });
    }

    function addOutputTargetSheetRow() {
        const current = systemConfigSnapshot || {};
        const registry = current.output_target || {};
        const sheets = Array.isArray(registry.sheets) ? registry.sheets.slice() : [];
        sheets.push({
            key: generateRegistryKey('output_sheet'),
            label: `Output Sheet ${sheets.length + 1}`,
            sheet_ref: '',
            tab_name: 'Employee_db',
            enabled: true,
        });
        renderOutputTargetSheets({
            ...current,
            output_target: {
                ...registry,
                sheets,
                active_sheet_key: registry.active_sheet_key || sheets[0]?.key || '',
            },
        });
    }

    async function approveSheetRotation(registry, noteId) {
        const note = document.getElementById(noteId);
        try {
            const result = await api(`/control/sheets/approve-rotation/${registry}`, 'POST');
            const refreshed = await api('/config/system');
            systemConfigSnapshot = refreshed.config || {};
            populateSystemForm(systemConfigSnapshot, schedulerConfigSnapshot);
            if (note) note.textContent = result.message || 'Rotation approved.';
            showToast(result.message || 'Rotation approved', 'success');
        } catch (error) {
            if (note) note.textContent = `Approval failed: ${error.message}`;
            showToast(error.message, 'error');
        }
    }

    tabs.forEach((tab) => {
        tab.addEventListener('click', () => setActiveTab(tab.dataset.configTab));
    });

    enrichmentSubtabs.forEach((tab) => {
        tab.addEventListener('click', () => setActiveEnrichmentSubtab(tab.dataset.configSubtab));
    });

    sheetsSubtabs.forEach((tab) => {
        tab.addEventListener('click', () => setActiveSheetsSubtab(tab.dataset.configSheetsSubtab));
    });

    const hashTab = window.location.hash ? window.location.hash.slice(1) : null;
    const storedTab = localStorage.getItem(STORAGE_KEY);
    setActiveTab(hashTab || storedTab || DEFAULT_TAB, false);
    setActiveEnrichmentSubtab(localStorage.getItem(ENRICHMENT_SUBTAB_STORAGE_KEY) || DEFAULT_ENRICHMENT_SUBTAB);
    setActiveSheetsSubtab(localStorage.getItem(SHEETS_SUBTAB_STORAGE_KEY) || DEFAULT_SHEETS_SUBTAB);

    window.addEventListener('hashchange', () => {
        const nextTab = window.location.hash ? window.location.hash.slice(1) : DEFAULT_TAB;
        setActiveTab(nextTab, false);
    });

    document.getElementById('btn-save-scraper-config')?.addEventListener('click', saveScraperConfig);
    document.getElementById('btn-reset-scraper-config')?.addEventListener('click', resetScraperConfig);
    document.getElementById('btn-sync-scraper-locations')?.addEventListener('click', syncSheetLocations);
    document.getElementById('btn-advance-scraper-locations')?.addEventListener('click', advanceSheetLocations);
    document.getElementById('btn-copy-scraper-service-email')?.addEventListener('click', () => copyValueFrom('scraper-location-service-email', 'Service-account email copied'));
    document.getElementById('btn-copy-scraper-csv-url')?.addEventListener('click', () => copyValueFrom('scraper-location-csv-url', 'CSV export URL copied'));
    document.getElementById('btn-copy-system-service-email')?.addEventListener('click', () => copyValueFrom('system-sheet-service-email', 'Service-account email copied'));
    document.getElementById('btn-save-system-watchers')?.addEventListener('click', () => saveSystemConfig('system-watchers-config-note'));
    document.getElementById('btn-reset-system-watchers')?.addEventListener('click', () => resetSystemConfig('system-watchers-config-note'));
    document.getElementById('btn-save-enrichment-linkedin-config')?.addEventListener('click', () => saveSystemConfig('enrichment-linkedin-config-note'));
    document.getElementById('btn-reset-enrichment-linkedin-config')?.addEventListener('click', () => resetSystemConfig('enrichment-linkedin-config-note'));
    document.getElementById('btn-save-enrichment-keyword-config')?.addEventListener('click', () => saveSystemConfig('enrichment-keyword-config-note'));
    document.getElementById('btn-reset-enrichment-keyword-config')?.addEventListener('click', () => resetSystemConfig('enrichment-keyword-config-note'));
    document.getElementById('btn-save-enrichment-scraperv3-config')?.addEventListener('click', () => saveSystemConfig('enrichment-scraperv3-config-note'));
    document.getElementById('btn-reset-enrichment-scraperv3-config')?.addEventListener('click', () => resetSystemConfig('enrichment-scraperv3-config-note'));
    document.getElementById('btn-save-system-sheets')?.addEventListener('click', () => saveSystemConfig('system-sheets-config-note'));
    document.getElementById('btn-reset-system-sheets')?.addEventListener('click', () => resetSystemConfig('system-sheets-config-note'));
    document.getElementById('btn-add-pipeline-sheet')?.addEventListener('click', addDeliverySheetRow);
    document.getElementById('btn-add-source-settlement-sheet')?.addEventListener('click', addSourceSettlementSheetRow);
    document.getElementById('btn-add-output-target-sheet')?.addEventListener('click', addOutputTargetSheetRow);
    document.getElementById('system-active-pipeline-sheet')?.addEventListener('change', updateSystemDeliverySummary);
    document.getElementById('btn-approve-pipeline-rotation')?.addEventListener('click', () => approveSheetRotation('pipeline_sheets', 'system-pipeline-rotation-status'));
    document.getElementById('btn-approve-source-rotation')?.addEventListener('click', () => approveSheetRotation('source_settlement', 'system-source-rotation-status'));
    document.getElementById('btn-approve-output-rotation')?.addEventListener('click', () => approveSheetRotation('output_target', 'system-output-rotation-status'));

    document.querySelectorAll('input[name="scraper-location-source-mode"]').forEach((input) => {
        input.addEventListener('change', () => {
            setLocationSourceMode(input.value);
            updateScraperMeta(collectScraperPayload());
        });
    });

    [
        'scraper-num-workers',
        'scraper-batch-size',
        'scraper-website-workers',
        'scraper-captcha-backoff',
        'scraper-delay-min',
        'scraper-delay-max',
        'scraper-stagger-min',
        'scraper-stagger-max',
        'scraper-category-timeout',
        'scraper-warn-error-rate',
        'scraper-critical-error-rate',
        'scraper-warn-website-rate',
        'scraper-normal-website-rate',
        'scraper-warn-yield',
        'scraper-normal-yield',
        'scraper-locations-list',
        'scraper-location-sheet-url',
        'scraper-location-tab-names',
        'scraper-location-column-name',
        'scraper-location-advance-delay',
        'scraper-keywords-1',
        'scraper-keywords-2',
        'scraper-keywords-3',
        'scraper-keywords-4',
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', () => updateScraperMeta(collectScraperPayload()));
        }
    });

    document.getElementById('scraper-location-auto-advance')?.addEventListener('change', () => updateScraperMeta(collectScraperPayload()));

    loadScraperConfig();
    loadSystemConfig();
    lucide.createIcons();
})();

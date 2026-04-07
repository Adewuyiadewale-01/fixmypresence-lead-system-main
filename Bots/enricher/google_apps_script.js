// === CONFIG ===
const SECRET = 'FMP-Scraper';
const PIPELINE_CONFIG_PROPERTY = 'FMP_ACTIVE_PIPELINE_CONFIG';
const DEFAULT_PIPELINE_CONFIG = {
  sheetId: '1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc',
  sheetLabel: 'Primary Pipeline',
  tabInbox: 'Leads',
  tabValidated: 'Validated_leads',
  tabUltra: 'Ultra_validated',
  tabConfig: 'config',
  inboxQuota: 5,
  enrichQuota: 5,
  hydroSheetId: '1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc',
  hydroFormulaTab: 'Hydro_formula',
  hydroIncompleteTab: 'Hydro_incomplete'
};
const MAX_BATCH = 50;

function extractDriveFolderId(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  const match = text.match(/\/folders\/([a-zA-Z0-9-_]+)/);
  if (match) return match[1];
  return /^[a-zA-Z0-9-_]+$/.test(text) ? text : '';
}

function clearWorksheetData(sheet) {
  if (!sheet) return;
  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow <= 1 || lastCol <= 0) return;
  sheet.getRange(2, 1, lastRow - 1, lastCol).clearContent();
}

function clearPipelineCopy(ss, cfg) {
  [
    cfg.tabInbox,
    cfg.tabValidated,
    cfg.tabUltra,
    cfg.tabConfig,
  ].forEach(function(tabName) {
    const sheet = ss.getSheetByName(tabName);
    if (sheet) clearWorksheetData(sheet);
  });
}

function createPipelineRotationCopy(options) {
  const cfg = getPipelineConfig();
  const opts = options || {};
  const title = String(opts.title || (cfg.sheetLabel + ' Next')).trim() || (cfg.sheetLabel + ' Next');
  const folderId = extractDriveFolderId(opts.folderId || opts.folder_id);
  const sourceFile = DriveApp.getFileById(cfg.sheetId);
  const copyFile = folderId
    ? sourceFile.makeCopy(title, DriveApp.getFolderById(folderId))
    : sourceFile.makeCopy(title);
  const copiedSheetId = copyFile.getId();
  const copiedSpreadsheet = SpreadsheetApp.openById(copiedSheetId);
  clearPipelineCopy(copiedSpreadsheet, cfg);
  return {
    sheet_id: copiedSheetId,
    sheet_ref: copiedSheetId,
    title: copyFile.getName(),
  };
}

function authorizeRotationAccess() {
  DriveApp.getRootFolder();
  SpreadsheetApp.getActive();
  return true;
}

function getPipelineConfig() {
  const props = PropertiesService.getScriptProperties();
  const raw = props.getProperty(PIPELINE_CONFIG_PROPERTY);
  if (!raw) return Object.assign({}, DEFAULT_PIPELINE_CONFIG);

  try {
    const parsed = JSON.parse(raw);
    return normalizePipelineConfig(parsed);
  } catch (err) {
    return Object.assign({}, DEFAULT_PIPELINE_CONFIG);
  }
}

function normalizePipelineConfig(config) {
  const data = config || {};
  return {
    sheetId: String(data.sheetId || data.sheet_id || DEFAULT_PIPELINE_CONFIG.sheetId).trim(),
    sheetLabel: String(data.sheetLabel || data.sheet_label || DEFAULT_PIPELINE_CONFIG.sheetLabel).trim(),
    tabInbox: String(data.tabInbox || data.inbox_tab || data.tabName || DEFAULT_PIPELINE_CONFIG.tabInbox).trim(),
    tabValidated: String(data.tabValidated || data.validated_tab || DEFAULT_PIPELINE_CONFIG.tabValidated).trim(),
    tabUltra: String(data.tabUltra || data.ultra_tab || DEFAULT_PIPELINE_CONFIG.tabUltra).trim(),
    tabConfig: String(data.tabConfig || data.config_tab || DEFAULT_PIPELINE_CONFIG.tabConfig).trim(),
    inboxQuota: Number(data.inboxQuota || data.inbox_quota || DEFAULT_PIPELINE_CONFIG.inboxQuota) || DEFAULT_PIPELINE_CONFIG.inboxQuota,
    enrichQuota: Number(data.enrichQuota || data.enrich_quota || DEFAULT_PIPELINE_CONFIG.enrichQuota) || DEFAULT_PIPELINE_CONFIG.enrichQuota,
    hydroSheetId: String(data.hydroSheetId || data.hydro_sheet_id || DEFAULT_PIPELINE_CONFIG.hydroSheetId).trim(),
    hydroFormulaTab: String(data.hydroFormulaTab || data.hydro_formula_tab || DEFAULT_PIPELINE_CONFIG.hydroFormulaTab).trim(),
    hydroIncompleteTab: String(data.hydroIncompleteTab || data.hydro_incomplete_tab || DEFAULT_PIPELINE_CONFIG.hydroIncompleteTab).trim()
  };
}

function savePipelineConfig(config) {
  const normalized = normalizePipelineConfig(config);
  PropertiesService.getScriptProperties().setProperty(
    PIPELINE_CONFIG_PROPERTY,
    JSON.stringify(normalized)
  );
  ensurePipelineTriggers(normalized);
  return normalized;
}

function openPipelineSpreadsheet(config) {
  const cfg = config || getPipelineConfig();
  return SpreadsheetApp.openById(cfg.sheetId);
}

function ensurePipelineTriggers(config) {
  const cfg = config || getPipelineConfig();
  const targetId = cfg.sheetId;
  const existing = ScriptApp.getProjectTriggers();

  existing.forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'onSheetChange') {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger('onSheetChange')
    .forSpreadsheet(targetId)
    .onChange()
    .create();
}

// === 1. RECEIVE NEW LEADS (via /doPost endpoint) ===
function doPost(e) {
    try {
        const payload = JSON.parse(e.postData.contents || '{}');
        if (!payload || payload.token !== SECRET) {
            return ContentService.createTextOutput('unauthorized')
                .setMimeType(ContentService.MimeType.TEXT);
        }

        if (payload.action === 'sync_pipeline_config') {
            const saved = savePipelineConfig(payload.pipeline || {});
            return ContentService.createTextOutput(
              JSON.stringify({ status: 'ok', message: 'pipeline config synced', pipeline: saved })
            ).setMimeType(ContentService.MimeType.JSON);
        }

        if (payload.action === 'create_rotation_copy') {
            const created = createPipelineRotationCopy(payload.rotation || {});
            return ContentService.createTextOutput(
              JSON.stringify({ status: 'ok', message: 'pipeline rotation copy created', copy: created })
            ).setMimeType(ContentService.MimeType.JSON);
        }

        if (payload.action === 'run_stage2_sorter') {
            processPhase1Enrichment();
            return ContentService.createTextOutput(
              JSON.stringify({ status: 'ok', message: 'stage2 sorter executed' })
            ).setMimeType(ContentService.MimeType.JSON);
        }

        const pipelineConfig = payload.pipeline
          ? savePipelineConfig(payload.pipeline)
          : getPipelineConfig();

        const rows = payload.rows;
        if (!Array.isArray(rows) || rows.length === 0) {
            return ContentService.createTextOutput('no rows')
                .setMimeType(ContentService.MimeType.TEXT);
        }

        // Sanity check
        for (var i = 0; i < rows.length; i++) {
            // MODIFIED: Accept 7 (legacy) or 8 (with status) columns
            if (!Array.isArray(rows[i]) || rows[i].length < 7) {
                return ContentService.createTextOutput('bad row at index ' + i)
                    .setMimeType(ContentService.MimeType.TEXT);
            }
        }

        const ss = openPipelineSpreadsheet(pipelineConfig);
        const sh = ss.getSheetByName(pipelineConfig.tabInbox);
        // MODIFIED: Use dynamic width (rows[0].length) instead of hardcoded 7
        const target = sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length);
        target.setValues(rows);

        const currentRows = sh.getLastRow() - 1;

        if (currentRows >= pipelineConfig.inboxQuota) {
            const moveSummary = moveVerifiedLeads();
            if (moveSummary.movedCount > 0) {
                processPhase1Enrichment();
            }
            return ContentService.createTextOutput(
              JSON.stringify({
                status: 'ok',
                message: `Quota met (${currentRows}). Moved instantly.`,
                pipeline: pipelineConfig,
                moved: moveSummary.movedCount,
                duplicates: moveSummary.duplicateCount
              })
            ).setMimeType(ContentService.MimeType.JSON);
        }

        return ContentService.createTextOutput(
          JSON.stringify({
            status: 'ok',
            message: `Waiting: Inbox has ${currentRows}/${pipelineConfig.inboxQuota} leads.`,
            pipeline: pipelineConfig
          })
        ).setMimeType(ContentService.MimeType.JSON);
    } catch (err) {
        return ContentService.createTextOutput('error: ' + err)
            .setMimeType(ContentService.MimeType.TEXT);
    }
}

// === 1B. WATCHER (Validated -> sorter) ===
function onSheetChange(e) {
    const cfg = getPipelineConfig();
    const ss = (e && e.source) || openPipelineSpreadsheet(cfg);
    const sheet = ss.getActiveSheet();

    if (!sheet || sheet.getName() !== cfg.tabValidated) return;

    const lock = LockService.getScriptLock();
    if (lock.tryLock(5000)) {
        try {
            processPhase1Enrichment();
        } catch (err) {
            Logger.log("Error in Instant Sort: " + err);
        } finally {
            lock.releaseLock();
        }
    }
}

// === STAGE 1: MOVER (Inbox -> Validated) ===
function moveVerifiedLeads() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const src = ss.getSheetByName(cfg.tabInbox);
    const dest = ss.getSheetByName(cfg.tabValidated);

    if (!src || !dest) {
        Logger.log("❌ Inbox or Validated tab missing.");
        return { movedCount: 0, duplicateCount: 0 };
    }

    const data = src.getDataRange().getValues();
    if (data.length <= 1) return { movedCount: 0, duplicateCount: 0 };

    const headers = data[0].map(h => h.toString().toLowerCase().trim());
    const nameCol = headers.indexOf("name");
    const webCol = headers.indexOf("website");

    let statusCol = headers.indexOf("status");
    if (statusCol === -1) statusCol = headers.indexOf("website status");

    let moveStatusCol = headers.indexOf("move status");
    if (moveStatusCol === -1) {
        moveStatusCol = headers.length;
        src.getRange(1, moveStatusCol + 1).setValue("Move Status");
    }

    if (nameCol === -1 || webCol === -1 || statusCol === -1) {
        Logger.log("❌ Missing required Inbox columns.");
        return { movedCount: 0, duplicateCount: 0 };
    }

    const destLastRow = dest.getLastRow();
    let existingWebsites = new Set();
    if (destLastRow > 1) {
        const destData = dest.getRange(2, 2, destLastRow - 1, 1).getValues();
        destData.forEach(r => existingWebsites.add((r[0] || "").toString().toLowerCase().trim()));
    }

    const rowsToMove = [];
    const updates = [];

    for (let i = 1; i < data.length; i++) {
        const rawStatus = data[i][statusCol];
        const status = (rawStatus || "").toString().toLowerCase().trim();
        const moveStatus = (data[i][moveStatusCol] || "").toString().trim();

        if (moveStatus === "MOVED" || moveStatus === "DUPLICATE") continue;

        if (status === "live" || status === "✅") {
            const name = data[i][nameCol];
            const website = data[i][webCol];
            const cleanWeb = (website || "").toString().toLowerCase().trim();

            if (existingWebsites.has(cleanWeb)) {
                updates.push({ row: i + 1, status: "DUPLICATE" });
            } else {
                rowsToMove.push([name, website, "✅"]);
                updates.push({ row: i + 1, status: "MOVED" });
                existingWebsites.add(cleanWeb);
            }
        }
    }

    if (rowsToMove.length > 0) {
        dest.getRange(dest.getLastRow() + 1, 1, rowsToMove.length, 3).setValues(rowsToMove);
    }

    updates.forEach(u => src.getRange(u.row, moveStatusCol + 1).setValue(u.status));
    return {
        movedCount: rowsToMove.length,
        duplicateCount: updates.filter(u => u.status === "DUPLICATE").length
    };
}

// === STAGE 2: SORTER (Validated -> Ultra / Hydro) ===
function processPhase1Enrichment() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const src = ss.getSheetByName(cfg.tabValidated);
    const ultra = ss.getSheetByName(cfg.tabUltra);

    if (!src || !ultra) {
        Logger.log("❌ Validated or Ultra tab missing.");
        return;
    }

    const data = src.getDataRange().getValues();
    if (data.length <= 1) return;

    const headers = data[0].map(h => h.toString().toLowerCase());
    const idx = (name) => headers.indexOf(name);

    const colName = idx("name");
    const colWeb = idx("website");
    const colLi = idx("linkedin");
    const colEmp = idx("linkedin employees");
    const colPpl = idx("linkedin people link");

    let colMoveStatus = idx("move status");
    if (colMoveStatus === -1) {
        colMoveStatus = headers.length;
        src.getRange(1, colMoveStatus + 1).setValue("move status");
    }

    let readyCount = 0;
    for (let i = 1; i < data.length; i++) {
        const row = data[i];
        if (row[colMoveStatus] && row[colMoveStatus].toString() !== "") continue;
        if (row[colLi] && row[colLi].toString().trim() !== "") {
            readyCount++;
        }
    }

    if (readyCount < cfg.enrichQuota) {
        Logger.log(`Stage 2: Waiting... Found ${readyCount}/${cfg.enrichQuota} rows.`);
        return;
    }

    const ultraRows = [];
    const hydroFormulaRows = [];
    const hydroIncompleteRows = [];
    const updates = [];

    for (let i = 1; i < data.length; i++) {
        const row = data[i];
        if (row[colMoveStatus] && row[colMoveStatus].toString() !== "") continue;

        const liRaw = row[colLi];
        if (!liRaw || liRaw.toString().trim() === "") continue;

        const name = row[colName];
        const web = row[colWeb];
        const emp = row[colEmp];
        const ppl = row[colPpl];
        const li = liRaw.toString();

        const isGoogleSearch = li.includes("Google") || li.includes("search");
        const isFormula = li.startsWith("=");
        const hasLinkedIn = li.includes("linkedin.com");
        const hasEmp = emp && emp.toString() !== "";
        const hasPpl = ppl && ppl.toString() !== "";

        if (isGoogleSearch || isFormula) {
            hydroFormulaRows.push([name, web, li, emp, ppl]);
            updates.push({ row: i + 1, status: "HYDRO_FORMULA" });
        } else if (hasLinkedIn && hasEmp && hasPpl) {
            ultraRows.push([name, web, li, emp, ppl]);
            updates.push({ row: i + 1, status: "ULTRA" });
        } else if (hasLinkedIn) {
            hydroIncompleteRows.push([name, web, li, emp, ppl]);
            updates.push({ row: i + 1, status: "HYDRO_INCOMPLETE" });
        }
    }

    if (ultraRows.length > 0) {
        ultra.getRange(ultra.getLastRow() + 1, 1, ultraRows.length, 5).setValues(ultraRows);
    }

    if (hydroFormulaRows.length > 0 || hydroIncompleteRows.length > 0) {
        sendToHydroSheet(cfg, hydroFormulaRows, hydroIncompleteRows);
    }

    updates.forEach(u => src.getRange(u.row, colMoveStatus + 1).setValue(u.status));
    Logger.log(`Moved: ${ultraRows.length} Ultra, ${hydroFormulaRows.length} Formula, ${hydroIncompleteRows.length} Hydro`);
}

function sendToHydroSheet(cfg, formulaRows, incompleteRows) {
    const ssHydro = SpreadsheetApp.openById(cfg.hydroSheetId);

    if (formulaRows.length > 0) {
        let sheet = ssHydro.getSheetByName(cfg.hydroFormulaTab);
        if (!sheet) sheet = ssHydro.insertSheet(cfg.hydroFormulaTab);
        sheet.getRange(sheet.getLastRow() + 1, 1, formulaRows.length, 5).setValues(formulaRows);
    }

    if (incompleteRows.length > 0) {
        let sheet = ssHydro.getSheetByName(cfg.hydroIncompleteTab);
        if (!sheet) sheet = ssHydro.insertSheet(cfg.hydroIncompleteTab);
        sheet.getRange(sheet.getLastRow() + 1, 1, incompleteRows.length, 5).setValues(incompleteRows);
    }
}

// === 2. VALIDATE WEBSITES IN BATCHES OF 50 (auto-resumes) ===
function validateWebsitesFromValidatedLeads() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const sheet = ss.getSheetByName(cfg.tabValidated);
    const destSheet = ss.getSheetByName(cfg.tabUltra) || ss.insertSheet(cfg.tabUltra);
    const configSheet = ss.getSheetByName(cfg.tabConfig) || ss.insertSheet(cfg.tabConfig);

    const data = sheet.getDataRange().getValues();
    if (data.length < 2) {
        Logger.log('⚠️ No data rows in Validated_leads.');
        return;
    }

    const headers = data[0].map(h => (h || "").toString().trim().toLowerCase());
    const colIndex = (name) => headers.indexOf(name.toLowerCase());

    const nameCol = colIndex('name');
    const websiteCol = colIndex('website');
    let statusCol = colIndex('website status');
    const linkedinCol = colIndex('linkedin');
    const empCountCol = colIndex('linkedin employees');
    const peopleLinkCol = colIndex('linkedin people link');

    if (nameCol === -1 || websiteCol === -1) {
        Logger.log('❌ Missing required columns "name" or "website" in Validated_leads.');
        return;
    }

    // Add Website Status column if not found
    if (statusCol === -1) {
        statusCol = headers.length;
        sheet.getRange(1, statusCol + 1).setValue('Website Status');
        Logger.log('🆕 Added "Website Status" column.');
    }

    // === CONFIG: read or initialize next_row ===
    let nextRow = 2;  // default start at row 2
    let keyRange = [];
    const configLastRow = configSheet.getLastRow();

    if (configLastRow > 0) {
        keyRange = configSheet.getRange(1, 1, configLastRow, 2).getValues();
        const rowKey = keyRange.find(r => r[0] === 'next_row');
        if (rowKey && rowKey[1]) {
            const parsed = parseInt(rowKey[1], 10);
            if (!isNaN(parsed) && parsed >= 2) {
                nextRow = parsed;
            }
        }
    } else {
        // First time: initialize config
        configSheet.appendRow(['next_row', nextRow]);
        Logger.log('🆕 Initialized config.next_row = 2');
    }

    // === Step 1: collect unvalidated websites from pointer onwards ===
    const rowsToCheck = [];
    for (let i = nextRow - 1; i < data.length; i++) {
        const row = data[i];
        const website = row[websiteCol];
        const status = row[statusCol];

        if (website && (!status || status === '')) {
            rowsToCheck.push({ rowIndex: i + 1, website });
        }
    }

    if (rowsToCheck.length < MAX_BATCH) {
        Logger.log(`⏸️ Not enough new leads to process: found ${rowsToCheck.length}, need ${MAX_BATCH}`);
        return;
    }

    const batch = rowsToCheck.slice(0, MAX_BATCH);

    // === Step 2: ping websites for this batch ===
    batch.forEach(({ rowIndex, website }) => {
        let isLive = false;
        try {
            const response = UrlFetchApp.fetch(website, {
                muteHttpExceptions: true,
                followRedirects: true,
                timeout: 10,
            });
            const code = response.getResponseCode();
            if (code >= 200 && code < 400) isLive = true;
        } catch (e) {
            isLive = false;
        }
        const mark = isLive ? '✅' : '❌';
        sheet.getRange(rowIndex, statusCol + 1).setValue(mark);
    });

    Logger.log(`🔍 Checked ${batch.length} websites for liveness.`);

    // === Step 3: re-fetch data & transfer fully enriched rows for THIS batch window ===
    const freshData = sheet.getDataRange().getValues();
    const rowsToTransfer = [];

    // Determine the numeric row range this batch corresponds to
    const batchStartRow = batch[0].rowIndex;
    const batchEndRow = batch[batch.length - 1].rowIndex;

    for (let r = batchStartRow; r <= batchEndRow; r++) {
        const row = freshData[r - 1];
        if (!row) continue;

        const status = row[statusCol];
        const name = row[nameCol];
        const website = row[websiteCol];
        const linkedin = linkedinCol >= 0 ? row[linkedinCol] : '';
        const empCount = empCountCol >= 0 ? row[empCountCol] : '';
        const peopleLink = peopleLinkCol >= 0 ? row[peopleLinkCol] : '';

        const isFormula = typeof linkedin === 'string' && linkedin.trim().startsWith('=');
        const isEmpty = (val) => !val || val.toString().trim() === '';

        const isFullyEnriched =
            status === '✅' &&
            !isEmpty(name) &&
            !isEmpty(website) &&
            !isEmpty(linkedin) &&
            !isFormula &&
            !isEmpty(empCount) &&
            !isEmpty(peopleLink);

        if (isFullyEnriched) {
            rowsToTransfer.push([name, website, linkedin, empCount, peopleLink]);
        }
    }

    if (rowsToTransfer.length > 0) {
        const hasHeader = destSheet.getLastRow() > 0;
        if (!hasHeader) {
            destSheet.appendRow(['name', 'website', 'LinkedIn', 'LinkedIn employees', 'LinkedIn people link']);
        }

        const startRow = destSheet.getLastRow() + 1;
        destSheet
            .getRange(startRow, 1, rowsToTransfer.length, 5)
            .setValues(rowsToTransfer);

        Logger.log(`✅ Transferred ${rowsToTransfer.length} rows to Ultra_validated (rows ${batchStartRow}-${batchEndRow} in Validated_leads).`);
    } else {
        Logger.log('⚠️ No enriched rows ready for transfer in this batch.');
    }

    // === Step 4: Advance pointer by MAX_BATCH ===
    const newNext = nextRow + MAX_BATCH;

    if (configLastRow > 0) {
        const keys = keyRange.map(r => r[0]);
        const idx = keys.indexOf('next_row');
        if (idx !== -1) {
            configSheet.getRange(idx + 1, 2).setValue(newNext);
        } else {
            configSheet.appendRow(['next_row', newNext]);
        }
    } else {
        // We already appended in the init case, but be safe:
        configSheet.appendRow(['next_row', newNext]);
    }

    Logger.log(`🔁 Next batch will start at logical row ${newNext}`);
}

// === 3. RESET FUNCTION (manual) ===
function resetLeadValidationState() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const sourceSheet = ss.getSheetByName(cfg.tabValidated);
    const destSheet = ss.getSheetByName(cfg.tabUltra);
    const configSheet = ss.getSheetByName(cfg.tabConfig);

    if (!sourceSheet || !destSheet) {
        throw new Error("❌ One of the main tabs (Validated_leads / Ultra_validated) is missing");
    }

    const data = sourceSheet.getDataRange().getValues();
    if (data.length > 1) {
        const headers = data[0].map(h => (h || '').toString().trim().toLowerCase());
        const statusCol = headers.indexOf('website status');

        if (statusCol !== -1) {
            // Clear Website Status for all data rows
            sourceSheet.getRange(2, statusCol + 1, data.length - 1, 1).clearContent();
            Logger.log(`✅ Website Status cleared for ${data.length - 1} rows.`);
        } else {
            Logger.log('⚠️ No "Website Status" column found to reset.');
        }
    }

    // Clear Ultra_validated except header
    const destLast = destSheet.getLastRow();
    if (destLast > 1) {
        destSheet.getRange(2, 1, destLast - 1, 5).clearContent();
        Logger.log(`✅ Cleared ${destLast - 1} rows from Ultra_validated.`);
    } else {
        Logger.log('⚠️ Ultra_validated already empty or only header present.');
    }

    // Reset config.next_row to 2
    if (configSheet) {
        const last = configSheet.getLastRow();
        if (last > 0) {
            const keys = configSheet.getRange(1, 1, last, 1).getValues().flat();
            const idx = keys.indexOf('next_row');
            if (idx !== -1) {
                configSheet.getRange(idx + 1, 2).setValue(2);
            } else {
                configSheet.appendRow(['next_row', 2]);
            }
        } else {
            configSheet.appendRow(['next_row', 2]);
        }
        Logger.log(`🔁 Reset config.next_row to 2.`);
    }

    Logger.log("🎯 Full reset done.");
}

function transferCompletedLeads() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const sheet = ss.getSheetByName(cfg.tabValidated);
    const dest = ss.getSheetByName(cfg.tabUltra) || ss.insertSheet(cfg.tabUltra);

    if (!sheet) {
        Logger.log('❌ Validated tab not found: ' + cfg.tabValidated);
        return;
    }

    const data = sheet.getDataRange().getValues();
    if (data.length < 2) {
        Logger.log('⚠️ No data rows in Validated_leads.');
        return;
    }

    // --- header helpers ---
    let headers = data[0].map(h => (h || '').toString().trim().toLowerCase());
    const idx = (name) => headers.indexOf(name.toLowerCase());
    const isEmpty = (val) => !val || val.toString().trim() === '';

    let nameCol = idx('name');
    let websiteCol = idx('website');
    let statusCol = idx('website status');          // ✅ / ❌ or FOUND/NONE
    let linkedinCol = idx('linkedin');
    let empCountCol = idx('linkedin employees');
    let peopleLinkCol = idx('linkedin people link');
    let ultraStatusCol = idx('ultra status');           // marker so we don't re-transfer

    if (nameCol === -1 || websiteCol === -1 || linkedinCol === -1) {
        Logger.log('❌ Missing one of: name / website / LinkedIn columns.');
        return;
    }

    // Create "Ultra Status" column if missing
    if (ultraStatusCol === -1) {
        const newCol = sheet.getLastColumn() + 1;
        sheet.getRange(1, newCol).setValue('Ultra Status');
        Logger.log('🆕 Added "Ultra Status" column.');

        // Update headers + index
        headers = sheet.getRange(1, 1, 1, newCol).getValues()[0]
            .map(h => (h || '').toString().trim().toLowerCase());
        ultraStatusCol = headers.indexOf('ultra status');
    }

    const rowsToTransfer = [];
    const rowsToMark = [];

    // --- scan every row for "complete + not yet transferred" ---
    for (let i = 1; i < data.length; i++) {
        const row = data[i];

        const name = row[nameCol];
        const website = row[websiteCol];
        const status = statusCol >= 0 ? row[statusCol] : '';
        const linkedin = linkedinCol >= 0 ? row[linkedinCol] : '';
        const empCount = empCountCol >= 0 ? row[empCountCol] : '';
        const peopleLink = peopleLinkCol >= 0 ? row[peopleLinkCol] : '';
        const ultraFlag = ultraStatusCol >= 0 ? row[ultraStatusCol] : '';

        // already transferred?
        if (!isEmpty(ultraFlag)) {
            continue;
        }

        // LinkedIn must be a real URL (not a formula, must contain linkedin.com)
        const liStr = (linkedin || '').toString().trim();
        const isFormula = liStr.startsWith('=');
        const hasRealLinkedIn =
            !isFormula && liStr !== '' && liStr.toLowerCase().indexOf('linkedin.com') !== -1;

        // Website must be marked live (✅ or FOUND etc)
        const live =
            status === '✅' ||
            status === 'FOUND' ||
            status === true;  // just in case

        const fullyEnriched =
            live &&
            !isEmpty(name) &&
            !isEmpty(website) &&
            hasRealLinkedIn &&
            !isEmpty(empCount) &&
            !isEmpty(peopleLink);

        if (!fullyEnriched) {
            continue;
        }

        // Queue for transfer
        rowsToTransfer.push([name, website, liStr, empCount, peopleLink]);
        rowsToMark.push(i + 1); // sheet row index (1-based)
    }

    if (rowsToTransfer.length === 0) {
        Logger.log('⚠️ No completed rows to transfer right now.');
        return;
    }

    // Ensure header in Ultra_validated
    if (dest.getLastRow() === 0) {
        dest.appendRow(['name', 'website', 'LinkedIn', 'LinkedIn employees', 'LinkedIn people link']);
    }

    // Write all transfers
    const startRow = dest.getLastRow() + 1;
    dest.getRange(startRow, 1, rowsToTransfer.length, 5)
        .setValues(rowsToTransfer);

    // Mark transferred rows back in Validated_leads
    rowsToMark.forEach(r => {
        sheet.getRange(r, ultraStatusCol + 1).setValue('✅');
    });

    Logger.log(`✅ Transferred ${rowsToTransfer.length} completed leads to Ultra_validated.`);
}


function transferHydroCandidates() {
    const cfg = getPipelineConfig();
    const ss = openPipelineSpreadsheet(cfg);
    const sheet = ss.getSheetByName(cfg.tabValidated);

    if (!sheet) {
        Logger.log('❌ Validated tab not found: ' + cfg.tabValidated);
        return;
    }

    const lastRow = sheet.getLastRow();
    const lastCol = sheet.getLastColumn();
    if (lastRow < 2) {
        Logger.log('⚠️ No data rows in Validated_leads.');
        return;
    }

    // Values (display) + formulas (for detecting Google search formulas)
    const values = sheet.getRange(1, 1, lastRow, lastCol).getValues();
    const formulas = sheet.getRange(1, 1, lastRow, lastCol).getFormulas();

    // ---- headers + helpers ----
    let headers = values[0].map(h => (h || '').toString().trim().toLowerCase());
    const idx = (name) => headers.indexOf(name.toLowerCase());
    const isEmpty = (val) => !val || val.toString().trim() === '';

    const nameCol = idx('name');
    const websiteCol = idx('website');
    const linkedinCol = idx('linkedin');
    const empCountCol = idx('linkedin employees');
    const peopleLinkCol = idx('linkedin people link');
    let hydroStatusCol = idx('hydro status');  // marker so we don't re-transfer

    if (nameCol === -1 || websiteCol === -1 || linkedinCol === -1) {
        Logger.log('❌ Missing one of: name / website / LinkedIn columns.');
        return;
    }

    // Create "Hydro Status" column if missing
    if (hydroStatusCol === -1) {
        const newCol = sheet.getLastColumn() + 1;
        sheet.getRange(1, newCol).setValue('Hydro Status');
        Logger.log('🆕 Added "Hydro Status" column.');

        // Refresh headers + index
        headers = sheet.getRange(1, 1, 1, newCol).getValues()[0]
            .map(h => (h || '').toString().trim().toLowerCase());
        hydroStatusCol = headers.indexOf('hydro status');
    }

    const formulaRows = []; // LinkedIn = formula
    const incompleteRows = []; // real LI URL but emp fields missing
    const formulaMarks = [];
    const incompleteMarks = [];

    // ---- scan rows ----
    for (let i = 1; i < lastRow; i++) {
        const row = values[i];
        const fRow = formulas[i];

        const name = row[nameCol];
        const website = row[websiteCol];
        const linkedinVal = linkedinCol >= 0 ? row[linkedinCol] : '';
        const linkedinFormula = linkedinCol >= 0 ? fRow[linkedinCol] : '';
        const empCount = empCountCol >= 0 ? row[empCountCol] : '';
        const peopleLink = peopleLinkCol >= 0 ? row[peopleLinkCol] : '';
        const hydroFlag = hydroStatusCol >= 0 ? row[hydroStatusCol] : '';

        // Already sent to Hydro?
        if (!isEmpty(hydroFlag)) continue;

        const displayStr = (linkedinVal || '').toString().trim();
        const formulaStr = (linkedinFormula || '').toString().trim();

        const isFormula = formulaStr !== ''; // any formula in that cell
        const hasRealLinkedIn =
            !isFormula &&
            displayStr !== '' &&
            displayStr.toLowerCase().indexOf('linkedin.com') !== -1;

        const missingEmpFields =
            hasRealLinkedIn && (isEmpty(empCount) || isEmpty(peopleLink));

        // If it's not a formula and not missing anything → not Hydro
        if (!isFormula && !missingEmpFields) continue;

        // Separate into two categories
        if (isFormula) {
            // Hydro: formula / Google search placeholder
            formulaRows.push([
                name,
                website,
                formulaStr, // store the formula expression as reference
                empCount,
                peopleLink
            ]);
            formulaMarks.push(i + 1);
        } else if (missingEmpFields) {
            // Hydro: real LinkedIn but incomplete enrichment
            incompleteRows.push([
                name,
                website,
                displayStr,
                empCount,
                peopleLink
            ]);
            incompleteMarks.push(i + 1);
        }
    }

    if (formulaRows.length === 0 && incompleteRows.length === 0) {
        Logger.log('⚠️ No Hydro candidates to transfer right now.');
        return;
    }

    // ---- open external scoring sheet ----
    const destBook = SpreadsheetApp.openById(cfg.hydroSheetId);

    // --- write formula-type Hydro ---
    if (formulaRows.length > 0) {
        let formulaSheet = destBook.getSheetByName(cfg.hydroFormulaTab);
        if (!formulaSheet) {
            formulaSheet = destBook.insertSheet(cfg.hydroFormulaTab);
            formulaSheet.appendRow([
                'name',
                'website',
                'LinkedIn (formula)',
                'LinkedIn employees',
                'LinkedIn people link'
            ]);
            Logger.log('🆕 Created Hydro_formula tab in external sheet.');
        }
        const startRow = formulaSheet.getLastRow() + 1;
        formulaSheet
            .getRange(startRow, 1, formulaRows.length, 5)
            .setValues(formulaRows);
    }

    // --- write incomplete-type Hydro ---
    if (incompleteRows.length > 0) {
        let incompleteSheet = destBook.getSheetByName(cfg.hydroIncompleteTab);
        if (!incompleteSheet) {
            incompleteSheet = destBook.insertSheet(cfg.hydroIncompleteTab);
            incompleteSheet.appendRow([
                'name',
                'website',
                'LinkedIn',
                'LinkedIn employees',
                'LinkedIn people link'
            ]);
            Logger.log('🆕 Created Hydro_incomplete tab in external sheet.');
        }
        const startRow2 = incompleteSheet.getLastRow() + 1;
        incompleteSheet
            .getRange(startRow2, 1, incompleteRows.length, 5)
            .setValues(incompleteRows);
    }

    // Mark rows as handled in Validated_leads
    formulaMarks.forEach(r => {
        sheet.getRange(r, hydroStatusCol + 1).setValue('FORMULA');
    });
    incompleteMarks.forEach(r => {
        sheet.getRange(r, hydroStatusCol + 1).setValue('INCOMPLETE');
    });

    Logger.log(
        `✅ Hydro transfer complete. FORMULA: ${formulaRows.length}, INCOMPLETE: ${incompleteRows.length}.`
    );
}

// === CONFIG ===
const SHEET_ID = '1KrKoeun-h6eEzSK6-cc4_MUHriuabg8GyAR9b8NR7fc';
const TAB_NAME = 'Leads';
const SECRET = 'FMP-Scraper';
const SOURCE_TAB = 'Validated_leads';
const DEST_TAB = 'Ultra_validated';
const CONFIG_TAB = 'config';
const MAX_BATCH = 50;
// External scoring sheet (where Alpha/Beta/Gamma/Hydro live)
const HYDRO_DEST_SHEET_ID = '1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc';
const HYDRO_FORMULA_TAB_NAME = 'Hydro_formula';     // formulas only
const HYDRO_INCOMPLETE_TAB_NAME = 'Hydro_incomplete'; // real LI, missing fields

// === 1. RECEIVE NEW LEADS (via /doPost endpoint) ===
function doPost(e) {
    try {
        const payload = JSON.parse(e.postData.contents || '{}');
        if (!payload || payload.token !== SECRET) {
            return ContentService.createTextOutput('unauthorized')
                .setMimeType(ContentService.MimeType.TEXT);
        }

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

        const ss = SpreadsheetApp.openById(SHEET_ID);
        const sh = ss.getSheetByName(TAB_NAME);
        // MODIFIED: Use dynamic width (rows[0].length) instead of hardcoded 7
        const target = sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length);
        target.setValues(rows);

        return ContentService.createTextOutput('ok')
            .setMimeType(ContentService.MimeType.TEXT);
    } catch (err) {
        return ContentService.createTextOutput('error: ' + err)
            .setMimeType(ContentService.MimeType.TEXT);
    }
}

// === 2. VALIDATE WEBSITES IN BATCHES OF 50 (auto-resumes) ===
function validateWebsitesFromValidatedLeads() {
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sheet = ss.getSheetByName(SOURCE_TAB);
    const destSheet = ss.getSheetByName(DEST_TAB) || ss.insertSheet(DEST_TAB);
    const configSheet = ss.getSheetByName(CONFIG_TAB) || ss.insertSheet(CONFIG_TAB);

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
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sourceSheet = ss.getSheetByName(SOURCE_TAB);
    const destSheet = ss.getSheetByName(DEST_TAB);
    const configSheet = ss.getSheetByName(CONFIG_TAB);

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
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sheet = ss.getSheetByName(SOURCE_TAB);
    const dest = ss.getSheetByName(DEST_TAB) || ss.insertSheet(DEST_TAB);

    if (!sheet) {
        Logger.log('❌ SOURCE_TAB not found: ' + SOURCE_TAB);
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
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sheet = ss.getSheetByName(SOURCE_TAB);

    if (!sheet) {
        Logger.log('❌ SOURCE_TAB not found: ' + SOURCE_TAB);
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
    const destBook = SpreadsheetApp.openById(HYDRO_DEST_SHEET_ID);

    // --- write formula-type Hydro ---
    if (formulaRows.length > 0) {
        let formulaSheet = destBook.getSheetByName(HYDRO_FORMULA_TAB_NAME);
        if (!formulaSheet) {
            formulaSheet = destBook.insertSheet(HYDRO_FORMULA_TAB_NAME);
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
        let incompleteSheet = destBook.getSheetByName(HYDRO_INCOMPLETE_TAB_NAME);
        if (!incompleteSheet) {
            incompleteSheet = destBook.insertSheet(HYDRO_INCOMPLETE_TAB_NAME);
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

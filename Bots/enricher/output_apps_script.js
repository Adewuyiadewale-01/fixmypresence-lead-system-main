/**
 * OUTPUT APPS SCRIPT
 * Deploy on output sheet (Employee_db sheet: 1YTLSv2-vo8PkDJebRZzwbxIC3Agafw_Prj6bTfudZQ8)
 * Receives scraped company+person results and writes grouped rows to Employee_db tab.
 *
 * Idempotent: deduplicates on company ID. If a company already exists,
 * its rows are replaced (updated) instead of appended as duplicates.
 *
 * Grouped format per company:
 *   Header row: ID | Company Name | Company Website | Company LinkedIn | '' | '' | '' | '' | Class
 *   Person rows: '' | '' | '' | '' | Person Name | Person Role | Person Email | Person LinkedIn | Class
 *
 * Returns JSON with per-company status: inserted, updated, or error.
 */

const OUTPUT_SHEET_ID = '1YTLSv2-vo8PkDJebRZzwbxIC3Agafw_Prj6bTfudZQ8';
const OUTPUT_TAB = 'Employee_db';
const OUTPUT_SECRET = 'FMP-Scraper';
const OUTPUT_CONFIG_PROPERTY = 'FMP_OUTPUT_SHEET_CONFIG';

const DEFAULT_OUTPUT_CONFIG = {
  sheetId: OUTPUT_SHEET_ID,
  tabName: OUTPUT_TAB,
};

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

function createOutputRotationCopy(options) {
  const cfg = getOutputConfig();
  const opts = options || {};
  const title = String(opts.title || 'Final Output Next').trim() || 'Final Output Next';
  const folderId = extractDriveFolderId(opts.folderId || opts.folder_id);
  const sourceFile = DriveApp.getFileById(cfg.sheetId);
  const copyFile = folderId
    ? sourceFile.makeCopy(title, DriveApp.getFolderById(folderId))
    : sourceFile.makeCopy(title);
  const copiedSheetId = copyFile.getId();
  const copiedSpreadsheet = SpreadsheetApp.openById(copiedSheetId);
  clearWorksheetData(copiedSpreadsheet.getSheetByName(cfg.tabName));
  return {
    sheet_id: copiedSheetId,
    sheet_ref: copiedSheetId,
    title: copyFile.getName(),
  };
}

function clearOutputSheetData() {
  const cfg = getOutputConfig();
  const ss = SpreadsheetApp.openById(cfg.sheetId);
  let sheet = ss.getSheetByName(cfg.tabName);
  if (!sheet) {
    sheet = ss.insertSheet(cfg.tabName);
    sheet.appendRow([
      'ID', 'Company Name', 'Company Website', 'Company LinkedIn',
      'Person Name', 'Person Role', 'Person Email', 'Person LinkedIn', 'Class'
    ]);
  }
  const clearedRows = Math.max(sheet.getLastRow() - 1, 0);
  clearWorksheetData(sheet);
  return {
    sheet_id: cfg.sheetId,
    tab_name: cfg.tabName,
    cleared_rows: clearedRows,
  };
}

function authorizeRotationAccess() {
  DriveApp.getRootFolder();
  SpreadsheetApp.getActive();
  return true;
}

function normalizeOutputConfig(config) {
  const data = config || {};
  return {
    sheetId: String(data.sheetId || data.sheet_id || DEFAULT_OUTPUT_CONFIG.sheetId).trim(),
    tabName: String(data.tabName || data.tab_name || DEFAULT_OUTPUT_CONFIG.tabName).trim(),
  };
}

function getOutputConfig() {
  const raw = PropertiesService.getScriptProperties().getProperty(OUTPUT_CONFIG_PROPERTY);
  if (!raw) return normalizeOutputConfig(DEFAULT_OUTPUT_CONFIG);
  try {
    return normalizeOutputConfig(JSON.parse(raw));
  } catch (_err) {
    return normalizeOutputConfig(DEFAULT_OUTPUT_CONFIG);
  }
}

function saveOutputConfig(config) {
  const normalized = normalizeOutputConfig(config);
  PropertiesService.getScriptProperties().setProperty(
    OUTPUT_CONFIG_PROPERTY,
    JSON.stringify(normalized)
  );
  return normalized;
}

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents || '{}');
    if (!payload || payload.token !== OUTPUT_SECRET) {
      return ContentService.createTextOutput(JSON.stringify({
        status: 'unauthorized', results: []
      })).setMimeType(ContentService.MimeType.JSON);
    }

    if (payload.action === 'sync_output_sheet_config') {
      var saved = saveOutputConfig(payload.output || {});
      return ContentService.createTextOutput(JSON.stringify({
        status: 'ok',
        message: 'output config synced',
        output: saved
      })).setMimeType(ContentService.MimeType.JSON);
    }

    if (payload.action === 'create_rotation_copy') {
      var created = createOutputRotationCopy(payload.rotation || {});
      return ContentService.createTextOutput(JSON.stringify({
        status: 'ok',
        message: 'output rotation copy created',
        copy: created
      })).setMimeType(ContentService.MimeType.JSON);
    }

    if (payload.action === 'clear_output_sheet') {
      var cleared = clearOutputSheetData();
      return ContentService.createTextOutput(JSON.stringify({
        status: 'ok',
        message: 'output sheet cleared',
        output: cleared
      })).setMimeType(ContentService.MimeType.JSON);
    }

    var companies = payload.companies;
    if (!Array.isArray(companies) || companies.length === 0) {
      return ContentService.createTextOutput(JSON.stringify({
        status: 'no_companies', results: []
      })).setMimeType(ContentService.MimeType.JSON);
    }

    var cfg = getOutputConfig();
    var ss = SpreadsheetApp.openById(cfg.sheetId);
    var sheet = ss.getSheetByName(cfg.tabName);
    if (!sheet) {
      sheet = ss.insertSheet(cfg.tabName);
      sheet.appendRow([
        'ID', 'Company Name', 'Company Website', 'Company LinkedIn',
        'Person Name', 'Person Role', 'Person Email', 'Person LinkedIn', 'Class'
      ]);
    }

    // Build index of existing company IDs -> row ranges for idempotency
    var existingIndex = _buildCompanyIndex(sheet);

    var results = [];
    // Process companies in reverse order so row deletions don't shift
    // the indices of earlier companies we haven't processed yet.
    var companyOps = [];

    for (var c = 0; c < companies.length; c++) {
      var company = companies[c];
      var companyId = (company.id || '').toString().trim();
      var companyName = company.name || '';
      var companyWebsite = company.website || '';
      var companyLinkedin = company.linkedin || '';
      var classLabel = company['class'] || '';
      var persons = company.persons || [];

      if (!companyId) {
        results.push({ id: companyId, status: 'error', reason: 'missing_id' });
        continue;
      }

      var newRows = [];
      // Company header row
      newRows.push([
        companyId, companyName, companyWebsite, companyLinkedin,
        '', '', '', '', classLabel
      ]);
      // Person rows
      for (var p = 0; p < persons.length; p++) {
        var person = persons[p];
        newRows.push([
          '', '', '', '',
          person.name || '',
          person.role || '',
          person.email || '',
          person.linkedin || '',
          classLabel
        ]);
      }

      var existing = existingIndex[companyId];
      if (existing) {
        // Update: delete old rows, then append new ones
        companyOps.push({
          id: companyId,
          action: 'update',
          deleteStart: existing.startRow,
          deleteCount: existing.rowCount,
          newRows: newRows
        });
        results.push({ id: companyId, status: 'updated', persons: persons.length });
      } else {
        companyOps.push({
          id: companyId,
          action: 'insert',
          newRows: newRows
        });
        results.push({ id: companyId, status: 'inserted', persons: persons.length });
      }
    }

    // Execute deletions first (from bottom to top to preserve row indices)
    var deletions = companyOps
      .filter(function(op) { return op.action === 'update'; })
      .sort(function(a, b) { return b.deleteStart - a.deleteStart; });

    for (var d = 0; d < deletions.length; d++) {
      sheet.deleteRows(deletions[d].deleteStart, deletions[d].deleteCount);
    }

    // Now append all new rows (both inserts and updates)
    var allNewRows = [];
    for (var i = 0; i < companyOps.length; i++) {
      for (var r = 0; r < companyOps[i].newRows.length; r++) {
        allNewRows.push(companyOps[i].newRows[r]);
      }
    }

    if (allNewRows.length > 0) {
      var startRow = sheet.getLastRow() + 1;
      sheet.getRange(startRow, 1, allNewRows.length, 9).setValues(allNewRows);
    }

    var inserted = results.filter(function(r) { return r.status === 'inserted'; }).length;
    var updated = results.filter(function(r) { return r.status === 'updated'; }).length;

    return ContentService.createTextOutput(JSON.stringify({
      status: 'ok',
      inserted: inserted,
      updated: updated,
      total: companies.length,
      results: results
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      status: 'error',
      message: err.toString(),
      results: []
    })).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Build an index of existing company IDs to their row ranges.
 * A company block starts at a row with a non-empty ID (col A)
 * and continues through consecutive rows with empty ID (person rows).
 */
function _buildCompanyIndex(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return {};

  var ids = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  var index = {};

  for (var i = 0; i < ids.length; i++) {
    var id = (ids[i][0] || '').toString().trim();
    if (!id) continue;

    var startRow = i + 2; // +2 because data starts at row 2
    var rowCount = 1;
    // Count person rows (empty ID) that follow this company header
    for (var j = i + 1; j < ids.length; j++) {
      var nextId = (ids[j][0] || '').toString().trim();
      if (nextId) break;
      rowCount++;
    }
    index[id] = { startRow: startRow, rowCount: rowCount };
  }

  return index;
}

function doGet(e) {
  return ContentService.createTextOutput(JSON.stringify({ status: 'output endpoint active' }))
    .setMimeType(ContentService.MimeType.JSON);
}

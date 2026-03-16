/**
 * GOOGLE APPS SCRIPT: LINKEDIN SCRAPER + LEAD TRANSFER + EMPLOYEE DB OUTPUT
 *
 * Deployed on source sheet: 1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc
 *
 * This merges the existing scraper/transfer logic with the new Employee_db output.
 */

// ==========================================
// 1. GLOBAL CONFIGURATION
// ==========================================
var SOURCE_SHEET_ID = "1YF8WvLTPu-Raf22rHaaphauj-w-n64CRu83ZeAtHYAc";
var TABS_TO_PROCESS = ["alpha", "beta", "gamma"];

// --- SCRAPER CONFIG (Column I) ---
var URL_COLUMN = "website";
var STATUS_COLUMN = "Scrape Status";

// --- TRANSFER CONFIG (Column H) ---
const TRANSFER_CONFIG = {
  DESTINATION_URL: "https://docs.google.com/spreadsheets/d/140KaXgBgoJUZuH6rbtZmnEpJFAL4k7WhUoWSNNtfzQ0/edit",
  DESTINATION_TAB_NAME: "Sheet1",
  SOURCE_TABS: ["Alpha", "Beta", "Gamma"],
  INSERTION_MODE: "TOP",
  STATUS_HEADER: "Eng status",
  TRIGGER_VALUE: "Pending",
  MOVED_VALUE: "Transferred",
  FIELD_MAPPING: [
    "name",
    "website",
    "LinkedIn",
    "LinkedIn people link",
    "LinkedIn employees",
    "Person 1",
    "Person 2"
  ]
};

// ==========================================
// 2. GET URLs (Scraper Logic) - returns unprocessed rows from Alpha/Beta/Gamma
// ==========================================
function doGet(e) {
    try {
        var ss = SpreadsheetApp.openById(SOURCE_SHEET_ID);
        var allUrls = [];

        TABS_TO_PROCESS.forEach(function (tabName) {
            var sheet = ss.getSheetByName(tabName);
            if (!sheet) return;

            var data = sheet.getDataRange().getValues();
            if (data.length < 1) return;

            var headers = data[0].map(function (h) { return h.toString().toLowerCase().trim(); });
            var urlColIndex = headers.indexOf(URL_COLUMN.toLowerCase());
            var statusColIndex = headers.indexOf(STATUS_COLUMN.toLowerCase());
            var nameColIndex = headers.indexOf("name");
            var linkedinColIndex = headers.indexOf("linkedin");
            var empCountColIndex = headers.indexOf("linkedin employees");
            var peopleLinkColIndex = headers.indexOf("linkedin people link");

            if (statusColIndex === -1) {
                statusColIndex = headers.length;
                sheet.getRange(1, statusColIndex + 1).setValue(STATUS_COLUMN);
            }

            for (var i = 1; i < data.length; i++) {
                var url = urlColIndex >= 0 ? data[i][urlColIndex] : "";
                var status = statusColIndex < data[i].length ? data[i][statusColIndex] : "";

                if (url && url.toString().trim() !== "" && (!status || status.toString().trim() === "")) {
                    allUrls.push({
                        tab: tabName,
                        row: i + 1,
                        url: url.toString().trim(),
                        name: nameColIndex >= 0 ? (data[i][nameColIndex] || "").toString().trim() : "",
                        website: url.toString().trim(),
                        linkedin: linkedinColIndex >= 0 ? (data[i][linkedinColIndex] || "").toString().trim() : "",
                        employees: empCountColIndex >= 0 ? (data[i][empCountColIndex] || "").toString().trim() : "",
                        people_link: peopleLinkColIndex >= 0 ? (data[i][peopleLinkColIndex] || "").toString().trim() : ""
                    });
                }
            }
        });

        return ContentService.createTextOutput(JSON.stringify({
            urls: allUrls,
            total: allUrls.length
        })).setMimeType(ContentService.MimeType.JSON);

    } catch (error) {
        return ContentService.createTextOutput(JSON.stringify({ error: error.toString() })).setMimeType(ContentService.MimeType.JSON);
    }
}

// ==========================================
// 3. POST (Scraper Results + Employee_db output + mark_processed)
// ==========================================
function doPost(e) {
    try {
        var jsonData = JSON.parse(e.postData.contents);
        var action = jsonData.action || "";

        // --- New: mark_processed action ---
        if (action === "mark_processed") {
            return handleMarkProcessed(jsonData);
        }

        // --- Legacy: single-site scraper result (status update only) ---
        var urls = jsonData.linkedin_urls || [];
        var emails = jsonData.emails || [];
        var tab = jsonData.tab;
        var row = jsonData.row;

        if (tab && row) {
            updateSheetStatus(tab, row, urls.length, emails.length);
        }

        return ContentService.createTextOutput("Success");
    } catch (error) {
        return ContentService.createTextOutput("Error: " + error.toString());
    }
}

// ==========================================
// 3a. Mark rows as processed in source sheet
// ==========================================
function handleMarkProcessed(jsonData) {
    var ss = SpreadsheetApp.openById(SOURCE_SHEET_ID);
    var updates = jsonData.updates || [];

    for (var u = 0; u < updates.length; u++) {
        var upd = updates[u];
        var sheet = ss.getSheetByName(upd.tab);
        if (!sheet) continue;

        var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
        var headerLower = headers.map(function (h) { return h.toString().toLowerCase().trim(); });
        var statusColIndex = headerLower.indexOf(STATUS_COLUMN.toLowerCase());

        if (statusColIndex === -1) {
            statusColIndex = headers.length;
            sheet.getRange(1, statusColIndex + 1).setValue(STATUS_COLUMN);
        }

        sheet.getRange(upd.row, statusColIndex + 1).setValue(upd.status || "done");
    }

    return ContentService.createTextOutput("ok").setMimeType(ContentService.MimeType.TEXT);
}

// ==========================================
// 3b. Legacy status update
// ==========================================
function updateSheetStatus(tabName, row, linkedinCount, emailCount) {
    var ss = SpreadsheetApp.openById(SOURCE_SHEET_ID);
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return;

    var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    var headerLower = headers.map(function (h) { return h.toString().toLowerCase().trim(); });
    var statusColIndex = headerLower.indexOf(STATUS_COLUMN.toLowerCase());

    if (statusColIndex === -1) {
        statusColIndex = headers.length;
        sheet.getRange(1, statusColIndex + 1).setValue(STATUS_COLUMN);
    }

    var statusText = linkedinCount + " LinkedIn and " + emailCount + " emails found";
    sheet.getRange(row, statusColIndex + 1).setValue(statusText);
}

// ==========================================
// 4. AUTOMATIC TRANSFER (Triggered by Edit)
// ==========================================
function transferLeadOnEdit(e) {
  if (!e) return;

  const sheet = e.range.getSheet();
  if (!TRANSFER_CONFIG.SOURCE_TABS.includes(sheet.getName())) return;
  if (e.value !== TRANSFER_CONFIG.TRIGGER_VALUE) return;

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const statusColIndex = headers.indexOf(TRANSFER_CONFIG.STATUS_HEADER) + 1;

  if (e.range.getColumn() !== statusColIndex) return;

  const rowValues = sheet.getRange(e.range.getRow(), 1, 1, sheet.getLastColumn()).getValues()[0];

  const payload = TRANSFER_CONFIG.FIELD_MAPPING.map(headerName => {
    const idx = headers.indexOf(headerName);
    return idx > -1 ? rowValues[idx] : "";
  });

  try {
    const destSs = SpreadsheetApp.openByUrl(TRANSFER_CONFIG.DESTINATION_URL);
    const destSheet = destSs.getSheetByName(TRANSFER_CONFIG.DESTINATION_TAB_NAME);

    if (destSheet) {
      writeDataToDestination(destSheet, payload);
      SpreadsheetApp.getActiveSpreadsheet().toast("Lead Transferred!", "Success");
    }
  } catch (err) {
    console.error(err);
  }
}

// ==========================================
// 5. FORCE PUSH (MANUAL TOOL)
// ==========================================
function moveAllPendingNow() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  try {
    const destSs = SpreadsheetApp.openByUrl(TRANSFER_CONFIG.DESTINATION_URL);
    const destSheet = destSs.getSheetByName(TRANSFER_CONFIG.DESTINATION_TAB_NAME);
    if (!destSheet) throw new Error(`Destination tab '${TRANSFER_CONFIG.DESTINATION_TAB_NAME}' not found.`);

    let totalMoved = 0;

    TRANSFER_CONFIG.SOURCE_TABS.forEach(tabName => {
      const sheet = ss.getSheetByName(tabName);
      if (!sheet) return;

      const data = sheet.getDataRange().getValues();
      const headers = data[0];

      const statusIdx = headers.findIndex(h => h.toString().trim() === TRANSFER_CONFIG.STATUS_HEADER);

      if (statusIdx === -1) {
        console.log(`ERROR: Could not find column '${TRANSFER_CONFIG.STATUS_HEADER}' in tab '${tabName}'`);
        return;
      }

      const colIndices = TRANSFER_CONFIG.FIELD_MAPPING.map(field =>
        headers.findIndex(h => h.toString().trim() === field)
      );

      for (let i = 1; i < data.length; i++) {
        if (data[i][statusIdx] === TRANSFER_CONFIG.TRIGGER_VALUE) {
          const payload = colIndices.map((idx) => (idx === -1 ? "" : data[i][idx]));

          destSheet.appendRow(payload);
          sheet.getRange(i + 1, statusIdx + 1).setValue(TRANSFER_CONFIG.MOVED_VALUE);
          totalMoved++;
        }
      }
    });

    ui.alert(`Success! Moved ${totalMoved} leads.`);

  } catch (e) {
    ui.alert(`Error: ${e.message}`);
  }
}

// ==========================================
// 6. HELPER: SMART INSERTION
// ==========================================
function writeDataToDestination(destSheet, payload) {
  if (TRANSFER_CONFIG.INSERTION_MODE === "TOP") {
    destSheet.insertRowBefore(2);
    destSheet.getRange(2, 1, 1, payload.length).setValues([payload]);
  } else {
    destSheet.appendRow(payload);
  }
}

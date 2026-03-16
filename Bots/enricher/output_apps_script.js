/**
 * OUTPUT APPS SCRIPT
 * Deploy on output sheet (Employee_db sheet: 1YTLSv2-vo8PkDJebRZzwbxIC3Agafw_Prj6bTfudZQ8)
 * Receives scraped company+person results and writes grouped rows to Employee_db tab.
 *
 * Grouped format per company:
 *   Header row: ID | Company Name | Company Website | Company LinkedIn | '' | '' | '' | '' | Class
 *   Person rows: '' | '' | '' | '' | Person Name | Person Role | Person Email | Person LinkedIn | Class
 */

const OUTPUT_SHEET_ID = '1YTLSv2-vo8PkDJebRZzwbxIC3Agafw_Prj6bTfudZQ8';
const OUTPUT_TAB = 'Employee_db';
const OUTPUT_SECRET = 'FMP-Scraper';

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents || '{}');
    if (!payload || payload.token !== OUTPUT_SECRET) {
      return ContentService.createTextOutput('unauthorized')
        .setMimeType(ContentService.MimeType.TEXT);
    }

    var companies = payload.companies;
    if (!Array.isArray(companies) || companies.length === 0) {
      return ContentService.createTextOutput('no companies')
        .setMimeType(ContentService.MimeType.TEXT);
    }

    var ss = SpreadsheetApp.openById(OUTPUT_SHEET_ID);
    var sheet = ss.getSheetByName(OUTPUT_TAB);
    if (!sheet) {
      sheet = ss.insertSheet(OUTPUT_TAB);
      sheet.appendRow([
        'ID', 'Company Name', 'Company Website', 'Company LinkedIn',
        'Person Name', 'Person Role', 'Person Email', 'Person LinkedIn', 'Class'
      ]);
    }

    var allRows = [];

    for (var c = 0; c < companies.length; c++) {
      var company = companies[c];
      var companyId = company.id || '';
      var companyName = company.name || '';
      var companyWebsite = company.website || '';
      var companyLinkedin = company.linkedin || '';
      var classLabel = company['class'] || '';
      var persons = company.persons || [];

      // Company header row
      allRows.push([
        companyId, companyName, companyWebsite, companyLinkedin,
        '', '', '', '', classLabel
      ]);

      // Person rows
      for (var p = 0; p < persons.length; p++) {
        var person = persons[p];
        allRows.push([
          '', '', '', '',
          person.name || '',
          person.role || '',
          person.email || '',
          person.linkedin || '',
          classLabel
        ]);
      }
    }

    if (allRows.length > 0) {
      var startRow = sheet.getLastRow() + 1;
      sheet.getRange(startRow, 1, allRows.length, 9).setValues(allRows);
    }

    return ContentService.createTextOutput('ok: ' + companies.length + ' companies written')
      .setMimeType(ContentService.MimeType.TEXT);

  } catch (err) {
    return ContentService.createTextOutput('error: ' + err)
      .setMimeType(ContentService.MimeType.TEXT);
  }
}

function doGet(e) {
  return ContentService.createTextOutput(JSON.stringify({ status: 'output endpoint active' }))
    .setMimeType(ContentService.MimeType.JSON);
}

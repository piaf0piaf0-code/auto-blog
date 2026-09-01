/**
 * Google Sheets buttons for the blog automation workflow.
 *
 * Setup:
 * 1. Open the Google Sheet.
 * 2. Extensions > Apps Script.
 * 3. Paste this file into Code.gs and save.
 * 4. Reload the sheet. A menu named "블로그 자동화" will appear.
 * 5. Optional buttons:
 *    - Insert > Drawing > make a button named "선택완료"
 *      then assign script: moveSelectedToToday
 *    - Insert > Drawing > make a button named "삭제"
 *      then assign script: deleteUnselectedRows
 */

const SOURCE_SHEETS = {
  "1.최신이슈": "최신이슈",
  "2.정책지원": "정책지원",
  "3.대출": "대출관련",
  "4. 신장정신": "신장정신",
  "5.웰빙": "웰빙",
  "6.꿈해몽": "꿈해몽",
};

const TODAY_SHEET_NAME = "오늘작성";
const TODAY_HEADERS = [
  "원본 메인 키워드",
  "카테고리",
  "워드프레스 롱테일",
  "SEO 최적화 제목",
  "메타 디스크립션 및 태그",
  "HTML 본문 내용",
  "작업 상태",
  "대표 링크/결과 URL",
];

// ⚠️ onOpen 은 여기서 지웠습니다.
//    프로젝트 전체에 onOpen 이 하나만 있어야 하는데, 글쓰기 파일
//    (260816글쓰기.gs)의 onOpen 이 메뉴를 전부 만듭니다. 아래 두 기능도
//    거기서 알아서 메뉴에 넣어 줍니다.
//
//    글쓰기 파일을 안 쓰실 거면 아래 주석을 풀어서 되살리세요.
//
// function onOpen() {
//   SpreadsheetApp.getUi()
//     .createMenu("블로그 자동화")
//     .addItem("선택완료: 1 표시 행을 오늘작성으로 이동", "moveSelectedToToday")
//     .addItem("삭제: 선택 안 된 행 삭제", "deleteUnselectedRows")
//     .addToUi();
// }

function moveSelectedToToday() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  const category = SOURCE_SHEETS[sheet.getName()];

  if (!category) {
    SpreadsheetApp.getUi().alert("수집용 탭에서 실행하세요.");
    return;
  }

  const todaySheet = ensureTodaySheet_(ss);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("이동할 키워드가 없습니다.");
    return;
  }

  const values = sheet.getRange(2, 1, lastRow - 1, Math.max(7, sheet.getLastColumn())).getValues();
  const headers = sheet.getRange(1, 1, 1, Math.max(7, sheet.getLastColumn())).getValues()[0];
  const selectedCol = findColumn_(headers, ["챗선택", "선택"], 1) - 1;
  const keywordCol = findColumn_(headers, ["기본키워드", "메인 키워드"], 2) - 1;
  const sourceCol = findColumn_(headers, ["데이터 출처", "Source URL"], 5) - 1;
  const wordpressLongtailCol = findColumn_(headers, ["워드프레스 롱테일", "워드프레스용 롱테일"], 4) - 1;
  const rowsToMove = [];
  const sourceRowsToDelete = [];

  values.forEach((row, index) => {
    const selected = isSelected_(row[selectedCol]);
    const keyword = String(row[keywordCol] || "").trim();
    const sourceUrl = String(row[sourceCol] || "").trim();
    const wordpressLongtails = String(row[wordpressLongtailCol] || "").trim();
    if (!selected || !keyword) return;

    rowsToMove.push([keyword, category, wordpressLongtails, "", "", "", "글쓰기대기", sourceUrl]);
    sourceRowsToDelete.push(index + 2);
  });

  if (!rowsToMove.length) {
    SpreadsheetApp.getUi().alert("A열에 1 또는 체크된 행이 없습니다.");
    return;
  }

  todaySheet.getRange(todaySheet.getLastRow() + 1, 1, rowsToMove.length, TODAY_HEADERS.length).setValues(rowsToMove);
  deleteRowsFromBottom_(sheet, sourceRowsToDelete);

  SpreadsheetApp.getUi().alert(`${rowsToMove.length}개를 오늘작성으로 이동했습니다.`);
}

function deleteUnselectedRows() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  const category = SOURCE_SHEETS[sheet.getName()];

  if (!category) {
    SpreadsheetApp.getUi().alert("수집용 탭에서 실행하세요.");
    return;
  }

  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    SpreadsheetApp.getUi().alert("삭제할 키워드가 없습니다.");
    return;
  }

  const values = sheet.getRange(2, 1, lastRow - 1, Math.max(7, sheet.getLastColumn())).getValues();
  const headers = sheet.getRange(1, 1, 1, Math.max(7, sheet.getLastColumn())).getValues()[0];
  const selectedCol = findColumn_(headers, ["챗선택", "선택"], 1) - 1;
  const keywordCol = findColumn_(headers, ["기본키워드", "메인 키워드"], 2) - 1;
  const rowsToDelete = [];

  values.forEach((row, index) => {
    const selected = isSelected_(row[selectedCol]);
    const keyword = String(row[keywordCol] || "").trim();
    if (!keyword) return;
    if (!selected) rowsToDelete.push(index + 2);
  });

  if (!rowsToDelete.length) {
    SpreadsheetApp.getUi().alert("선택 안 된 키워드가 없습니다.");
    return;
  }

  deleteRowsFromBottom_(sheet, rowsToDelete);
  SpreadsheetApp.getUi().alert(`${rowsToDelete.length}개를 삭제했습니다.`);
}

function ensureTodaySheet_(ss) {
  let sheet = ss.getSheetByName(TODAY_SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(TODAY_SHEET_NAME);

  const firstRow = sheet.getRange(1, 1, 1, TODAY_HEADERS.length).getValues()[0];
  const hasHeader = firstRow.some((value) => String(value || "").trim());
  const headerMatches = TODAY_HEADERS.every((header, index) => String(firstRow[index] || "").trim() === header);
  if (!hasHeader || !headerMatches) {
    sheet.getRange(1, 1, 1, TODAY_HEADERS.length).setValues([TODAY_HEADERS]);
  }

  return sheet;
}

function isSelected_(value) {
  if (value === true) return true;
  const text = String(value || "").trim().toUpperCase();
  return text === "1" || text === "TRUE" || text === "Y" || text === "YES" || text === "승인";
}

function findColumn_(headers, names, fallback) {
  const normalizedHeaders = headers.map((header) => String(header || "").trim());
  for (const name of names) {
    const index = normalizedHeaders.indexOf(name);
    if (index >= 0) return index + 1;
  }
  return fallback;
}

function deleteRowsFromBottom_(sheet, rowNumbers) {
  rowNumbers
    .sort((a, b) => b - a)
    .forEach((rowNumber) => sheet.deleteRow(rowNumber));
}

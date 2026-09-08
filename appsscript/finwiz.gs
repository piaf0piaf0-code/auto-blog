/**
 * finwiz 로드맵 글쓰기 — 순서대로 쓰고, 링크는 알아서 걸린다.
 *
 * 하는 일
 *   1. 로드맵(엑셀)을 시트 탭 두 개로 옮긴다  → finwiz_작성순서 / finwiz_링크관리
 *   2. 다음에 쓸 글 한 편을 골라 요청문을 만든다
 *      - 제목은 로드맵에 적힌 제목 그대로 (바꾸지 않는다)
 *      - **이미 발행된 글 중에서만** 내부 링크를 골라 넣는다
 *   3. 붙여넣은 글을 오늘작성 탭에 새 행으로 저장한다 (발행대기)
 *   4. 발행 확인이 주소를 찾아내면 finwiz_링크관리 에 적는다
 *      → 그다음 글부터 그 주소를 링크로 쓸 수 있다
 *
 * 핵심 규칙 (사용자가 정한 방식)
 *   "3번 글에는 1번 글 링크를, 4번 글에는 1~3번 중 해당되는 글 링크를."
 *   즉 아직 안 쓴 글의 링크는 절대 넣지 않는다. 그래서 나중에 되돌아가
 *   이미 발행한 글을 고칠 일이 없다.
 *
 * 자료는 finwiz자료.gs 에 들어 있다. 이 파일에는 자료가 없다.
 */

// ══════════════════════════════════════════════════════════
//  설정
// ══════════════════════════════════════════════════════════

var finwiz순서탭 = 'finwiz_작성순서';
var finwiz링크탭 = 'finwiz_링크관리';

/** 이 로드맵 글이 들어갈 카테고리. 오늘작성 탭에 이 값으로 적힌다. */
var finwiz카테고리 = '대출관련';

/** 한 글에 넣을 내부 링크 최대 개수. */
var finwiz링크개수 = 3;

/** finwiz_작성순서 탭의 열 이름 (순서 그대로). */
var finwiz순서헤더 = ['순번', '단계', '클러스터', '우선순위', '메인키워드', '검색량',
                      '작성전략', '확인URL', '제목', '내ID',
                      '관련글1 ID', '관련글2 ID', '관련글3 ID',
                      '작성상태', '발행URL', '쓴날짜', '소제목'];

/** finwiz_링크관리 탭의 열 이름 (순서 그대로). */
var finwiz링크헤더 = ['ID', '글 제목', '상태', 'URL', '클러스터', '키워드'];

/** 주제별로 확인한 숫자를 모아 두는 탭. 같은 숫자를 167번 다시 찾지 않기 위한 것. */
var finwiz사실탭 = 'finwiz_사실카드';
var finwiz사실헤더 = ['클러스터', '항목', '값', '기준일', '출처URL'];

/** 사실카드가 이만큼 지나면 '원문에서 다시 확인하라'고 요청문에 적는다. */
var finwiz사실유효일 = 90;

/**
 * 본문에 있으면 저장을 막는 표현.
 *
 * ⚠ 여기서 잡아야 할 것은 '단어' 가 아니라 '주장' 이다.
 *   '신용불량자' 는 그냥 표준어다. finwiz 로드맵에도 제목으로 들어 있다.
 *   처음에 /(신용|연체|채무)\s*(불량|...)/ 로 잡았더니 정상 글이 막혔다.
 *   막아야 하는 것은 '신용 상관없이 가능' 같은 유인 문구다.
 *
 * ⚠ 경고하는 문장도 막으면 안 된다.
 *   '무조건 승인해 준다는 곳은 사기입니다' 는 좋은 글이다.
 *   그래서 걸린 자리 뒤에 부정·경고하는 말이 있으면 넘어간다(아래 finwiz경고맥락).
 */
var finwiz금지표현 = [
  { 패턴: /무조건\s*(승인|가능|대출)/,                이름: "'무조건 승인' 같은 단정 표현" },
  { 패턴: /100\s*%\s*(승인|가능|대출)/,              이름: "'100% 승인' 표현" },
  { 패턴: /확정\s*승인|승인\s*확정/,                 이름: "'승인 확정' 표현" },
  { 패턴: /누구나\s*(승인|받을\s*수\s*있|가능합니다)/, 이름: "'누구나 가능' 표현" },
  // '신용불량자' 라는 낱말이 아니라 '신용과 무관하게 된다' 는 주장을 잡는다
  { 패턴: /(신용|신용등급|연체|채무)\s*(이력\s*)?(상관\s*없|무관|안\s*보|묻지\s*않)/,
    이름: "'신용 상관없이' 류 표현" },
  { 패턴: /(신용불량자|연체자|무직자)\s*(도|라도)\s*(무조건|100\s*%|누구나|당연히)/,
    이름: '조건 없이 된다는 주장' },
  { 패턴: /작업\s*대출|무서류\s*대출|당일\s*무조건/, 이름: '불법 대출 연상 표현' },
  { 패턴: /\b0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}\b/, 이름: '전화번호' },
  // 안내가 아니라 '지금 신청하라' 고 시키는 말만 잡는다
  { 패턴: /(지금|여기서|아래에서|아래)\s*(바로\s*)?(신청|상담)\s*(하세요|해\s*보세요|하시면|하기)/,
    이름: '신청 유도 문구' },
  { 패턴: /202[0-5]\s*년\s*(기준|현재)/,             이름: '지난 연도 기준 표기',
    // 2026 과 견주는 문장이면 정상이다. "2025년 기준 X였으나 2026년부터 Y"
    비교허용: true }
];

/**
 * 걸린 자리 뒤에 이런 말이 있으면 경고하는 문장이다. 막지 않는다.
 * 예) "무조건 승인된다는 곳은 사기입니다", "누구나 가능한 것은 아닙니다"
 */
var finwiz경고맥락 = /(없습니다|없다|아닙니다|아니다|않습니다|않다|불가능|주의|조심|피하|사기|불법|과장|허위|의심|걸러)/;

/** 걸린 표현이 '경고하는 문장' 안에 있나. 있으면 막지 않는다. */
function finwiz경고문인가(글자, 자리, 길이) {
  var 뒤 = String(글자 || '').substr(자리 + 길이, 30);
  return finwiz경고맥락.test(뒤);
}

// ══════════════════════════════════════════════════════════
//  탭 만들기 / 로드맵 가져오기
// ══════════════════════════════════════════════════════════

function finwiz문서() {
  if (typeof 문서가져오기 === 'function') return 문서가져오기();
  return SpreadsheetApp.getActiveSpreadsheet();
}

function finwiz탭(이름, 헤더) {
  var 문서 = finwiz문서();
  var 탭 = 문서.getSheetByName(이름);
  if (!탭) {
    탭 = 문서.insertSheet(이름);
    탭.getRange(1, 1, 1, 헤더.length).setValues([헤더]);
    탭.setFrozenRows(1);
  } else if (탭.getLastRow() === 0) {
    탭.getRange(1, 1, 1, 헤더.length).setValues([헤더]);
    탭.setFrozenRows(1);
  }
  return 탭;
}

/**
 * 로드맵을 시트에 넣는다. 여러 번 눌러도 안전하다.
 *
 * 이미 적혀 있는 URL·상태·작성상태·발행URL 은 건드리지 않는다.
 * 그래야 엑셀이 바뀌어 다시 가져와도 지금까지 발행한 기록이 안 날아간다.
 */
function finwiz로드맵가져오기() {
  if (typeof finwiz링크자료 === 'undefined' || typeof finwiz순서자료 === 'undefined') {
    throw new Error(
      'finwiz자료.gs 파일이 없습니다.\n' +
      'Apps Script 편집기에서 finwiz자료.gs 를 먼저 추가해 주세요.'
    );
  }

  // ── 링크관리 ──────────────────────────────────────────
  var 링크 = finwiz탭(finwiz링크탭, finwiz링크헤더);
  var 이전URL = {}, 이전상태 = {};
  if (링크.getLastRow() >= 2) {
    링크.getRange(2, 1, 링크.getLastRow() - 1, finwiz링크헤더.length).getValues()
      .forEach(function (줄) {
        var id = String(줄[0] || '').trim();
        if (!id) return;
        if (String(줄[3] || '').trim()) 이전URL[id] = String(줄[3]).trim();
        if (String(줄[2] || '').trim()) 이전상태[id] = String(줄[2]).trim();
      });
  }

  var 링크줄 = finwiz링크자료.map(function (줄) {
    var id = String(줄[0]);
    var url = 이전URL[id] || String(줄[3] || '');
    var 상태 = 이전상태[id] || String(줄[2] || '');
    // 자료에는 미작성인데 그 사이 주소가 생겼으면 발행완료로 본다
    if (url && 상태 === '미작성') 상태 = '발행완료';
    return [id, String(줄[1]), 상태, url, String(줄[4] || ''), String(줄[5] || '')];
  });
  if (링크.getLastRow() >= 2) {
    링크.getRange(2, 1, 링크.getLastRow() - 1, 링크.getLastColumn()).clearContent();
  }
  링크.getRange(2, 1, 링크줄.length, finwiz링크헤더.length).setValues(링크줄);

  // ── 작성순서 ──────────────────────────────────────────
  var 순서 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  var 이전작성 = {}, 이전발행 = {}, 이전날짜 = {}, 이전소제목 = {};
  if (순서.getLastRow() >= 2) {
    순서.getRange(2, 1, 순서.getLastRow() - 1, finwiz순서헤더.length).getValues()
      .forEach(function (줄) {
        var id = String(줄[9] || '').trim();
        if (!id) return;
        if (String(줄[13] || '').trim()) 이전작성[id] = String(줄[13]).trim();
        if (String(줄[14] || '').trim()) 이전발행[id] = String(줄[14]).trim();
        if (String(줄[15] || '').trim()) 이전날짜[id] = String(줄[15]).trim();
        if (String(줄[16] || '').trim()) 이전소제목[id] = String(줄[16]).trim();
      });
  }

  var 제목찾기 = {};
  링크줄.forEach(function (줄) { 제목찾기[줄[0]] = 줄[1]; });

  var 순서줄 = finwiz순서자료.map(function (줄) {
    var id = String(줄[8] || '');
    var 발행 = 이전발행[id] || 링크줄.filter(function (ㄱ) { return ㄱ[0] === id; })
                                     .map(function (ㄱ) { return ㄱ[3]; })[0] || '';
    return [
      줄[0], String(줄[1] || ''), String(줄[2] || ''), String(줄[3] || ''),
      String(줄[4] || ''), 줄[5] === '' ? '' : 줄[5],
      String(줄[6] || ''), String(줄[7] || ''),
      제목찾기[id] || '', id,
      String(줄[9] || ''), String(줄[10] || ''), String(줄[11] || ''),
      이전작성[id] || (발행 ? '발행완료' : '미작성'),
      발행, 이전날짜[id] || '', 이전소제목[id] || ''
    ];
  });
  if (순서.getLastRow() >= 2) {
    순서.getRange(2, 1, 순서.getLastRow() - 1, 순서.getLastColumn()).clearContent();
  }
  순서.getRange(2, 1, 순서줄.length, finwiz순서헤더.length).setValues(순서줄);
  순서.autoResizeColumn(9);

  // ── 사실카드 틀 ──────────────────────────────────────
  var 카드결과 = finwiz카드틀깔기();

  SpreadsheetApp.flush();
  return {
    글수: 순서줄.length,
    링크수: 링크줄.length,
    주소있음: 링크줄.filter(function (줄) { return !!줄[3]; }).length,
    카드항목: 카드결과.전체,
    카드채움: 카드결과.채움
  };
}

/**
 * 사실카드 탭에 '무엇을 확인해야 하는가' 목록을 깔아 둔다.
 *
 * 값은 비워 둔다. 값은 사용자가 공식 페이지에서 확인해 채운다.
 * 이미 채워 둔 값과 기준일은 절대 지우지 않는다.
 */
function finwiz카드틀깔기() {
  if (typeof finwiz카드틀 === 'undefined') return { 전체: 0, 채움: 0 };

  var 출처표 = {};
  if (typeof finwiz출처자료 !== 'undefined') {
    finwiz출처자료.forEach(function (줄) { 출처표[줄[0]] = 줄[3]; });
  }

  var 탭 = finwiz탭(finwiz사실탭, finwiz사실헤더);
  var 이전 = {};
  if (탭.getLastRow() >= 2) {
    탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz사실헤더.length).getValues()
      .forEach(function (줄) {
        var 열쇠 = String(줄[0] || '').trim() + '\u0000' + finwiz소제목열쇠(줄[1]);
        var 값 = String(줄[2] || '').trim();
        if (값) 이전[열쇠] = { 값: 값, 기준일: finwiz날짜글자(줄[3]),
                              출처: String(줄[4] || '').trim() };
      });
  }

  var 줄들 = [], 채움 = 0, 남은것 = {};
  for (var 열쇠2 in 이전) 남은것[열쇠2] = true;

  for (var 클러스터 in finwiz카드틀) {
    finwiz카드틀[클러스터].forEach(function (한개) {
      var 열쇠 = 클러스터 + '\u0000' + finwiz소제목열쇠(한개[0]);
      var 옛것 = 이전[열쇠];
      delete 남은것[열쇠];
      if (옛것) 채움++;
      줄들.push([클러스터, 한개[0], 옛것 ? 옛것.값 : '',
                 옛것 ? 옛것.기준일 : '',
                 (옛것 && 옛것.출처) ? 옛것.출처 : (출처표[한개[1]] || '')]);
    });
  }

  // 틀에 없지만 사용자가 직접 넣어 둔 줄은 뒤에 그대로 살린다
  if (탭.getLastRow() >= 2) {
    탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz사실헤더.length).getValues()
      .forEach(function (줄) {
        var 클러스터 = String(줄[0] || '').trim();
        var 항목 = String(줄[1] || '').trim();
        if (!클러스터 || !항목) return;
        var 열쇠 = 클러스터 + '\u0000' + finwiz소제목열쇠(항목);
        if (!남은것[열쇠]) return;
        delete 남은것[열쇠];
        줄들.push([클러스터, 항목, String(줄[2] || '').trim(),
                   finwiz날짜글자(줄[3]), String(줄[4] || '').trim()]);
        if (String(줄[2] || '').trim()) 채움++;
      });
  }

  if (탭.getLastRow() >= 2) {
    탭.getRange(2, 1, 탭.getLastRow() - 1, 탭.getLastColumn()).clearContent();
  }
  if (줄들.length) {
    탭.getRange(2, 1, 줄들.length, finwiz사실헤더.length).setValues(줄들);
  }
  return { 전체: 줄들.length, 채움: 채움 };
}

function finwiz로드맵메뉴() {
  var ui = SpreadsheetApp.getUi();
  try {
    var 결과 = finwiz로드맵가져오기();
    ui.alert(
      'finwiz 로드맵을 가져왔습니다.\n\n' +
      '· ' + finwiz순서탭 + ' : 쓸 글 ' + 결과.글수 + '편\n' +
      '· ' + finwiz링크탭 + ' : 글 ' + 결과.링크수 + '개 (주소 있는 글 ' + 결과.주소있음 + '개)\n\n' +
      '이제 메뉴에서 "🧭 finwiz 순서대로 글쓰기" 를 누르면 1번 글부터 씁니다.'
    );
  } catch (오류) {
    ui.alert('가져오지 못했습니다.\n\n' + 오류.message);
  }
}


// ══════════════════════════════════════════════════════════
//  읽기
// ══════════════════════════════════════════════════════════

function finwiz링크읽기() {
  var 탭 = finwiz탭(finwiz링크탭, finwiz링크헤더);
  var 맵 = {};
  if (탭.getLastRow() < 2) return 맵;
  탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz링크헤더.length).getValues()
    .forEach(function (줄, i) {
      var id = String(줄[0] || '').trim();
      if (!id) return;
      맵[id] = {
        ID: id, 행: i + 2,
        제목: String(줄[1] || '').trim(),
        상태: String(줄[2] || '').trim(),
        URL: String(줄[3] || '').trim(),
        클러스터: String(줄[4] || '').trim(),
        키워드: String(줄[5] || '').trim()
      };
    });
  return 맵;
}

function finwiz순서읽기() {
  var 탭 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  if (탭.getLastRow() < 2) return [];
  return 탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz순서헤더.length).getValues()
    .map(function (줄, i) {
      return {
        행: i + 2,
        순번: Number(줄[0]) || 0,
        단계: String(줄[1] || '').trim(),
        클러스터: String(줄[2] || '').trim(),
        우선순위: String(줄[3] || '').trim(),
        메인키워드: String(줄[4] || '').trim(),
        검색량: String(줄[5] || '').trim(),
        작성전략: String(줄[6] || '').trim(),
        확인URL: String(줄[7] || '').trim(),
        제목: String(줄[8] || '').trim(),
        ID: String(줄[9] || '').trim(),
        관련: [String(줄[10] || '').trim(), String(줄[11] || '').trim(), String(줄[12] || '').trim()]
                .filter(function (v) { return !!v; }),
        작성상태: String(줄[13] || '').trim(),
        발행URL: String(줄[14] || '').trim(),
        쓴날짜: String(줄[15] || '').trim(),
        소제목: String(줄[16] || '').trim()
      };
    })
    .filter(function (한줄) { return !!한줄.ID; });
}


// ══════════════════════════════════════════════════════════
//  내부 링크 고르기
//
//  주소가 이미 있는 글만 후보다. 아직 안 쓴 글은 절대 넣지 않는다.
//  순서:
//    1) 로드맵이 지정한 관련글 1~3 중 주소가 있는 것
//    2) 같은 클러스터에서 주소가 있는 글 (가까운 순번 → 기존글 순)
//    3) 그래도 모자라면 메인키워드와 겹치는 말이 있는 글
// ══════════════════════════════════════════════════════════

/** 키워드를 낱말로 쪼갠다. 한 글자짜리는 버린다. */
function finwiz낱말(글자) {
  return String(글자 || '').toLowerCase()
    .replace(/[^0-9a-z가-힣]+/g, ' ')
    .split(' ')
    .filter(function (말) { return 말.length >= 2; });
}

function finwiz링크고르기(대상, 링크맵, 순서목록) {
  var 고른것 = [], 담음 = {};
  담음[대상.ID] = true;

  function 넣기(id, 이유) {
    if (!id || 담음[id]) return;
    var 글 = 링크맵[id];
    if (!글 || !글.URL) return;
    담음[id] = true;
    고른것.push({ ID: id, 제목: 글.제목, URL: 글.URL, 이유: 이유 });
  }

  // 1) 로드맵이 지정한 관련글
  대상.관련.forEach(function (id) { 넣기(id, '로드맵 지정'); });
  if (고른것.length >= finwiz링크개수) return 고른것.slice(0, finwiz링크개수);

  // 2) 같은 클러스터 — 계획글은 순번이 가까운 것부터
  var 순번찾기 = {};
  순서목록.forEach(function (한줄) { 순번찾기[한줄.ID] = 한줄.순번; });

  var 같은클러스터 = [];
  for (var id in 링크맵) {
    var 글 = 링크맵[id];
    if (!글.URL || 담음[id]) continue;
    if (글.클러스터 !== 대상.클러스터) continue;
    같은클러스터.push({
      ID: id,
      거리: (순번찾기[id] === undefined) ? 999 : Math.abs(순번찾기[id] - 대상.순번)
    });
  }
  같은클러스터.sort(function (가, 나) { return 가.거리 - 나.거리; });
  같은클러스터.forEach(function (한개) {
    if (고른것.length < finwiz링크개수) 넣기(한개.ID, '같은 주제(' + 대상.클러스터 + ')');
  });
  if (고른것.length >= finwiz링크개수) return 고른것.slice(0, finwiz링크개수);

  // 3) 말이 겹치는 글
  var 내낱말 = finwiz낱말(대상.메인키워드 + ' ' + 대상.제목);
  var 겹침 = [];
  for (var id2 in 링크맵) {
    var 글2 = 링크맵[id2];
    if (!글2.URL || 담음[id2]) continue;
    var 남낱말 = finwiz낱말(글2.키워드 + ' ' + 글2.제목);
    var 수 = 0;
    내낱말.forEach(function (말) { if (남낱말.indexOf(말) !== -1) 수++; });
    if (수 > 0) 겹침.push({ ID: id2, 수: 수 });
  }
  겹침.sort(function (가, 나) { return 나.수 - 가.수; });
  겹침.forEach(function (한개) {
    if (고른것.length < finwiz링크개수) 넣기(한개.ID, '겹치는 키워드');
  });

  return 고른것.slice(0, finwiz링크개수);
}


// ══════════════════════════════════════════════════════════
//  공식 출처 — 이 주제는 어디를 보고 써야 하는가
//
//  로드맵의 '작성시 확인 URL' 은 167편 중 69편에만 있다.
//  나머지 98편도 근거 없이 쓰면 안 되므로 주제별 공식 출처를 붙인다.
// ══════════════════════════════════════════════════════════

function finwiz출처찾기(대상) {
  var 나온것 = [], 담음 = {};

  // 로드맵이 그 글에 직접 지정한 URL 이 가장 우선이다
  if (대상.확인URL) {
    담음[대상.확인URL] = true;
    나온것.push({ 기관: '로드맵 지정', 주제: 대상.메인키워드,
                  URL: 대상.확인URL, 메모: '' });
  }

  if (typeof finwiz주제출처 === 'undefined' || typeof finwiz출처자료 === 'undefined') {
    return 나온것;
  }
  var 표 = {};
  finwiz출처자료.forEach(function (줄) {
    표[줄[0]] = { 기관: 줄[1], 주제: 줄[2], URL: 줄[3], 메모: 줄[4] };
  });

  (finwiz주제출처[대상.클러스터] || []).forEach(function (id) {
    var 하나 = 표[id];
    if (!하나 || 담음[하나.URL]) return;
    담음[하나.URL] = true;
    나온것.push(하나);
  });
  return 나온것.slice(0, 4);
}


// ══════════════════════════════════════════════════════════
//  사실카드 — 같은 숫자를 167번 다시 찾지 않기 위한 것
//
//  햇살론 한도·금리 같은 값은 한 클러스터 안의 글 십여 편이 똑같이 쓴다.
//  글마다 봇에게 다시 찾게 하면 (1) 매번 틀릴 기회가 생기고
//  (2) 글끼리 숫자가 어긋나고 (3) 값이 바뀌면 손댈 곳이 열 군데가 된다.
//
//  그래서 주제마다 한 번만 확인해 시트에 박아 두고, 그 주제의 모든 글
//  요청문에 "이 숫자만 쓰세요" 로 넣는다. 값이 바뀌면 칸 하나만 고치면 된다.
// ══════════════════════════════════════════════════════════

function finwiz사실읽기() {
  var 탭 = finwiz탭(finwiz사실탭, finwiz사실헤더);
  var 맵 = {};
  if (탭.getLastRow() < 2) return 맵;
  탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz사실헤더.length).getValues()
    .forEach(function (줄, i) {
      var 클러스터 = String(줄[0] || '').trim();
      var 항목 = String(줄[1] || '').trim();
      if (!클러스터 || !항목) return;
      if (!맵[클러스터]) 맵[클러스터] = [];
      맵[클러스터].push({
        행: i + 2, 클러스터: 클러스터, 항목: 항목,
        값: String(줄[2] || '').trim(),
        기준일: finwiz날짜글자(줄[3]),
        출처URL: String(줄[4] || '').trim()
      });
    });
  return 맵;
}

function finwiz날짜글자(값) {
  if (!값) return '';
  if (Object.prototype.toString.call(값) === '[object Date]') {
    return Utilities.formatDate(값, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  return String(값).trim().substring(0, 10);
}

/**
 * 봇이 준 출처 칸에서 주소만 뽑는다.
 * `[주소](주소)` 나 `제목 | 주소` 처럼 써 주는 일이 있어 그대로 두면 두 번 들어간다.
 */
function finwiz주소만(글자) {
  var 값 = String(글자 || '').trim();
  if (!값) return '';
  var m = /\((https?:\/\/[^)\s]+)\)/.exec(값);      // [무엇](주소)
  if (m) return m[1];
  m = /(https?:\/\/[^\s|)\]]+)/.exec(값);            // 글 안의 첫 주소
  if (m) return m[1];
  return 값;
}

/** 기준일로부터 며칠 지났나. 모르면 -1. */
function finwiz며칠지났나(기준일) {
  var 글자 = finwiz날짜글자(기준일);
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(글자);
  if (!m) return -1;
  var 그때 = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Math.floor((new Date().getTime() - 그때.getTime()) / 86400000);
}

/** 이 주제의 사실카드 상태. 없으면 없음, 오래됐으면 오래됨. */
function finwiz카드상태(클러스터, 사실맵) {
  var 모든줄 = (사실맵 || finwiz사실읽기())[클러스터] || [];
  // 값이 비어 있는 줄은 '아직 확인 안 한 항목'이다. 카드가 있다고 보지 않는다.
  var 줄들 = 모든줄.filter(function (하나) { return !!하나.값; });
  if (!줄들.length) {
    return { 있음: false, 개수: 0, 빈줄: 모든줄.length,
             오래됨: false, 지난날: -1, 줄들: [] };
  }
  var 최대 = -1, 오래된줄 = 0;
  줄들.forEach(function (하나) {
    var d = finwiz며칠지났나(하나.기준일);
    하나.지난날 = d;
    if (d > finwiz사실유효일) 오래된줄++;
    if (d > 최대) 최대 = d;
  });

  // 한 줄만 오래됐다고 카드 전체를 '오래됨'으로 보면 경고가 무뎌진다.
  // (봇이 옛 제도 한 줄을 참고로 넣어 두는 일이 실제로 있었다.)
  // 절반을 넘게 오래됐을 때만 카드 전체를 다시 확인하라고 한다.
  // 나머지는 그 줄에만 표시를 붙인다.
  return {
    있음: true, 개수: 줄들.length, 빈줄: 모든줄.length - 줄들.length,
    오래됨: (오래된줄 * 2 > 줄들.length),
    오래된줄: 오래된줄, 지난날: 최대, 줄들: 줄들
  };
}

/**
 * 사실카드를 만들어 달라고 할 요청문.
 * 주제마다 한 번만 하면 된다. 그 주제의 글 전부가 이걸 쓴다.
 */
function finwiz사실카드요청문(클러스터) {
  var 순서목록 = finwiz순서읽기();
  var 이주제 = 순서목록.filter(function (한줄) { return 한줄.클러스터 === 클러스터; });
  if (!이주제.length) throw new Error("'" + 클러스터 + "' 주제를 찾지 못했습니다.");

  var 대표 = 이주제[0];
  var 출처 = finwiz출처찾기({ 클러스터: 클러스터, 확인URL: '', 메인키워드: 대표.메인키워드 });
  var 키워드들 = [];
  이주제.forEach(function (한줄) {
    if (한줄.메인키워드 && 키워드들.indexOf(한줄.메인키워드) === -1) 키워드들.push(한줄.메인키워드);
  });

  var 오늘 = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  var 조각 = [
    '[하는 일]',
    "'" + 클러스터 + "' 주제로 블로그 글 " + 이주제.length + '편을 쓸 예정입니다.',
    '글마다 숫자를 다시 찾으면 글끼리 숫자가 어긋납니다.',
    '그래서 **지금 한 번만** 공식 사이트에서 확인해 표로 정리해 주세요.',
    '',
    '[반드시 열어서 확인할 공식 페이지]'
  ];
  출처.forEach(function (하나) {
    조각.push('- ' + 하나.기관 + ' · ' + 하나.주제 + ' : ' + 하나.URL +
              (하나.메모 ? '  (' + 하나.메모 + ')' : ''));
  });
  조각.push('');
  조각.push('[이 주제에서 다룰 키워드]');
  조각.push(키워드들.slice(0, 15).join(', '));
  조각.push('');
  조각.push('[규칙]');
  조각.push('- 위 공식 페이지를 실제로 열어서 확인한 값만 적어 주세요.');
  조각.push('- 기억이나 추정으로 적지 마세요. 페이지에서 못 찾으면 그 줄은 빼세요.');
  조각.push('- 값에는 단위를 붙여 주세요. 예: `연 3.6%`, `최대 1,200만원`, `연소득 4,500만원 이하`');
  조각.push('- 기준일은 그 페이지에 적힌 시행일·갱신일을 쓰고, 없으면 오늘(' + 오늘 + ')로 하세요.');
  조각.push('- 출처는 위 목록에 있는 주소 중 그 값이 실제로 적혀 있던 주소를 쓰세요.');
  조각.push('- 아래 [채울 항목] 을 그대로 쓰고 값만 채워 주세요. 항목 이름을 바꾸지 마세요.');
  조각.push('- 확인 못 한 항목은 그 줄을 아예 빼세요. 빈칸이나 "확인 필요"로 채우지 마세요.');
  조각.push('- 알아낸 값이 더 있으면 아래 목록 뒤에 줄을 추가해도 됩니다.');
  조각.push('');

  var 항목들 = (typeof finwiz카드틀 !== 'undefined') ? (finwiz카드틀[클러스터] || []) : [];
  if (항목들.length) {
    조각.push('[채울 항목 ' + 항목들.length + '개]');
    항목들.forEach(function (한개) { 조각.push('- ' + 한개[0]); });
    조각.push('');
  }

  조각.push('[출력 형식]');
  조각.push('표 하나만 답해 주세요. 인사말이나 설명은 붙이지 마세요.');
  조각.push('');
  조각.push('| 항목 | 값 | 기준일 | 출처 |');
  조각.push('|---|---|---|---|');
  if (항목들.length) {
    조각.push('| ' + 항목들[0][0] + ' | (확인한 값) | ' + 오늘 + ' | (그 값이 적힌 주소) |');
  } else {
    조각.push('| 햇살론 일반 대출한도 | 최대 2,000만원 | ' + 오늘 + ' | https://www.kinfa.or.kr/... |');
  }

  return { 요청문: 조각.join('\n'), 클러스터: 클러스터, 글수: 이주제.length,
           출처: 출처, GPT주소: (typeof 마이GPT주소 !== 'undefined') ? 마이GPT주소 : '' };
}

/** 받은 표를 사실카드 탭에 넣는다. 그 주제의 기존 줄은 갈아끼운다. */
function finwiz사실카드저장(클러스터, 답변) {
  var 줄들 = [];
  String(답변 || '').split('\n').forEach(function (줄) {
    var t = 줄.trim();
    if (t.indexOf('|') === -1) return;
    if (typeof 표구분줄인가 === 'function' && 표구분줄인가(t)) return;
    var 칸 = (typeof 표칸나누기 === 'function')
      ? 표칸나누기(t)
      : t.replace(/^\||\|$/g, '').split('|').map(function (v) { return v.trim(); });
    if (칸.length < 2) return;
    if (/^항목$/.test(칸[0])) return;                       // 머리줄
    if (!칸[0] || !칸[1]) return;
    if (/^https?:/.test(칸[0])) return;
    줄들.push([클러스터, 칸[0], 칸[1],
               finwiz날짜글자(칸[2] || ''), finwiz주소만(칸[3])]);
  });

  if (!줄들.length) {
    return { 성공: false, 문제들: [
      '표를 찾지 못했습니다.',
      '`| 항목 | 값 | 기준일 | 출처 |` 모양의 표를 그대로 복사해서 붙여넣어 주세요.'
    ] };
  }

  // 받은 값을 항목 이름으로 짝지어 둔다 (띄어쓰기·기호 차이는 무시)
  var 받은것 = {};
  줄들.forEach(function (한줄) { 받은것[finwiz소제목열쇠(한줄[1])] = 한줄; });

  var 탭 = finwiz탭(finwiz사실탭, finwiz사실헤더);
  var 지금 = (탭.getLastRow() >= 2)
    ? 탭.getRange(2, 1, 탭.getLastRow() - 1, finwiz사실헤더.length).getValues() : [];

  // ① 이 주제의 기존 줄은 자리를 지키면서 값만 갈아끼운다
  var 새것 = [], 채운수 = 0, 쓴것 = {};
  지금.forEach(function (한줄) {
    var 클 = String(한줄[0] || '').trim();
    var 항 = String(한줄[1] || '').trim();
    if (!클 || !항) return;
    if (클 !== 클러스터) { 새것.push(한줄.slice(0, finwiz사실헤더.length)); return; }
    var 열쇠 = finwiz소제목열쇠(항);
    var 받음 = 받은것[열쇠];
    if (받음) {
      쓴것[열쇠] = true; 채운수++;
      새것.push([클러스터, 항, 받음[2], 받음[3],
                 받음[4] || String(한줄[4] || '').trim()]);
    } else {
      // 이번에 못 찾은 항목은 지우지 말고 빈 채로 둔다 (다음에 다시 시도)
      새것.push([클러스터, 항, String(한줄[2] || '').trim(),
                 finwiz날짜글자(한줄[3]), String(한줄[4] || '').trim()]);
    }
  });

  // ② 틀에 없던 항목은 이 주제 줄 뒤에 붙인다
  var 덧붙임 = [];
  줄들.forEach(function (한줄) {
    var 열쇠 = finwiz소제목열쇠(한줄[1]);
    if (쓴것[열쇠]) return;
    쓴것[열쇠] = true; 채운수++;
    덧붙임.push(한줄);
  });
  if (덧붙임.length) {
    var 마지막 = -1;
    새것.forEach(function (한줄, i) { if (한줄[0] === 클러스터) 마지막 = i; });
    새것 = 새것.slice(0, 마지막 + 1).concat(덧붙임, 새것.slice(마지막 + 1));
  }

  if (탭.getLastRow() >= 2) {
    탭.getRange(2, 1, 탭.getLastRow() - 1, 탭.getLastColumn()).clearContent();
  }
  if (새것.length) {
    탭.getRange(2, 1, 새것.length, finwiz사실헤더.length).setValues(새것);
  }
  SpreadsheetApp.flush();

  var 이주제 = 새것.filter(function (한줄) { return 한줄[0] === 클러스터; });
  var 아직 = 이주제.filter(function (한줄) { return !String(한줄[2] || '').trim(); });
  return { 성공: true, 개수: 채운수, 클러스터: 클러스터,
           전체: 이주제.length, 아직: 아직.length,
           아직항목: 아직.slice(0, 6).map(function (한줄) { return 한줄[1]; }) };
}

/** 사실카드가 없거나 오래된 주제 목록. */
function finwiz카드점검() {
  var 순서목록 = finwiz순서읽기();
  var 사실맵 = finwiz사실읽기();
  var 주제 = {}, 차례 = [];
  순서목록.forEach(function (한줄) {
    if (주제[한줄.클러스터]) { 주제[한줄.클러스터].전체++; return; }
    주제[한줄.클러스터] = { 클러스터: 한줄.클러스터, 전체: 1, 남음: 0 };
    차례.push(한줄.클러스터);
  });
  순서목록.forEach(function (한줄) {
    if (한줄.작성상태 !== '발행완료' && 한줄.작성상태 !== '작성완료') 주제[한줄.클러스터].남음++;
  });

  return 차례.map(function (이름) {
    var 상태 = finwiz카드상태(이름, 사실맵);
    return {
      클러스터: 이름, 전체: 주제[이름].전체, 남음: 주제[이름].남음,
      있음: 상태.있음, 개수: 상태.개수, 오래됨: 상태.오래됨,
      오래된줄: 상태.오래된줄 || 0, 지난날: 상태.지난날
    };
  });
}

function finwiz카드점검메뉴() {
  var ui = SpreadsheetApp.getUi();
  try {
    var 목록 = finwiz카드점검();
    var 없음 = 목록.filter(function (하나) { return !하나.있음 && 하나.남음 > 0; });
    var 오래 = 목록.filter(function (하나) { return 하나.있음 && 하나.오래됨; });
    var 글 = [];
    if (없음.length) {
      글.push('사실카드가 아직 없는 주제 (' + 없음.length + '개)');
      없음.forEach(function (하나) {
        글.push('  · ' + 하나.클러스터 + ' — 남은 글 ' + 하나.남음 + '편');
      });
      글.push('');
    }
    if (오래.length) {
      글.push('확인한 지 ' + finwiz사실유효일 + '일이 지난 주제 (' + 오래.length + '개)');
      오래.forEach(function (하나) {
        글.push('  · ' + 하나.클러스터 + ' — ' + 하나.지난날 + '일 전');
      });
      글.push('');
    }
    if (!글.length) 글.push('모든 주제의 사실카드가 최신입니다.');
    else 글.push('"🧭 finwiz 순서대로 글쓰기" 에서 해당 주제 글을 열면 카드를 만들 수 있습니다.');
    ui.alert(글.join('\n'));
  } catch (오류) {
    ui.alert('보지 못했습니다.\n\n' + 오류.message);
  }
}


// ══════════════════════════════════════════════════════════
//  본문 검사 — 발행하기 전에 시트 안에서 거른다 (돈이 들지 않는다)
//
//  finwiz 는 돈 이야기다. 틀린 숫자는 독자에게도 손해고,
//  단정·유도 표현은 애드센스 금융 정책에 걸린다.
//  그래서 저장하기 전에 자동으로 본다.
// ══════════════════════════════════════════════════════════

/** 값에 붙은 단위. 비교할 수 없는 값이면 빈 글자. */
function finwiz단위(값) {
  var v = String(값 || '');
  if (/%|퍼센트/.test(v)) return '%';
  if (/억\s*원|억원/.test(v)) return '억원';
  if (/만\s*원|만원/.test(v)) return '만원';
  return '';
}

/** 그 단위가 붙은 숫자만 뽑는다. 1,200만원 → '1200' */
function finwiz숫자뽑기(글자, 단위) {
  var 꼬리 = (단위 === '%') ? '\\s*(?:%|퍼센트)'
           : (단위 === '억원') ? '\\s*억\\s*원'
           : '\\s*만\\s*원';
  var 찾기 = new RegExp('([0-9][0-9.]*)(?:\\s*[~-]\\s*([0-9][0-9.]*))?' + 꼬리, 'g');
  var m, 나온것 = [], 글 = String(글자 || '').replace(/,/g, '');
  while ((m = 찾기.exec(글)) !== null) {
    if (나온것.indexOf(m[1]) === -1) 나온것.push(m[1]);
    if (m[2] && 나온것.indexOf(m[2]) === -1) 나온것.push(m[2]);
  }
  return 나온것;
}

/** 항목 이름에서 '한도·금리·조건' 같은 흔한 말을 떼고 알맹이만 남긴다. */
function finwiz항목열쇠(항목) {
  var 흔한말 = ['최대', '한도', '금리', '조건', '요건', '대상', '기준', '이하', '이상',
                '소득', '연령', '나이', '신청', '기간', '금액', '범위', '수준'];
  var 낱말 = finwiz낱말(항목).filter(function (말) { return 흔한말.indexOf(말) === -1; });
  if (!낱말.length) return '';
  return 낱말.slice(0, 2).join('');
}

/** 태그를 걷어낸 본문 글자. */
function finwiz글자만(HTML) {
  return String(HTML || '')
    .replace(/<a\b[^>]*>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ');
}

/** 소제목(H2·H3)을 뽑아 비교하기 좋게 만든다. */
function finwiz소제목뽑기(HTML) {
  var 찾기 = /<h[23][^>]*>([\s\S]*?)<\/h[23]>/gi, m, 나온것 = [];
  while ((m = 찾기.exec(String(HTML || ''))) !== null) {
    var 이름 = m[1].replace(/<[^>]+>/g, '').trim();
    if (!이름) continue;
    if (/최종\s*정리하면|함께\s*보면\s*좋은\s*글/.test(이름)) continue;
    나온것.push(이름);
  }
  return 나온것;
}

function finwiz소제목열쇠(글자) {
  return String(글자 || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
}

/** 이미 쓴 글과 소제목이 얼마나 겹치나. 0~1. */
function finwiz겹침(새소제목, 옛소제목) {
  if (!새소제목.length || !옛소제목.length) return 0;
  var 옛 = {}, 맞은수 = 0;
  옛소제목.forEach(function (이름) { 옛[finwiz소제목열쇠(이름)] = true; });
  새소제목.forEach(function (이름) { if (옛[finwiz소제목열쇠(이름)]) 맞은수++; });
  return 맞은수 / 새소제목.length;
}

/**
 * 저장 전에 본문을 본다.
 *   막을것 — 있으면 저장하지 않는다 (애드센스·YMYL 위험)
 *   경고   — 저장은 하되 화면에 알린다 (사람이 10초만 보면 되는 것들)
 */
function finwiz본문검사(HTML, 대상, 사실줄들, 순서목록) {
  var 막을것 = [], 경고 = [];
  var 글자 = finwiz글자만(HTML);

  // ① 애드센스·YMYL 위험 표현
  finwiz금지표현.forEach(function (하나) {
    var 찾기 = new RegExp(하나.패턴.source, 'g');
    var m;
    while ((m = 찾기.exec(글자)) !== null) {
      if (finwiz경고문인가(글자, m.index, m[0].length)) continue;   // 경고하는 문장은 통과
      if (하나.비교허용) {
        // 앞뒤 40자 안에 2026 이 있으면 지난 값과 견주는 문장이다
        var 둘레 = 글자.substr(Math.max(0, m.index - 40), m[0].length + 80);
        if (둘레.indexOf('2026') !== -1) continue;
      }
      막을것.push(하나.이름 + ' — 본문의 "' + m[0].trim() + '" 부분');
      break;
    }
  });

  // ② 사실카드와 어긋나는 숫자
  //
  //  글이 카드의 모든 항목을 다뤄야 하는 것은 아니다. 그래서 '안 나왔다'로는
  //  알리지 않는다. 그 항목 이름의 낱말이 **전부** 한자리에 모여 있고,
  //  그 자리에 카드와 **다른 숫자**가 적혀 있을 때만 알린다.
  //  헛경보가 한 번 나면 그다음부터 아무도 안 읽기 때문에 일부러 깐깐하게 본다.
  var 압축본문 = 글자.replace(/[,\s]/g, '');
  (사실줄들 || []).forEach(function (하나) {
    var 단위 = finwiz단위(하나.값);
    if (!단위) return;
    var 카드숫자 = finwiz숫자뽑기(하나.값, 단위);
    if (!카드숫자.length) return;

    var 낱말들 = finwiz낱말(하나.항목).filter(function (말) {
      return ['최대', '이하', '이상', '기준', '범위', '수준'].indexOf(말) === -1;
    });
    if (낱말들.length < 2) return;

    var 첫자리 = 압축본문.indexOf(낱말들[0]);
    while (첫자리 !== -1) {
      var 창 = 압축본문.substring(Math.max(0, 첫자리 - 30),
                                 첫자리 + 낱말들[0].length + 60);
      var 다있나 = 낱말들.every(function (말) { return 창.indexOf(말) !== -1; });
      if (다있나) {
        var 본문값 = finwiz숫자뽑기(창, 단위);
        // 카드 숫자가 본문 어디에든 나오면 제대로 쓴 것으로 본다.
        //   예: '불법사금융예방대출은 연소득 3,500만원 이하가 대상이고 한도는 100만원'
        //   → '한도' 근처에 3500 이 먼저 걸리지만 100 이 본문에 있으므로 문제 없다.
        //   이걸 안 보면 소득요건 숫자를 한도 오류로 잘못 잡는다(실제로 그랬다).
        var 어디든있나 = 카드숫자.some(function (v) {
          return finwiz숫자뽑기(압축본문, 단위).indexOf(v) !== -1;
        });
        var 맞나 = !본문값.length || 어디든있나 ||
          본문값.some(function (v) { return 카드숫자.indexOf(v) !== -1; });
        if (!맞나) {
          경고.push('사실카드와 숫자가 다릅니다 — ' + 하나.항목 + ' 은(는) 카드에 「' +
                    하나.값 + '」 인데 본문에는 ' + 본문값.slice(0, 3).join('·') + 단위 +
                    ' 로 적혀 있습니다. 어느 쪽이 맞는지 확인해 주세요.');
        }
        return;
      }
      첫자리 = 압축본문.indexOf(낱말들[0], 첫자리 + 1);
    }
  });

  // ③ 근거 없는 숫자: 금액·금리가 있는데 출처 링크가 없다
  var 돈있나 = /(연\s*)?\d[\d,.]*\s*(%|퍼센트|만\s*원|만원|억\s*원|억원)/.test(글자);
  var 링크있나 = /<a\b[^>]*href=/i.test(String(HTML || ''));
  if (돈있나 && !링크있나) {
    경고.push('금액이나 금리가 있는데 본문에 링크가 하나도 없습니다. 공식 출처 링크를 넣어 주세요.');
  }

  // ④ 이미 쓴 글과 소제목이 너무 겹친다 (167편 자기잠식 방지)
  var 새소제목 = finwiz소제목뽑기(HTML);
  var 제일겹친것 = null, 제일값 = 0;
  (순서목록 || []).forEach(function (한줄) {
    if (한줄.ID === 대상.ID || !한줄.소제목) return;
    var 값 = finwiz겹침(새소제목, 한줄.소제목.split('|'));
    if (값 > 제일값) { 제일값 = 값; 제일겹친것 = 한줄; }
  });
  if (제일겹친것 && 제일값 >= 0.6) {
    경고.push('소제목이 ' + 제일겹친것.순번 + '번 글과 ' + Math.round(제일값 * 100) +
              '% 겹칩니다: "' + 제일겹친것.제목.substring(0, 30) + '..." — ' +
              '같은 주제 글끼리 검색순위를 서로 깎아먹을 수 있습니다.');
  }

  // ⑤ 소제목이 너무 적다
  if (새소제목.length < 3) {
    경고.push('소제목이 ' + 새소제목.length + '개뿐입니다. 3개 이상으로 나눠 주세요.');
  }

  // ⑥ 목록도 표도 없이 문단만 이어진다
  //    대출 글은 조건·금액·순서가 많아서 줄글로만 쓰면 독자가 자기 경우를 못 찾는다.
  var 본문HTML = String(HTML || '');
  var 목록수 = (본문HTML.match(/<ul[\s>]/gi) || []).length +
               (본문HTML.match(/<ol[\s>]/gi) || []).length;
  var 표수 = (본문HTML.match(/<table[\s>]/gi) || []).length;
  if (목록수 === 0 && 표수 === 0) {
    경고.push('목록도 표도 없이 문단만 이어집니다. 조건·금액·확인 순서는 ' +
              '목록이나 표로 바꿔 주세요. (요청문에 이미 넣어 두었으니 다시 받아 보셔도 됩니다)');
  } else if (목록수 < 2 && 표수 === 0) {
    경고.push('목록이 ' + 목록수 + '개뿐이고 표가 없습니다. 견주는 내용이 있으면 표로 바꿔 주세요.');
  }

  // ⑦ 너무 긴 문단
  var 긴문단 = 0;
  var 문단찾기 = /<p[^>]*>([\s\S]*?)<\/p>/gi, 문단m;
  while ((문단m = 문단찾기.exec(본문HTML)) !== null) {
    var 문단글 = 문단m[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    if (문단글.length >= 260) 긴문단++;
  }
  if (긴문단 >= 3) {
    경고.push('한 덩어리가 너무 긴 문단이 ' + 긴문단 + '개 있습니다. 읽기 쉽게 나눠 주세요.');
  }

  return { 막을것: 막을것, 경고: 경고, 소제목: 새소제목 };
}


// ══════════════════════════════════════════════════════════
//  화면이 부르는 것들
// ══════════════════════════════════════════════════════════

/** 다음에 쓸 글 목록. 아직 안 쓴 것 위에서부터. */
// 하루에 몇 편까지 한꺼번에 발행하는가.
// 이 수만큼은 서로를 링크로 걸 수 없다. 아직 주소가 없기 때문이다.
var finwiz하루편수 = 7;


/**
 * 오늘 같이 써도 되는 글을 고른다.
 *
 * 글의 주소는 발행이 끝나야 생긴다. 그래서 오늘 같이 쓰는 글끼리는
 * 서로 링크를 걸 수 없다. 로드맵이 "3번 글에는 1번 글을 걸어라" 라고
 * 찍어줬는데 1번과 3번을 같은 날 쓰면, 그 링크는 그냥 사라지고
 * 엉뚱한 글이 대신 걸린다.
 *
 * 그래서 앞 글을 참조하는 글은 다음 날로 미룬다. 하루 이틀 늦어지지만
 * 로드맵이 짜 놓은 연결이 살아난다.
 */
function finwiz오늘묶음(남은것, 링크맵, 편수) {
  편수 = 편수 || finwiz하루편수;
  var 오늘 = [], 오늘ID = {}, 미룸 = {};

  for (var i = 0; i < 남은것.length && 오늘.length < 편수; i++) {
    var 한줄 = 남은것[i];
    var 기다릴것 = [];

    (한줄.관련 || []).forEach(function (id) {
      if (!오늘ID[id]) return;
      var 글 = 링크맵[id];
      if (글 && 글.URL) return;        // 이미 주소가 있으면 걸 수 있다
      기다릴것.push({ ID: id, 제목: (글 && 글.제목) || id });
    });

    if (기다릴것.length) {
      미룸[한줄.순번] = 기다릴것;
      continue;
    }
    오늘.push(한줄.순번);
    오늘ID[한줄.ID] = true;
  }
  return { 오늘: 오늘, 미룸: 미룸 };
}


function finwiz다음목록() {
  var 순서목록 = finwiz순서읽기();
  if (!순서목록.length) {
    return { 준비안됨: true,
             안내: '먼저 메뉴에서 "📥 finwiz 로드맵 가져오기" 를 한 번 눌러 주세요.' };
  }
  var 링크맵 = finwiz링크읽기();

  var 남은것 = 순서목록.filter(function (한줄) {
    return 한줄.작성상태 !== '발행완료' && 한줄.작성상태 !== '작성완료';
  });
  남은것.sort(function (가, 나) { return 가.순번 - 나.순번; });

  var 묶음 = finwiz오늘묶음(남은것, 링크맵, finwiz하루편수);
  var 오늘인가 = {};
  묶음.오늘.forEach(function (순번) { 오늘인가[순번] = true; });

  // 오늘 쓸 글은 목록 어디에 있든 위로 끌어올린다.
  // 로드맵은 큰 글(1번) 아래에 딸린 글(2~13번)이 붙는 모양이라,
  // 오늘 쓸 글이 33번, 68번처럼 한참 아래에 있는 경우가 흔하다.
  var 오늘것 = [], 나중것 = [];
  남은것.forEach(function (한줄) {
    (오늘인가[한줄.순번] ? 오늘것 : 나중것).push(한줄);
  });
  var 줄세움 = 오늘것.concat(나중것).slice(0, Math.max(12, 오늘것.length));

  var 사실맵 = finwiz사실읽기();
  var 보여줄것 = 줄세움.map(function (한줄) {
    var 링크 = finwiz링크고르기(한줄, 링크맵, 순서목록);
    var 카드 = finwiz카드상태(한줄.클러스터, 사실맵);
    return {
      순번: 한줄.순번, 단계: 한줄.단계, 클러스터: 한줄.클러스터,
      제목: 한줄.제목, 메인키워드: 한줄.메인키워드,
      작성전략: 한줄.작성전략, 확인URL: 한줄.확인URL,
      작성상태: 한줄.작성상태 || '미작성',
      링크수: 링크.length,
      링크: 링크,
      오늘쓰기: !!오늘인가[한줄.순번],
      기다릴글: 묶음.미룸[한줄.순번] || [],
      카드있음: 카드.있음, 카드오래됨: 카드.오래됨, 카드개수: 카드.개수,
      카드빈줄: 카드.빈줄 || 0, 카드오래된줄: 카드.오래된줄 || 0
    };
  });

  var 주소있음 = 0;
  for (var id in 링크맵) if (링크맵[id].URL) 주소있음++;

  return {
    전체: 순서목록.length,
    남음: 남은것.length,
    끝남: 순서목록.length - 남은것.length,
    주소있음: 주소있음,
    하루편수: finwiz하루편수,
    오늘묶음: 묶음.오늘,
    목록: 보여줄것,
    GPT주소: (typeof 마이GPT주소 !== 'undefined') ? 마이GPT주소 : ''
  };
}

/** 순번으로 한 줄을 찾는다. 없으면 알기 쉬운 오류를 낸다. */
function finwiz찾기(순번) {
  var 순서목록 = finwiz순서읽기();
  for (var i = 0; i < 순서목록.length; i++) {
    if (순서목록[i].순번 === Number(순번)) {
      return { 대상: 순서목록[i], 목록: 순서목록 };
    }
  }
  throw new Error(순번 + '번 글을 ' + finwiz순서탭 + ' 탭에서 찾지 못했습니다.\n' +
                  '"← 목록으로" 를 눌러 다시 불러와 주세요.');
}

/**
 * 요청문 만들기.
 *
 * 제목은 로드맵 제목 그대로 쓰라고 못 박는다. 그래야 발행 확인이
 * 제목으로 주소를 찾아내고, 그 주소가 다음 글의 링크가 된다.
 */
function finwiz요청문(순번) {
  var 찾음 = finwiz찾기(순번);
  var 대상 = 찾음.대상;
  var 링크 = finwiz링크고르기(대상, finwiz링크읽기(), 찾음.목록);

  var 출처 = finwiz출처찾기(대상);
  var 카드 = finwiz카드상태(대상.클러스터);

  var 규칙 = (typeof 공통규칙 !== 'undefined') ? 공통규칙.slice() : [];
  규칙.push('- 40대 이상 독자를 생각해 친절하고 쉬운 존댓말로 써 주세요.');
  규칙.push('- 제목은 아래 [고정 제목] 을 글자 하나 바꾸지 말고 그대로 쓰세요. 새로 짓지 마세요.');
  규칙.push('- 대출 조건·한도·금리는 단정하지 말고, 확인해야 할 기준과 순서를 알려 주세요.');
  규칙.push('- 특정 업체를 추천하거나 유도하지 마세요. 업체 이름을 나열하지 마세요.');
  규칙.push('- `무조건 승인`, `100% 가능`, `누구나 가능`, `신용 상관없이` 는 절대 쓰지 마세요. 사실이 아니고 광고 정책에도 걸립니다.');
  규칙.push('- 전화번호나 `지금 신청하세요` 같은 유도 문구를 넣지 마세요.');
  규칙.push('- 숫자(한도·금리·소득요건)는 위 공식 페이지에서 확인한 것만 쓰세요. 확인 못 한 값은 숫자 대신 `공식 페이지에서 확인` 이라고 쓰세요.');
  규칙.push('- 연도를 적을 때는 2026년 기준으로 쓰세요. 지난 연도를 기준으로 적지 마세요.');
  규칙.push('- 숫자를 쓴 문단에는 그 값이 적힌 공식 페이지 링크를 함께 넣어 주세요.');

  var 정보줄 = [
    '[글쓰기 정보]',
    '이번 글 순번: ' + 대상.순번 + '번 (' + 대상.단계 + ')',
    '주제 묶음: ' + 대상.클러스터,
    '메인 키워드: ' + 대상.메인키워드,
    '올릴 곳: 티스토리(finwiz.tistory.com)'
  ];
  if (대상.검색량) 정보줄.push('월 검색량(참고): ' + 대상.검색량);
  if (대상.작성전략) 정보줄.push('이 글의 차별점: ' + 대상.작성전략);

  var 조각 = [
    정보줄.join('\n'),
    '',
    '[고정 제목]',
    대상.제목,
    ''
  ];

  // ── 반드시 열어서 확인할 공식 페이지 ──
  if (출처.length) {
    조각.push('[먼저 열어서 확인할 공식 페이지]');
    조각.push('아래 페이지를 실제로 열어 지금 값이 맞는지 확인하고 쓰세요.');
    출처.forEach(function (하나) {
      조각.push('- ' + 하나.기관 + ' · ' + 하나.주제 + ' : ' + 하나.URL +
                (하나.메모 ? '  (' + 하나.메모 + ')' : ''));
    });
    조각.push('');
  }

  // ── 이미 확인해 둔 숫자 ──
  if (카드.있음) {
    조각.push('[확인해 둔 숫자 — 이 값을 쓰세요]');
    if (카드.오래됨) {
      조각.push('⚠ 아래 값은 확인한 지 오래됐습니다(최대 ' + 카드.지난날 + '일 전). ' +
                '위 공식 페이지에서 바뀐 게 없는지 먼저 확인하고, 바뀌었으면 새 값을 쓰고 글 맨 끝에 ' +
                '`[바뀐 값] 항목 = 새 값` 으로 알려 주세요.');
    } else if (카드.오래된줄) {
      조각.push('아래는 공식 페이지에서 확인해 둔 값입니다. 이 값과 다르게 쓰지 마세요.');
      조각.push('※ 표시가 붙은 줄만 오래된 값입니다. 그 줄은 원문에서 다시 확인해 주세요.');
    } else {
      조각.push('아래는 공식 페이지에서 확인해 둔 값입니다. 이 값과 다르게 쓰지 마세요.');
    }
    카드.줄들.forEach(function (하나) {
      var 꼬리 = 하나.기준일 ? '  (' + 하나.기준일 + ' 기준' : '';
      if (꼬리 && !카드.오래됨 && 하나.지난날 > finwiz사실유효일) {
        꼬리 += ', ' + 하나.지난날 + '일 전 ※';
      }
      if (꼬리) 꼬리 += ')';
      조각.push('- ' + 하나.항목 + ' : ' + 하나.값 + 꼬리);
    });
    조각.push('');
    조각.push('여기에 없는 숫자는 위 공식 페이지에서 직접 확인한 것만 쓰세요.');
    조각.push('');
  }

  if (링크.length) {
    조각.push('[본문에 반드시 넣을 내부 링크]');
    조각.push('아래 링크를 본문 흐름에 맞는 자리에 자연스럽게 넣어 주세요.');
    조각.push('마크다운 링크 형식 그대로 쓰고, 주소는 한 글자도 바꾸지 마세요.');
    링크.forEach(function (한개) {
      조각.push('- [' + 한개.제목 + '](' + 한개.URL + ')');
    });
    조각.push('');
  } else {
    조각.push('[내부 링크]');
    조각.push('이번 글은 아직 연결할 기존 글이 없습니다. 내부 링크는 넣지 마세요.');
    조각.push('');
  }

  // ── 구조 지시 ────────────────────────────────────────
  // 이게 없으면 문단만 죽 이어지는 글이 나온다. 대출 글은 조건·금액·순서가
  // 많아서 줄글로 늘어놓으면 독자가 자기 경우를 못 찾는다.
  조각.push('[글의 짜임 — 이대로 만들어 주세요]');
  조각.push('- 소제목(##)은 4~7개. 각 소제목 아래는 2~4문단으로 하세요.');
  조각.push('- 소제목마다 **첫 문장이 결론**이어야 합니다. 배경 설명으로 시작하지 마세요.');
  조각.push('    나쁜 예: `100만원 안팎을 찾는 분들은 대체로 갑작스러운 생활비 부족인 경우가 많습니다.`');
  조각.push('    좋은 예: `100만원이 필요하다면 대출보다 정책서민금융을 먼저 확인하는 편이 낫습니다.`');
  조각.push('- 소제목은 ## 만 쓰세요. ### 로 한 단계 더 나누지 마세요.');
  조각.push('- 각 소제목마다 그 대목의 핵심 문장 하나를 **굵게** 표시하세요. 문단 전체를 굵게 하지는 마세요.');
  조각.push('- 아래 세 가지는 줄글로 풀지 말고 반드시 목록(-)이나 표로 만드세요.');
  조각.push('    · 조건이나 자격요건이 두 개 이상일 때');
  조각.push('    · 금액·금리·기간·대상을 견줄 때');
  조각.push('    · 확인 순서나 준비 서류를 알려줄 때');
  조각.push('- 표는 3~5줄, 열은 3개 이내로 하세요. 첫 열은 독자가 자기 경우를 찾는 기준으로 쓰세요.');
  조각.push('  (좋은 예: `필요한 금액 | 자주 있는 상황 | 먼저 확인할 것`)');
  조각.push('- 한 문단은 3줄(120자 안팎)을 넘기지 마세요. 길면 나누세요.');
  조각.push('- 글 맨 앞 도입부는 3줄 이내로 짧게 쓰고, 바로 첫 소제목으로 넘어가세요.');
  조각.push('- 글 전체에 **목록 2개 이상, 표 1개 이상**이 들어가야 합니다.');
  조각.push('- 소제목은 그 대목의 내용이 드러나게 지으세요.');
  조각.push('  제목에서 `N가지`처럼 개수를 약속했으면 번호를 써도 되지만, 번호 뒤에 무엇인지 분명히 적으세요.');
  조각.push('- 같은 말을 다르게 반복해 분량을 늘리지 마세요. 늘려야 하면 사례나 경우를 더하세요.');
  조각.push('');

  조각.push('[요청]');
  조각.push(출처.length
    ? '위 공식 페이지를 먼저 열어 확인해 주세요. 기억으로 쓰지 마세요.'
    : '메인 키워드를 먼저 웹 검색으로 확인해 주세요.');
  조각.push('그다음 티스토리(finwiz.tistory.com)에 올릴 블로그 글 **한 편**을 끝까지 완성해 주세요.');
  조각.push('개요나 목차만 주지 말고, 바로 발행할 수 있는 완성된 본문을 써 주세요.');
  조각.push('');
  조각.push('[지켜야 할 규칙]');
  조각.push(규칙.join('\n'));
  조각.push('');
  조각.push('[출력 형식]');
  조각.push('아래 순서로만 답해 주세요. 인사말이나 설명은 붙이지 마세요.');
  조각.push('');
  조각.push('# ' + 대상.제목);
  조각.push('');
  조각.push((typeof 출력형식 !== 'undefined')
    ? 출력형식.split('\n').slice(1).join('\n').replace(/^\n/, '')
    : '(본문. 소제목은 ## 로 씁니다.)\n\n## 최종 정리하면\n(핵심 정리)\n\n## 메타 디스크립션\n(한 문장)\n\n## 관련 태그\n(쉼표로 8~10개)');

  return { 요청문: 조각.join('\n'), 제목: 대상.제목, 링크: 링크,
           출처: 출처, 카드: { 있음: 카드.있음, 개수: 카드.개수, 빈줄: 카드.빈줄 || 0,
                             오래됨: 카드.오래됨, 오래된줄: 카드.오래된줄 || 0,
                             지난날: 카드.지난날 },
           클러스터: 대상.클러스터,
           메인키워드: 대상.메인키워드,
           GPT주소: (typeof 마이GPT주소 !== 'undefined') ? 마이GPT주소 : '' };
}


// ══════════════════════════════════════════════════════════
//  저장 — 오늘작성 탭에 새 행으로 넣는다
// ══════════════════════════════════════════════════════════

/**
 * 넣어야 할 링크가 본문에 다 들어갔는지 보고, 빠진 것은 직접 붙인다.
 *
 * 봇이 링크를 빼먹거나 주소를 고쳐 쓰는 일이 있다. 그때도 링크가
 * 반드시 들어가도록 '함께 보면 좋은 글' 목록으로 끝부분에 넣어 준다.
 */
function finwiz링크채우기(HTML, 링크) {
  var 본문 = String(HTML || '');
  if (!링크 || !링크.length) return { HTML: 본문, 채운것: [] };

  var 빠진것 = 링크.filter(function (한개) {
    return 본문.indexOf(한개.URL) === -1;
  });
  if (!빠진것.length) return { HTML: 본문, 채운것: [] };

  var 줄들 = 빠진것.map(function (한개) {
    return '<li><a href="' + 한개.URL + '" target="_blank" rel="noopener">' +
           한개.제목 + '</a></li>';
  });

  // 이미 '함께 보면 좋은 글' 목록이 있으면 거기에 끼워 넣는다.
  // 두 번 돌려도 목록이 두 개로 늘어나지 않게 하기 위해서다.
  var 기존 = 본문.match(/<h2[^>]*>\s*함께\s*보면\s*좋은\s*글\s*<\/h2>\s*<ul>/i);
  if (기존) {
    var 끼울자리 = 기존.index + 기존[0].length;
    본문 = 본문.substring(0, 끼울자리) + '\n' + 줄들.join('\n') + 본문.substring(끼울자리);
    return { HTML: 본문, 채운것: 빠진것 };
  }

  var 넣을것 = ['<h2>함께 보면 좋은 글</h2>', '<ul>']
                 .concat(줄들).concat(['</ul>']).join('\n');

  // '최종 정리하면' 은 발행 도구가 자리표로 쓰므로 그 앞에 넣는다
  var 자리 = 본문.search(/<h2[^>]*>\s*최종\s*정리하면/);
  if (자리 !== -1) {
    본문 = 본문.substring(0, 자리) + 넣을것 + '\n' + 본문.substring(자리);
  } else {
    본문 = 본문 + '\n' + 넣을것;
  }
  return { HTML: 본문, 채운것: 빠진것 };
}

/**
 * 붙여넣은 글을 저장한다.
 *
 * 오늘작성 탭에 새 행을 만들고, 기존 저장 기능을 그대로 쓴다.
 * 저장이 끝나면 제목을 로드맵 제목으로 되돌린다(봇이 바꿔 썼을 수 있다).
 */
function finwiz저장(순번, 답변) {
  var 찾음 = finwiz찾기(순번);
  var 대상 = 찾음.대상;
  var 링크 = finwiz링크고르기(대상, finwiz링크읽기(), 찾음.목록);

  var 준비 = 시트준비();
  var 탭 = 준비.탭, 열 = 준비.열;

  // 같은 글이 이미 오늘작성에 있으면 그 행을 다시 쓴다 (중복 방지)
  var 행번호 = 0;
  var 마지막행 = 탭.getLastRow();
  if (마지막행 >= 2 && 열.title !== -1) {
    var 제목들 = 탭.getRange(2, 열.title + 1, 마지막행 - 1, 1).getValues();
    for (var i = 0; i < 제목들.length; i++) {
      if (제목같나(제목들[i][0], 대상.제목)) { 행번호 = i + 2; break; }
    }
  }
  if (!행번호) {
    행번호 = 탭.getLastRow() + 1;
    탭.getRange(행번호, 열.keyword + 1).setValue(대상.메인키워드);
    탭.getRange(행번호, 열.category + 1).setValue(finwiz카테고리);
    if (열.source !== -1 && 대상.확인URL) {
      탭.getRange(행번호, 열.source + 1).setValue(대상.확인URL);
    }
    if (열['ts롱테일'] !== -1 && 열['ts롱테일'] !== undefined) {
      탭.getRange(행번호, 열['ts롱테일'] + 1).setValue(대상.메인키워드);
    }
    SpreadsheetApp.flush();
  }

  var 결과 = 저장하기(행번호, 답변, 'tistory', 대상.메인키워드);
  if (!결과.성공) return 결과;

  // 제목은 로드맵 제목으로 고정한다 (발행 확인이 이 제목으로 주소를 찾는다)
  탭.getRange(행번호, 열.title + 1).setValue(대상.제목);
  if (열.focus !== -1 && 열.focus !== undefined) {
    탭.getRange(행번호, 열.focus + 1).setValue(대상.메인키워드);
  }

  // 링크가 빠졌으면 직접 채운다
  var 채움 = { 채운것: [] };
  var 본문 = '';
  if (열.html !== -1 && 열.html !== undefined) {
    본문 = String(탭.getRange(행번호, 열.html + 1).getValue() || '');
    채움 = finwiz링크채우기(본문, 링크);
    if (채움.채운것.length) {
      본문 = 채움.HTML;
      탭.getRange(행번호, 열.html + 1).setValue(본문);
    }
  }

  // 발행 전 검사 — 위험한 표현이 있으면 여기서 막는다
  var 사실맵 = finwiz사실읽기();
  var 검사 = finwiz본문검사(본문, 대상, 사실맵[대상.클러스터] || [], 찾음.목록);
  if (검사.막을것.length) {
    // 상태를 되돌려 발행 도구가 집어가지 않게 한다
    탭.getRange(행번호, 열.status + 1).setValue('검사실패');
    SpreadsheetApp.flush();
    return {
      성공: false,
      문제들: 검사.막을것.concat([
        '(이 표현들은 사실과 다를 수 있고 애드센스 금융 정책에도 걸립니다. ' +
        '해당 문장을 고쳐서 다시 붙여넣어 주세요. 시트 상태는 검사실패로 두었습니다.)'
      ])
    };
  }

  // 로드맵에 '작성완료' 로 표시 (주소는 발행 확인이 나중에 채운다)
  var 순서탭 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  순서탭.getRange(대상.행, 14).setValue('작성완료');
  순서탭.getRange(대상.행, 16).setValue(
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd'));
  // 소제목을 적어 둔다. 다음 글이 겹치는지 보는 데 쓴다.
  순서탭.getRange(대상.행, 17).setValue(검사.소제목.join('|'));
  SpreadsheetApp.flush();

  // 다음에 쓸 글 (이어서 쓰기 버튼용)
  var 다음 = null;
  찾음.목록.slice().sort(function (가, 나) { return 가.순번 - 나.순번; })
    .forEach(function (한줄) {
      if (다음) return;
      if (한줄.순번 <= 대상.순번) return;
      if (한줄.작성상태 === '발행완료' || 한줄.작성상태 === '작성완료') return;
      다음 = { 순번: 한줄.순번, 제목: 한줄.제목, 클러스터: 한줄.클러스터,
               단계: 한줄.단계, 메인키워드: 한줄.메인키워드 };
    });

  return {
    성공: true,
    상태: 결과.상태,
    행번호: 행번호,
    메모: 결과.메모 || [],
    넣은링크: 링크.length,
    채운링크: 채움.채운것.map(function (한개) { return 한개.제목; }),
    경고: 검사.경고,
    다음: 다음
  };
}


// ══════════════════════════════════════════════════════════
//  발행 확인 뒤 — 찾은 주소를 링크관리에 적는다
//
//  이게 돌아야 다음 글이 이 글을 링크로 걸 수 있다.
//  발행확인() 이 끝날 때 자동으로 불린다. 메뉴로 따로 눌러도 된다.
// ══════════════════════════════════════════════════════════

/**
 * 티스토리 블로그에서 발행된 글 목록을 통째로 읽어 온다.
 *
 * 지금까지는 RSS 만 봤다. 그런데 티스토리 RSS 는 최근 글 몇십 개만
 * 싣는다. 하루 7편씩 오래 쓰면 옛 글은 RSS 에서 밀려나 영영 못 찾는다.
 *
 * 그래서 sitemap.xml 을 쓴다. 여기에는 글이 다 들어 있다.
 * 다만 주소만 있고 제목이 없으므로, 각 주소를 열어 <title> 을 읽는다.
 * 한 번에 여러 개를 같이 받아 와서(fetchAll) 시간을 줄인다.
 */
var finwiz목록보관 = {};

function finwiz티스토리목록(도메인) {
  if (finwiz목록보관[도메인]) return finwiz목록보관[도메인];

  var 결과 = { 글들: [], 주소수: 0, 실패: '' };
  var 주소들 = [];

  // 1) sitemap 에서 글 주소를 모은다 (sitemap 안에 sitemap 이 또 있을 수 있다)
  var 볼것 = ['https://' + 도메인 + '/sitemap.xml'];
  var 본것 = {};
  for (var 회 = 0; 회 < 6 && 볼것.length; 회++) {
    var 이번 = 볼것.shift();
    if (본것[이번]) continue;
    본것[이번] = true;
    var 원문 = '';
    try {
      var 답 = UrlFetchApp.fetch(이번, 브라우저인척);
      if (답.getResponseCode() === 200) 원문 = 답.getContentText();
    } catch (e) { }
    if (!원문) continue;

    var 찾기 = /<loc>\s*([^<\s]+)\s*<\/loc>/g, 한개;
    var 색인인가 = 원문.indexOf('<sitemapindex') !== -1;
    while ((한개 = 찾기.exec(원문)) !== null) {
      var 주소 = 한개[1];
      if (색인인가) { 볼것.push(주소); continue; }
      // 글 주소만 고른다. 티스토리 글은 끝이 숫자다.
      if (/\/\d+\/?$/.test(주소)) 주소들.push(주소.replace(/\/$/, ''));
    }
  }

  주소들 = 주소들.filter(function (값, i) { return 주소들.indexOf(값) === i; });
  결과.주소수 = 주소들.length;
  if (!주소들.length) {
    결과.실패 = 'sitemap.xml 에서 글 주소를 찾지 못했습니다';
    finwiz목록보관[도메인] = 결과;
    return 결과;
  }

  // 2) 주소를 20개씩 묶어 열어서 제목을 읽는다
  for (var i = 0; i < 주소들.length; i += 20) {
    var 묶음 = 주소들.slice(i, i + 20);
    var 답들 = [];
    try {
      답들 = UrlFetchApp.fetchAll(묶음.map(function (주소) {
        return { url: 주소, muteHttpExceptions: true, followRedirects: true,
                 headers: 브라우저인척.headers };
      }));
    } catch (e) { continue; }

    답들.forEach(function (답, k) {
      var 몸통 = '';
      try { if (답.getResponseCode() === 200) 몸통 = 답.getContentText(); } catch (e) { }
      if (!몸통) return;
      var 제목찾기 = 몸통.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
      if (!제목찾기) return;
      var 제목 = finwiz제목다듬기(제목찾기[1]);
      if (제목) 결과.글들.push({ 제목: 제목, URL: 묶음[k] });
    });
  }

  if (!결과.글들.length) 결과.실패 = '글 주소는 찾았는데 제목을 읽지 못했습니다';
  finwiz목록보관[도메인] = 결과;
  return 결과;
}

/** 브라우저 제목에서 블로그 이름과 군더더기를 뗀다. */
function finwiz제목다듬기(글자) {
  var 제목 = String(글자 || '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ').trim();
  // '제목 :: 블로그이름' / '제목 - 블로그이름' / '제목 | 블로그이름'
  var 자리 = 제목.indexOf(' :: ');
  if (자리 > 0) 제목 = 제목.substring(0, 자리);
  return 제목.trim();
}


function finwiz주소반영() {
  var 링크맵 = finwiz링크읽기();
  if (!Object.keys(링크맵).length) return { 반영: 0, 진단: null };

  // 제목 → 링크관리 줄
  var 제목목록 = [];
  for (var id in 링크맵) 제목목록.push(링크맵[id]);

  var 문서 = finwiz문서();
  var 찾은주소 = {};   // ID → URL

  // 왜 못 찾았는지 알려 주기 위해 세어 둔다.
  // '없습니다' 만 보여 주면 무엇을 고쳐야 할지 알 수 없다.
  var 진단 = {
    탭없음: [], 줄수: 0, 주소있는줄: 0, 주소빈줄: 0,
    제목맞은줄: 0, 제목틀린예: [], 주소빈예: [],
    이미있음: 0, 손으로적음: 0,
    블로그도메인: '', 블로그글수: 0, 블로그주소수: 0,
    블로그에서찾음: 0, 블로그실패: '', 아직없는글: 0
  };

  [탭이름, 완료탭이름].forEach(function (이름) {
    var 탭 = 문서.getSheetByName(이름);
    if (!탭 || 탭.getLastRow() < 2) { 진단.탭없음.push(이름); return; }
    var 헤더 = 탭.getRange(1, 1, 1, 탭.getLastColumn()).getValues()[0];
    var 열 = 열찾기(헤더);
    if (열.title === -1 && 열['ts제목'] === -1) { 진단.탭없음.push(이름 + '(제목 칸 없음)'); return; }

    탭.getRange(2, 1, 탭.getLastRow() - 1, 탭.getLastColumn()).getValues()
      .forEach(function (행) {
        var 제목들 = [];
        if (열.title !== -1) 제목들.push(String(행[열.title] || '').trim());
        if (열['ts제목'] !== -1 && 열['ts제목'] !== undefined) {
          제목들.push(String(행[열['ts제목']] || '').trim());
        }
        var 보이는제목 = 제목들.filter(function (한개) { return !!한개; })[0] || '';
        if (!보이는제목) return;
        진단.줄수++;

        var 주소 = '';
        if (열['ts주소'] !== -1 && 열['ts주소'] !== undefined) {
          주소 = String(행[열['ts주소']] || '').trim();
        }
        if (!주소) {
          진단.주소빈줄++;
          if (진단.주소빈예.length < 3) 진단.주소빈예.push(보이는제목);
          return;
        }
        진단.주소있는줄++;

        var 맞음 = false;
        제목목록.forEach(function (글) {
          if (맞음) return;
          for (var i = 0; i < 제목들.length; i++) {
            if (제목들[i] && 제목같나(제목들[i], 글.제목)) {
              맞음 = true;
              if (글.URL) { 진단.이미있음++; return; }
              if (!찾은주소[글.ID]) 찾은주소[글.ID] = 주소;
              return;
            }
          }
        });
        if (맞음) 진단.제목맞은줄++;
        else if (진단.제목틀린예.length < 3) 진단.제목틀린예.push(보이는제목);
      });
  });

  var 링크탭 = finwiz탭(finwiz링크탭, finwiz링크헤더);
  var 순서탭 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  var 순서목록 = finwiz순서읽기();
  var 순서행 = {};
  순서목록.forEach(function (한줄) { 순서행[한줄.ID] = 한줄.행; });

  // 사람이 최종작성순서 탭의 '발행URL' 칸에 주소를 직접 적어 넣었으면
  // 그것도 받아들인다.
  순서목록.forEach(function (한줄) {
    var 손주소 = String(한줄.발행URL || '').trim();
    if (!손주소) return;
    var 글 = 링크맵[한줄.ID];
    if (!글 || 글.URL) return;
    if (찾은주소[한줄.ID]) return;
    찾은주소[한줄.ID] = 손주소;
    진단.손으로적음++;
  });

  // 시트에 주소가 없는 글은 블로그에서 직접 찾는다.
  // 시트를 거치지 않으므로, 시트에 흔적이 없는 옛 글도 살아난다.
  var 아직없음 = 제목목록.filter(function (글) {
    return !글.URL && !찾은주소[글.ID];
  });
  if (아직없음.length) {
    var 도메인 = (typeof 카테고리표 !== 'undefined' && 카테고리표[finwiz카테고리])
                   ? 카테고리표[finwiz카테고리].티스토리 : '';
    if (도메인) {
      var 목록 = finwiz티스토리목록(도메인);
      진단.블로그도메인 = 도메인;
      진단.블로그글수 = 목록.글들.length;
      진단.블로그주소수 = 목록.주소수;
      진단.블로그실패 = 목록.실패;
      목록.글들.forEach(function (블로그글) {
        아직없음.forEach(function (글) {
          if (찾은주소[글.ID]) return;
          if (제목같나(블로그글.제목, 글.제목)) {
            찾은주소[글.ID] = 블로그글.URL;
            진단.블로그에서찾음 = (진단.블로그에서찾음 || 0) + 1;
          }
        });
      });
    }
  }

  진단.아직없는글 = 제목목록.filter(function (글) {
    return !글.URL && !찾은주소[글.ID];
  }).length;

  var 반영 = 0;
  for (var id2 in 찾은주소) {
    if (!링크맵[id2]) continue;
    링크탭.getRange(링크맵[id2].행, 4).setValue(찾은주소[id2]);
    링크탭.getRange(링크맵[id2].행, 3).setValue('발행완료');
    if (순서행[id2]) {
      순서탭.getRange(순서행[id2], 15).setValue(찾은주소[id2]);
      순서탭.getRange(순서행[id2], 14).setValue('발행완료');
    }
    반영++;
  }
  if (반영) SpreadsheetApp.flush();
  return { 반영: 반영, 진단: 진단 };
}

// ══════════════════════════════════════════════════════════
//  이미 쓴 글의 링크 손보기
//
//  글을 쓸 때는 아직 발행 안 된 글의 주소를 몰라서 링크를 못 걸었다.
//  나중에 그 글이 발행되면 주소가 생긴다. 그때 뒤늦게 채워 넣는다.
// ══════════════════════════════════════════════════════════

/**
 * 오늘작성 / 발행완료 탭을 훑어서 finwiz 글의 링크 상태를 본다.
 *
 * 고칠 수 있는 것과 없는 것을 나눈다.
 *   - 오늘작성에 남아 있는 글 : 아직 발행 전이니 지금 바로 고칠 수 있다
 *   - 발행완료로 넘어간 글     : 티스토리에서 직접 고쳐야 한다
 */
function finwiz링크점검(고칠까) {
  var 순서목록 = finwiz순서읽기();
  if (!순서목록.length) throw new Error('먼저 "📥 finwiz 로드맵 가져오기" 를 눌러 주세요.');
  var 링크맵 = finwiz링크읽기();

  var 문서 = finwiz문서();
  var 고친것 = [], 손봐야할것 = [], 멀쩡한것 = 0, 본문없음 = 0;

  [탭이름, 완료탭이름].forEach(function (이름) {
    var 탭 = 문서.getSheetByName(이름);
    if (!탭 || 탭.getLastRow() < 2) return;
    var 발행된탭 = (이름 === 완료탭이름);

    var 헤더 = 탭.getRange(1, 1, 1, 탭.getLastColumn()).getValues()[0];
    var 열 = 열찾기(헤더);
    if (열.title === -1) return;

    var 값들 = 탭.getRange(2, 1, 탭.getLastRow() - 1, 탭.getLastColumn()).getValues();

    값들.forEach(function (행, 자리) {
      var 제목 = String(행[열.title] || '').trim();
      if (!제목) return;

      // 이 줄이 로드맵의 몇 번 글인가
      var 대상 = null;
      for (var i = 0; i < 순서목록.length; i++) {
        if (제목같나(제목, 순서목록[i].제목)) { 대상 = 순서목록[i]; break; }
      }
      if (!대상) return;

      var 본문칸 = (열['ts본문'] !== -1 && 열['ts본문'] !== undefined && 행[열['ts본문']])
                     ? 열['ts본문'] : 열.html;
      if (본문칸 === -1 || 본문칸 === undefined) { 본문없음++; return; }
      var 본문 = String(행[본문칸] || '');
      if (!본문) { 본문없음++; return; }

      // 지금 주소로 다시 고르면 무엇이 걸려야 하는가
      var 링크 = finwiz링크고르기(대상, 링크맵, 순서목록);
      var 빠진것 = 링크.filter(function (한개) { return 본문.indexOf(한개.URL) === -1; });
      if (!빠진것.length) { 멀쩡한것++; return; }

      if (발행된탭) {
        // 이미 발행된 글은 시트만 고쳐 봐야 소용없다. 사람이 티스토리에서 고친다.
        손봐야할것.push({
          순번: 대상.순번, 제목: 대상.제목,
          주소: (열['ts주소'] !== -1 && 열['ts주소'] !== undefined)
                  ? String(행[열['ts주소']] || '') : '',
          빠진링크: 빠진것
        });
        return;
      }

      if (!고칠까) {
        고친것.push({ 순번: 대상.순번, 제목: 대상.제목, 빠진링크: 빠진것, 미리보기: true });
        return;
      }
      var 채움 = finwiz링크채우기(본문, 링크);
      탭.getRange(자리 + 2, 본문칸 + 1).setValue(채움.HTML);
      고친것.push({ 순번: 대상.순번, 제목: 대상.제목, 빠진링크: 채움.채운것 });
    });
  });

  고친것.sort(function (가, 나) { return 가.순번 - 나.순번; });
  손봐야할것.sort(function (가, 나) { return 가.순번 - 나.순번; });
  return { 고친것: 고친것, 손봐야할것: 손봐야할것, 멀쩡한것: 멀쩡한것, 본문없음: 본문없음 };
}


function finwiz링크점검메뉴() {
  var ui = SpreadsheetApp.getUi();
  try {
    var 미리 = finwiz링크점검(false);

    if (!미리.고친것.length && !미리.손봐야할것.length) {
      ui.alert('빠진 링크가 없습니다.\n\n' +
               '검사한 글 중 ' + 미리.멀쩡한것 + '편은 링크가 다 들어가 있습니다.');
      return;
    }

    var 말 = '';
    if (미리.고친것.length) {
      말 += '■ 지금 바로 고칠 수 있는 글 ' + 미리.고친것.length + '편\n' +
            '   (아직 발행 전이라 시트에서 고치면 됩니다)\n\n';
      미리.고친것.slice(0, 8).forEach(function (한개) {
        말 += '   ' + 한개.순번 + '번  ' + 한개.제목.substring(0, 30) +
              '  → 링크 ' + 한개.빠진링크.length + '개 추가\n';
      });
      if (미리.고친것.length > 8) 말 += '   … 외 ' + (미리.고친것.length - 8) + '편\n';
      말 += '\n';
    }
    if (미리.손봐야할것.length) {
      말 += '■ 이미 발행돼서 손으로 고쳐야 하는 글 ' + 미리.손봐야할것.length + '편\n' +
            '   (티스토리에서 직접 고쳐야 합니다. 아래 "아니오" 를 누르면\n' +
            '    목록을 새 탭에 적어 드립니다)\n\n';
    }
    말 += '지금 고칠 수 있는 것만 고칠까요?';

    var 답 = ui.alert('빠진 링크 점검', 말, ui.ButtonSet.YES_NO);
    if (답 !== ui.Button.YES) {
      if (미리.손봐야할것.length) finwiz손볼목록쓰기(미리.손봐야할것);
      return;
    }

    var 결과 = finwiz링크점검(true);
    var 끝말 = 결과.고친것.length + '편의 글에 빠진 링크를 채웠습니다.\n' +
               '이제 그 글들을 발행하시면 됩니다.';
    if (결과.손봐야할것.length) {
      finwiz손볼목록쓰기(결과.손봐야할것);
      끝말 += '\n\n이미 발행된 ' + 결과.손봐야할것.length + '편은 시트에서 못 고칩니다.\n' +
              "'finwiz_손볼글' 탭에 목록을 적어 뒀습니다.";
    }
    ui.alert(끝말);
  } catch (오류) {
    ui.alert('하지 못했습니다.\n\n' + 오류.message);
  }
}


/** 이미 발행돼서 손으로 고쳐야 하는 글을 새 탭에 적는다. */
function finwiz손볼목록쓰기(손봐야할것) {
  var 헤더 = ['순번', '글 제목', '글 주소', '넣을 링크 제목', '넣을 링크 주소', '붙여넣을 HTML'];
  var 탭 = finwiz탭('finwiz_손볼글', 헤더);
  if (탭.getLastRow() > 1) 탭.getRange(2, 1, 탭.getLastRow() - 1, 헤더.length).clearContent();

  var 줄들 = [];
  손봐야할것.forEach(function (한개) {
    한개.빠진링크.forEach(function (링크) {
      줄들.push([
        한개.순번, 한개.제목, 한개.주소, 링크.제목, 링크.URL,
        '<li><a href="' + 링크.URL + '" target="_blank" rel="noopener">' +
          링크.제목 + '</a></li>'
      ]);
    });
  });
  if (줄들.length) 탭.getRange(2, 1, 줄들.length, 헤더.length).setValues(줄들);
  return 줄들.length;
}


function finwiz주소반영메뉴() {
  var ui = SpreadsheetApp.getUi();
  try {
    var 결과 = finwiz주소반영();
    var 진 = 결과.진단;

    if (결과.반영) {
      var 말 = '새로 확인된 글 주소 ' + 결과.반영 + '개를 ' + finwiz링크탭 + ' 에 적었습니다.\n' +
               '이제 다음 글부터 이 글들을 링크로 걸 수 있습니다.';
      if (진 && 진.블로그에서찾음) {
        말 += '\n\n그중 ' + 진.블로그에서찾음 + '개는 ' + 진.블로그도메인 +
              ' 에서 직접 찾아온 주소입니다.';
      }
      if (진 && 진.손으로적음) {
        말 += '\n' + 진.손으로적음 + '개는 사장님이 발행URL 칸에 직접 적으신 주소입니다.';
      }
      if (진 && 진.아직없는글) {
        말 += '\n\n아직 주소를 못 찾은 글이 ' + 진.아직없는글 + '편 남았습니다.\n' +
              '한 번 더 눌러 보시고, 그래도 남으면 발행URL 칸에 직접 적어 주세요.';
      }
      ui.alert(말);
      return;
    }

    // 못 찾았을 때는 어디서 막혔는지 알려 준다
    if (!진) { ui.alert('로드맵이 비어 있습니다.\n"📥 finwiz 로드맵 가져오기" 를 먼저 눌러 주세요.'); return; }

    var 글 = '새로 적을 주소가 없습니다.\n\n';
    글 += '● 아직 주소가 없는 로드맵 글 : ' + 진.아직없는글 + '편  ← 이게 문제입니다\n';
    글 += '● 이미 주소가 있는 글        : ' + 진.이미있음 + '편\n\n';
    글 += '[블로그에서 직접 찾아본 결과]\n';
    if (진.블로그도메인) {
      글 += '  ' + 진.블로그도메인 + ' 에서 글 주소 ' + 진.블로그주소수 + '개를 찾았고,\n';
      글 += '  그중 ' + 진.블로그글수 + '개의 제목을 읽었습니다.\n';
      if (진.블로그실패) 글 += '  → ' + 진.블로그실패 + '\n';
    } else {
      글 += '  블로그 주소를 몰라 찾아보지 못했습니다.\n';
    }
    글 += '\n[시트에서 찾아본 결과]\n';
    글 += '  오늘작성+발행완료에서 본 글 ' + 진.줄수 + '줄 중\n';
    글 += '  티스토리 주소가 적힌 글 ' + 진.주소있는줄 + '줄 / 빈 글 ' + 진.주소빈줄 + '줄\n\n';

    if (진.블로그도메인 && 진.블로그글수 === 0) {
      글 += '【무엇이 문제인가】\n';
      글 += '블로그에서 글 목록을 못 읽었습니다. 아래를 확인해 주세요.\n\n';
      글 += '1. 브라우저로 ' + 진.블로그도메인 + '/sitemap.xml 을 열어 보세요.\n';
      글 += '   글 주소 목록이 보여야 합니다. 안 보이면 티스토리 관리 →\n';
      글 += '   블로그 설정에서 "검색엔진 수집 허용" 을 켜 주세요.\n';
      글 += '2. 블로그가 비공개면 읽을 수 없습니다.\n';
      글 += '3. 그래도 안 되면 4번 방법을 쓰세요.\n';
    } else if (진.아직없는글 > 0 && 진.블로그글수 > 0) {
      글 += '【무엇이 문제인가】\n';
      글 += '블로그 글 ' + 진.블로그글수 + '개를 읽었는데 로드맵 제목과 맞는 것이 없습니다.\n';
      글 += '발행할 때 제목이 바뀐 것 같습니다.\n\n';
      글 += '【해 볼 것】\n';
      글 += '4번 방법(직접 붙여넣기)을 쓰시면 확실합니다.\n';
    } else if (진.아직없는글 === 0) {
      글 += '【결론】\n';
      글 += '주소가 다 채워져 있습니다. 이 상태가 정상입니다.\n';
      ui.alert('finwiz 링크 갱신', 글, ui.ButtonSet.OK);
      return;
    }

    글 += '\n【4번 · 직접 붙여넣기 — 가장 확실합니다】\n';
    글 += '01_최종작성순서 탭의 "발행URL" 칸(O열)에 글 주소를 붙여넣고\n';
    글 += '이 메뉴를 다시 누르세요. 손으로 적은 주소도 그대로 씁니다.\n';
    글 += '티스토리 관리 → 글 관리 에서 주소를 복사하시면 됩니다.\n';
    글 += '기둥 글 몇 개만 채워도 링크가 훨씬 좋아집니다.';

    ui.alert('finwiz 링크 갱신', 글, ui.ButtonSet.OK);
  } catch (오류) {
    ui.alert('하지 못했습니다.\n\n' + 오류.message);
  }
}


// ══════════════════════════════════════════════════════════
//  화면 열기
// ══════════════════════════════════════════════════════════

function finwiz화면열기() {
  var 화면 = HtmlService.createHtmlOutputFromFile('finwiz화면')
    .setTitle('finwiz 순서대로 글쓰기');
  SpreadsheetApp.getUi().showSidebar(화면);
}

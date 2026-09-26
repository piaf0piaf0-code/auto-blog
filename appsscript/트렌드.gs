// ══════════════════════════════════════════════════════════
//  트렌드에서 건지기
//
//  윈도우 '인기 검색어' 처럼 사람들이 지금 치는 말을 한눈에 보는 것은
//  직관적이다. 하지만 그대로 쓰면 돈이 안 된다.
//
//      축구중계 · 영화 · 선수 이름   → 언론사가 1위, 사흘이면 끝, 단가 낮음
//      '○○ 사망' 같은 소식          → 확인 안 된 소식일 수 있다. 쓰면 안 된다
//      청년미래적금                  → 금융·정책, 단가 높음, 몇 달 검색된다
//
//  트렌드 목록은 그물이다. 던져서 우리 분야에 걸리는 것만 건진다.
//
//  출처는 구글 트렌드의 한국 인기 검색어 RSS 다. 블로그 방문자가
//  대부분 구글에서 오므로 구글 목록이 맞다. 열쇠가 필요 없고 무료다.
//  윈도우 목록(빙)은 공식으로 가져올 길이 없다.
// ══════════════════════════════════════════════════════════

var TR탭 = 'TR_후보';

var TR주소들 = [
  'https://trends.google.com/trending/rss?geo=KR',
  'https://trends.google.com/trends/trendingsearches/daily/rss?geo=KR'
];

// 이런 말이 들어간 검색어는 쓰지 않는다.
// 실존 인물의 죽음·사고·사생활은 틀리면 사람을 다치게 하고,
// 애드센스도 이런 민감한 사건 글에는 광고를 막는다.
var TR버림말 = [
  '사망', '숨져', '숨진', '별세', '부고', '타계', '유족', '빈소',
  '사고', '참사', '추락', '화재', '폭발', '실종', '자살', '극단적',
  '살인', '폭행', '성범죄', '성폭행', '마약', '구속', '체포',
  '열애', '결별', '이혼', '불륜', '논란', '루머', '폭로', '학폭'
];

// 우리 분야에 걸리는 말. 위에서부터 먼저 맞는 쪽으로 보낸다.
// 카테고리 이름은 카테고리표(260816글쓰기.gs)와 같아야 한다.
var TR분야표 = [
  { 카테고리: '대출관련',
    말: ['대출', '금리', '적금', '예금', '통장', '계좌', '저축', '이자', '신용점수',
         '신용등급', '햇살론', '전세', '월세', '주담대', '주택담보', '청약', '카드론',
         '리볼빙', '채무', '파산', '회생', '보험료', '보험금'] },
  { 카테고리: '정책지원',
    말: ['지원금', '장려금', '수당', '급여', '바우처', '환급', '보조금', '지원사업',
         '연금', '연말정산', '종합소득세', '세금', '감면', '공제', '신청방법', '신청기간',
         '정부지원', '국민지원', '민생', '고용보험', '실업급여', '기초생활'] },
  { 카테고리: '웰빙',
    말: ['건강', '증상', '질환', '당뇨', '혈압', '혈당', '콜레스테롤', '콩팥', '신장',
         '영양제', '비타민', '다이어트', '수면', '불면', '독감', '백신', '예방접종',
         '감기', '코로나', '식단', '운동법', '통증'] }
];


// ── 받아서 읽기 ─────────────────────────────────────────

function TR받아오기() {
  var 머리 = (typeof 브라우저인척 !== 'undefined') ? 브라우저인척.headers : {};
  var 마지막 = '';
  for (var i = 0; i < TR주소들.length; i++) {
    try {
      var 답 = UrlFetchApp.fetch(TR주소들[i],
        { muteHttpExceptions: true, followRedirects: true, headers: 머리 });
      var 코드 = 답.getResponseCode();
      if (코드 === 200) {
        var 원문 = 답.getContentText();
        if (원문.indexOf('<item') !== -1) return 원문;
        마지막 = TR주소들[i] + ' — 글 목록이 비어 있음';
      } else {
        마지막 = TR주소들[i] + ' — 응답 ' + 코드;
      }
    } catch (오류) {
      마지막 = TR주소들[i] + ' — ' + 오류;
    }
  }
  throw new Error('구글 트렌드를 받지 못했습니다. ' + 마지막);
}

function TR글자(글) {
  return String(글 || '')
    .replace(/<!\[CDATA\[|\]\]>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&apos;/g, "'")
    .replace(/\s+/g, ' ').trim();
}

/** 이름공간 앞머리(ht:)가 붙든 안 붙든 꺼낸다 */
function TR칸(덩어리, 이름) {
  var m = new RegExp('<(?:[\\w-]+:)?' + 이름 + '[^>]*>([\\s\\S]*?)</(?:[\\w-]+:)?' + 이름 + '>').exec(덩어리);
  return m ? TR글자(m[1]) : '';
}

function TR읽기(원문) {
  var 항목들 = String(원문 || '').match(/<item[\s\S]*?<\/item>/g) || [];
  return 항목들.map(function (덩어리) {
    return {
      검색어: TR칸(덩어리, 'title'),
      검색량: TR칸(덩어리, 'approx_traffic'),
      뉴스: TR칸(덩어리, 'news_item_title'),
      뉴스주소: TR칸(덩어리, 'news_item_url')
    };
  }).filter(function (하나) { return 하나.검색어; });
}


// ── 가르기 ──────────────────────────────────────────────

function TR열쇠(글자) {
  return String(글자 || '').replace(/[^0-9A-Za-z가-힣]/g, '').toLowerCase();
}

/**
 * 한 검색어의 판정.
 *   씀     우리 분야에 걸림 → 후보 탭에 올림
 *   안 씀  민감한 소식 → 버림
 *   건너뜀 우리 분야가 아님
 */
function TR가르기(하나) {
  var 열쇠 = TR열쇠(하나.검색어);
  var 뉴스열쇠 = TR열쇠(하나.뉴스);

  for (var i = 0; i < TR버림말.length; i++) {
    var 말 = TR열쇠(TR버림말[i]);
    // 뉴스 제목까지 본다. 검색어는 이름뿐인데 뉴스가 부고인 경우가 많다.
    if (열쇠.indexOf(말) !== -1 || 뉴스열쇠.indexOf(말) !== -1) {
      return { 판정: '안 씀', 카테고리: '', 이유: "'" + TR버림말[i] + "' 소식 — 확인 안 된 내용일 수 있고 사람을 다치게 할 수 있습니다" };
    }
  }

  for (var k = 0; k < TR분야표.length; k++) {
    var 분야 = TR분야표[k];
    for (var j = 0; j < 분야.말.length; j++) {
      if (열쇠.indexOf(TR열쇠(분야.말[j])) !== -1) {
        return { 판정: '씀', 카테고리: 분야.카테고리,
                 이유: "'" + 분야.말[j] + "' — 단가가 높고 오래 검색되는 분야" };
      }
    }
  }
  return { 판정: '건너뜀', 카테고리: '', 이유: '우리 분야가 아님 — 언론사가 1위, 며칠이면 끝' };
}

/** 오늘작성·발행완료·후보 탭에 이미 있는 키워드 */
function TR이미있는것() {
  var 모음 = {};
  var 문서 = SpreadsheetApp.getActive();
  var 볼탭 = ['오늘작성', (typeof 완료탭이름 !== 'undefined' ? 완료탭이름 : '발행완료'), TR탭];
  볼탭.forEach(function (이름) {
    var 탭 = 문서.getSheetByName(이름);
    if (!탭 || 탭.getLastRow() < 2) return;
    var 열 = (이름 === TR탭) ? 2 : 1;   // 후보 탭은 B열, 나머지는 A열이 키워드
    탭.getRange(2, 열, 탭.getLastRow() - 1, 1).getValues().forEach(function (줄) {
      var k = TR열쇠(줄[0]);
      if (k) 모음[k] = true;
    });
  });
  return 모음;
}


// ── 후보 탭 ─────────────────────────────────────────────

var TR머리 = ['받은 날', '검색어', '검색량', '카테고리', '이유', '관련 뉴스', '뉴스 주소', '오늘작성으로', '옮긴 날'];

function TR탭가져오기() {
  var 문서 = SpreadsheetApp.getActive();
  var 탭 = 문서.getSheetByName(TR탭);
  if (!탭) {
    탭 = 문서.insertSheet(TR탭);
    탭.getRange(1, 1, 1, TR머리.length).setValues([TR머리]);
    탭.getRange(1, 1, 1, TR머리.length).setFontWeight('bold').setBackground('#e8eaed');
    탭.setFrozenRows(1);
  }
  return 탭;
}

/**
 * 구글 트렌드를 받아 우리 분야에 걸리는 것만 후보 탭에 쌓는다.
 * 매일 자동 실행에서도 부르므로 화면(알림창)을 쓰지 않는다.
 */
function 트렌드받기() {
  var 시간대 = Session.getScriptTimeZone() || 'Asia/Seoul';
  var 오늘 = Utilities.formatDate(new Date(), 시간대, 'yyyy-MM-dd');
  var 목록 = TR읽기(TR받아오기());
  var 있던것 = TR이미있는것();

  var 전부 = [], 새후보 = [], 이번에본것 = {};
  목록.forEach(function (하나) {
    // 같은 검색어가 목록에 두 번 오면 두 번째는 보여 주지도 않는다
    var 본열쇠 = TR열쇠(하나.검색어);
    if (이번에본것[본열쇠]) return;
    이번에본것[본열쇠] = true;
    var 갈래 = TR가르기(하나);
    var 줄 = { 검색어: 하나.검색어, 검색량: 하나.검색량, 판정: 갈래.판정,
               카테고리: 갈래.카테고리, 이유: 갈래.이유, 뉴스: 하나.뉴스, 뉴스주소: 하나.뉴스주소 };
    if (갈래.판정 === '씀' && 있던것[TR열쇠(하나.검색어)]) {
      줄.판정 = '이미 있음';
      줄.이유 = '오늘작성·발행완료·후보에 이미 있는 키워드';
    }
    전부.push(줄);
    if (줄.판정 === '씀') {
      새후보.push(줄);
      있던것[TR열쇠(하나.검색어)] = true;   // 같은 날 목록 안의 중복도 막는다
    }
  });

  if (새후보.length) {
    var 탭 = TR탭가져오기();
    var 시작 = 탭.getLastRow() + 1;
    탭.getRange(시작, 1, 새후보.length, TR머리.length).setValues(새후보.map(function (줄) {
      return [오늘, 줄.검색어, 줄.검색량, 줄.카테고리, 줄.이유, 줄.뉴스, 줄.뉴스주소, false, ''];
    }));
    try {
      var 체크 = SpreadsheetApp.newDataValidation().requireCheckbox().build();
      탭.getRange(시작, 8, 새후보.length, 1).setDataValidation(체크);
    } catch (e) { }
  }

  return { 날짜: 오늘, 전부: 전부, 새후보: 새후보.length };
}


// ── 체크한 것을 오늘작성으로 ────────────────────────────

/**
 * 후보 탭에서 체크한 줄을 오늘작성으로 옮긴다.
 * 롱테일 자동 채우기를 이어서 돌려, 채워지면 바로 글쓰기대기가 된다.
 */
function 트렌드옮기기() {
  var 탭 = SpreadsheetApp.getActive().getSheetByName(TR탭);
  if (!탭 || 탭.getLastRow() < 2) return { 옮김: 0, 목록: [] };

  var 시간대 = Session.getScriptTimeZone() || 'Asia/Seoul';
  var 오늘 = Utilities.formatDate(new Date(), 시간대, 'yyyy-MM-dd');
  var 값 = 탭.getRange(2, 1, 탭.getLastRow() - 1, TR머리.length).getValues();

  var 준비 = 시트준비();
  var 오늘탭 = 준비.탭, 열 = 준비.열;
  var 열수 = 오늘탭.getLastColumn();
  var 이미 = TR이미있는것오늘작성(오늘탭, 열);

  var 옮김 = [];
  값.forEach(function (줄, i) {
    var 체크 = 줄[7] === true || String(줄[7]).toUpperCase() === 'TRUE';
    if (!체크 || 줄[8]) return;   // 안 골랐거나 이미 옮김
    var 키워드 = String(줄[1] || '').trim();
    if (!키워드) return;
    var 행번호 = i + 2;

    if (!이미[TR열쇠(키워드)]) {
      var 새줄 = [];
      for (var c = 0; c < 열수; c++) 새줄.push('');
      새줄[열.keyword] = 키워드;
      새줄[열.category] = String(줄[3] || '').trim();
      오늘탭.getRange(오늘탭.getLastRow() + 1, 1, 1, 열수).setValues([새줄]);
      이미[TR열쇠(키워드)] = true;
      옮김.push(키워드);
    }
    탭.getRange(행번호, 9).setValue(오늘);
  });

  var 롱테일 = null;
  if (옮김.length && typeof 롱테일채우기 === 'function') {
    try { 롱테일 = 롱테일채우기(); } catch (e) { 롱테일 = null; }
  }
  return { 옮김: 옮김.length, 목록: 옮김, 롱테일: 롱테일 };
}

function TR이미있는것오늘작성(오늘탭, 열) {
  var 모음 = {};
  if (오늘탭.getLastRow() < 2) return 모음;
  오늘탭.getRange(2, 열.keyword + 1, 오늘탭.getLastRow() - 1, 1).getValues()
    .forEach(function (줄) { var k = TR열쇠(줄[0]); if (k) 모음[k] = true; });
  return 모음;
}


// ── 메뉴 ────────────────────────────────────────────────

function 트렌드받기메뉴() {
  var 화면 = SpreadsheetApp.getUi();
  var 결과;
  try {
    결과 = 트렌드받기();
  } catch (오류) {
    화면.alert(String(오류.message || 오류) +
      '\n\n구글이 주소를 바꿨을 수 있습니다. 이 문구를 그대로 보내 주세요.');
    return;
  }

  var 줄 = ['구글 트렌드 한국 인기 검색어 (' + 결과.날짜 + ')', ''];
  var 표시 = { '씀': '✔ 씀', '이미 있음': '· 이미 있음', '안 씀': '✕ 안 씀', '건너뜀': '  건너뜀' };
  결과.전부.forEach(function (하나) {
    줄.push((표시[하나.판정] || 하나.판정) + '  ' + 하나.검색어 +
            (하나.카테고리 ? '  → ' + 하나.카테고리 : ''));
  });
  줄.push('');
  줄.push(결과.새후보
    ? '새 후보 ' + 결과.새후보 + '개를 ' + TR탭 + ' 탭에 올렸습니다.\n쓸 것만 체크한 뒤 메뉴의 ➡️ 체크한 트렌드 오늘작성으로 를 누르세요.'
    : '오늘은 우리 분야에 걸린 새 검색어가 없습니다.');
  화면.alert(줄.join('\n'));
}

function 트렌드옮기기메뉴() {
  var 화면 = SpreadsheetApp.getUi();
  var 결과;
  try {
    결과 = 트렌드옮기기();
  } catch (오류) {
    화면.alert('옮기다가 문제가 생겼습니다.\n\n' + String(오류.message || 오류));
    return;
  }
  if (!결과.옮김) {
    화면.alert(TR탭 + " 탭의 '오늘작성으로' 칸에서 체크한 줄이 없습니다.");
    return;
  }
  var 줄 = [결과.옮김 + '개를 오늘작성으로 옮겼습니다.', ''];
  결과.목록.forEach(function (k) { 줄.push('  ' + k); });
  if (결과.롱테일) {
    줄.push('');
    줄.push('롱테일 자동 채우기: 채움 ' + 결과.롱테일.채움 + ' · 보류 ' + 결과.롱테일.보류);
    줄.push('채워진 줄은 글쓰기대기가 되어 다음 글쓰기 때 바로 쓰입니다.');
  }
  화면.alert(줄.join('\n'));
}

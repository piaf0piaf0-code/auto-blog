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
         '정부지원', '국민지원', '민생', '고용보험', '실업급여', '기초생활',
         '월세지원', '페이백', '소비쿠폰', '지역화폐', '출산지원', '육아휴직'] },
  // 최신이슈 — 뉴스 요약이 아니라 '생활 일정'. 해마다 돌아오고 독자가 무언가 해야 하는 것.
  // '일정' '출시' 같은 넓은 말은 넣지 않는다. 넣으면 '축구 중계 일정' 이 다시 들어온다.
  { 카테고리: '최신이슈',
    말: ['연휴', '공휴일', '대체공휴일', '임시공휴일', '명절', '추석', '설날', '귀성', '귀경',
         '통행료', '택배마감', '휴무', '운영시간', '영업시간', '전기요금', '가스요금', '수도요금',
         '교통요금', '건강보험료', '자동차세', '재산세', '주민세', '수능', '원서접수', '분리배출'] },
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

  // 가장 길게 맞는 말을 고른다. '청년월세지원' 은 '월세'(대출)보다
  // '월세지원'(정책)에 가깝다. 길게 맞을수록 뜻이 좁고 정확하다.
  var 고른것 = null;
  for (var k = 0; k < TR분야표.length; k++) {
    var 분야 = TR분야표[k];
    for (var j = 0; j < 분야.말.length; j++) {
      var 말열쇠 = TR열쇠(분야.말[j]);
      if (열쇠.indexOf(말열쇠) !== -1 && (!고른것 || 말열쇠.length > 고른것.길이)) {
        고른것 = { 카테고리: 분야.카테고리, 말: 분야.말[j], 길이: 말열쇠.length };
      }
    }
  }
  if (고른것) {
    return { 판정: '씀', 카테고리: 고른것.카테고리,
             이유: "'" + 고른것.말 + "' — 단가가 높고 오래 검색되는 분야" };
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


// ── 두 번째 그물: 우리 분야 뉴스 ─────────────────────────
//
//  구글 트렌드 목록은 하루 스무 개 안팎이고 대부분 스포츠·연예다.
//  목록을 늘려도 축구가 늘 뿐이다. 필요한 건 '우리 분야 안의' 후보다.
//
//  새 정책·새 상품은 검색이 몰리기 전에 뉴스에 먼저 나온다.
//  청년미래적금도 그랬다. 그래서 분야별로 구글 뉴스를 훑어,
//  제목에 되풀이해 나오는 '이름 붙은 말' 을 건진다.
//
//      "청년미래적금 가입 조건은"  →  청년미래적금   (적금이 붙은 고유한 이름)
//      "근로장려금 추석 전 지급"   →  근로장려금     (장려금이 붙은 고유한 이름)
//      "가계대출 증가세"          →  버림            (거시 경제 기사 말)
//
//  그다음 구글 자동완성에 실제로 뜨는지 확인한다. 뉴스에만 나오고
//  아무도 안 치는 말은 여기서 떨어진다. 둘 다 무료다.

// 분야마다 뉴스를 훑을 말. 너무 많으면 느려진다.
var TR분야뉴스말 = {
  '대출관련': ['청년 적금', '적금 출시', '대출 금리', '전세대출', '햇살론', '서민금융', '신용점수'],
  '정책지원': ['지원금 신청', '장려금 지급', '바우처 신청', '수당 신청', '환급 신청', '연금 인상'],
  '웰빙': ['예방접종 시작', '건강검진', '질병관리청 권고'],
  '최신이슈': ['달라지는 제도', '연휴 운영', '요금 인상']
};
var TR뉴스최소건수 = 2;     // 서로 다른 기사 제목에 이만큼 나와야 후보
var TR뉴스최대후보 = 40;    // 자동완성으로 확인할 최대 개수

// 거시 경제 기사에 늘 나오는 말. 사람들이 이걸로 블로그를 찾지 않는다.
var TR흔한합성어 = ['가계대출', '기준금리', '시중금리', '대출금리', '예금금리', '대출규제',
                    '금리인하', '금리인상', '주담대', '정부지원금', '정책자금', '대출잔액',
                    '연체율', '대출수요', '금융지원'];

// 낱말 끝에 붙는 토씨. 긴 것부터 떼어 본다.
var TR토씨 = ['에서는', '으로는', '에서', '으로', '까지', '부터', '이란', '에게', '이나',
              '이며', '이고', '과', '와', '은', '는', '이', '가', '을', '를', '의', '에', '도', '만', '로', '란'];

function TR뉴스주소(말) {
  return 'https://news.google.com/rss/search?q=' + encodeURIComponent(말 + ' when:7d') +
         '&hl=ko&gl=KR&ceid=KR:ko';
}

/** 기사 제목에서 ' - 언론사' 꼬리를 뗀다 */
function TR기사제목(글) {
  var t = TR글자(글);
  var 자리 = t.lastIndexOf(' - ');
  return 자리 > 8 ? t.slice(0, 자리) : t;
}

function TR토씨떼기(낱말) {
  for (var i = 0; i < TR토씨.length; i++) {
    var 토 = TR토씨[i];
    if (낱말.length - 토.length >= 3 && 낱말.slice(-토.length) === 토) {
      return 낱말.slice(0, -토.length);
    }
  }
  return 낱말;
}

var TR모든분야말 = null;
function TR분야말목록() {
  if (TR모든분야말) return TR모든분야말;
  TR모든분야말 = [];
  TR분야표.forEach(function (분야) {
    분야.말.forEach(function (말) { TR모든분야말.push(TR열쇠(말)); });
  });
  return TR모든분야말;
}

/**
 * 기사 제목 하나에서 '이름 붙은 말' 을 뽑는다.
 * 분야 말(적금·장려금…)을 품고 있으면서 그 말보다 긴 낱말만.
 * '적금' 은 버리고 '청년미래적금' 은 남긴다.
 */
function TR이름붙은말(제목) {
  var 분야말 = TR분야말목록();
  var 흔한것 = {};
  TR흔한합성어.forEach(function (말) { 흔한것[TR열쇠(말)] = true; });

  var 나온것 = {};
  String(제목 || '').split(/[\s,·…'"“”‘’\[\]()<>《》「」『』!?:;|\/]+/).forEach(function (조각) {
    var 낱말 = TR토씨떼기(조각.replace(/^[^0-9A-Za-z가-힣]+|[^0-9A-Za-z가-힣]+$/g, ''));
    if (!/^[가-힣A-Za-z0-9-]{3,15}$/.test(낱말)) return;
    var 열쇠 = TR열쇠(낱말);
    if (흔한것[열쇠]) return;
    for (var i = 0; i < 분야말.length; i++) {
      if (열쇠.indexOf(분야말[i]) !== -1 && 열쇠.length > 분야말[i].length) {
        나온것[낱말] = true;
        return;
      }
    }
  });
  return Object.keys(나온것);
}

/**
 * 분야 뉴스에서 후보를 건진다.
 * 이미있는것 은 오늘작성·발행완료·후보에 있는 키워드 표.
 */
function TR뉴스후보(이미있는것) {
  var 머리 = (typeof 브라우저인척 !== 'undefined') ? 브라우저인척.headers : {};
  var 물을것 = [];
  Object.keys(TR분야뉴스말).forEach(function (카테고리) {
    TR분야뉴스말[카테고리].forEach(function (말) { 물을것.push({ 카테고리: 카테고리, 말: 말 }); });
  });

  var 답들 = UrlFetchApp.fetchAll(물을것.map(function (하나) {
    return { url: TR뉴스주소(하나.말), muteHttpExceptions: true, followRedirects: true, headers: 머리 };
  }));

  // 낱말 → 나온 기사들
  var 모음 = {};
  var 본제목 = {};
  답들.forEach(function (답) {
    var 몸 = '';
    try { if (답.getResponseCode() === 200) 몸 = 답.getContentText(); } catch (e) { }
    (몸.match(/<item[\s\S]*?<\/item>/g) || []).forEach(function (덩어리) {
      var 제목 = TR기사제목(TR칸(덩어리, 'title'));
      var 제목열쇠 = TR열쇠(제목);
      if (!제목 || 본제목[제목열쇠]) return;   // 같은 기사가 여러 검색에 걸려도 한 번만
      본제목[제목열쇠] = true;
      var 주소 = TR칸(덩어리, 'link');
      TR이름붙은말(제목).forEach(function (낱말) {
        var k = TR열쇠(낱말);
        var 칸 = 모음[k] || (모음[k] = { 낱말: 낱말, 기사: [] });
        칸.기사.push({ 제목: 제목, 주소: 주소 });
      });
    });
  });

  var 후보 = Object.keys(모음).map(function (k) { return 모음[k]; })
    .filter(function (칸) {
      return 칸.기사.length >= TR뉴스최소건수 && !TR비슷한게있나(TR열쇠(칸.낱말), 이미있는것 || {});
    })
    .sort(function (가, 나) { return 나.기사.length - 가.기사.length; })
    .slice(0, TR뉴스최대후보);
  if (!후보.length) return [];

  // 자동완성에 실제로 뜨는가 — 사람들이 치는 말인지 확인
  var 확인됨 = null;
  if (typeof 자동완성주소 === 'function' && typeof 자동완성읽기 === 'function') {
    var 자답 = [];
    try {
      자답 = UrlFetchApp.fetchAll(후보.map(function (칸) {
        return { url: 자동완성주소(칸.낱말), muteHttpExceptions: true, followRedirects: true, headers: 머리 };
      }));
    } catch (e) { 자답 = []; }
    var 하나라도됨 = 자답.some(function (답) {
      try { return 답.getResponseCode() === 200; } catch (e) { return false; }
    });
    if (하나라도됨) {
      확인됨 = 자답.map(function (답, i) {
        var 몸 = '';
        try { if (답.getResponseCode() === 200) 몸 = 답.getContentText(); } catch (e) { }
        var 열쇠 = TR열쇠(후보[i].낱말);
        return 자동완성읽기(몸).filter(function (제안) {
          return TR열쇠(제안).indexOf(열쇠) !== -1;
        }).length;
      });
    }
  }

  var 결과 = [];
  후보.forEach(function (칸, i) {
    var 자동수 = 확인됨 ? 확인됨[i] : null;
    if (확인됨 && 자동수 < 2) return;   // 자동완성에 거의 안 뜬다 = 아무도 안 친다
    var 대표 = 칸.기사[0];
    var 갈래 = TR가르기({ 검색어: 칸.낱말, 뉴스: 칸.기사.map(function (a) { return a.제목; }).join(' ') });
    if (갈래.판정 !== '씀') return;
    결과.push({
      검색어: 칸.낱말,
      출처: '분야 뉴스',
      검색량: '뉴스 ' + 칸.기사.length + '건' + (자동수 === null ? ' · 자동완성 확인 못 함' : ' · 자동완성 ' + 자동수 + '개'),
      카테고리: 갈래.카테고리,
      이유: '새로 뉴스에 오르는 이름 — 검색이 몰리기 전에 먼저 씁니다',
      뉴스: 대표.제목,
      뉴스주소: 대표.주소
    });
  });
  return 결과;
}


/**
 * 비슷한 키워드가 이미 있나.
 *
 * 똑같은 말만 보면 놓친다. '근로장려금 신청' 글이 이미 있는데
 * '근로장려금' 을 새로 쓰면 내 글끼리 같은 검색어를 두고 싸운다.
 * 그럴 땐 새 글이 아니라 있던 글을 보강해야 한다.
 *
 *   새 말이 있던 말 안에 들어 있다   → 있음  (근로장려금 ⊂ 근로장려금신청)
 *   있던 말이 새 말 안에 들어 있다   → 있던 말이 5자 이상일 때만 있음
 *                                       (전세대출 은 청년전세대출 을 막지 않는다)
 */
function TR비슷한게있나(열쇠, 있던것) {
  if (!열쇠) return '';
  if (있던것[열쇠]) return 열쇠;
  var 목록 = Object.keys(있던것);
  for (var i = 0; i < 목록.length; i++) {
    var 있던 = 목록[i];
    if (!있던 || 있던 === 열쇠) continue;
    if (있던.indexOf(열쇠) !== -1 && 열쇠.length >= 3) return 있던;
    if (열쇠.indexOf(있던) !== -1 && 있던.length >= 5) return 있던;
  }
  return '';
}

// ── 후보 탭 ─────────────────────────────────────────────

var TR머리 = ['받은 날', '검색어', '출처', '검색량', '카테고리', '이유', '관련 뉴스', '뉴스 주소', '오늘작성으로', '옮긴 날'];
var TR체크열 = 9, TR옮긴날열 = 10;   // 1부터 센 열 번호

function TR탭가져오기() {
  var 얻음 = (typeof SC탭얻기 === 'function')
    ? SC탭얻기(TR탭)
    : { 탭: SpreadsheetApp.getActive().getSheetByName(TR탭) || SpreadsheetApp.getActive().insertSheet(TR탭), 새로: true };
  var 탭 = 얻음.탭;
  if (얻음.새로 || 탭.getLastRow() === 0) {
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
  var 목록 = [], 트렌드실패 = '';
  try {
    목록 = TR읽기(TR받아오기());
  } catch (오류) {
    트렌드실패 = String(오류.message || 오류);
  }
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
    if (갈래.판정 === '씀' && TR비슷한게있나(TR열쇠(하나.검색어), 있던것)) {
      줄.판정 = '이미 있음';
      줄.이유 = '비슷한 키워드가 이미 있음 — 새 글 대신 그 글을 보강하세요';
    }
    줄.출처 = '구글 트렌드';
    전부.push(줄);
    if (줄.판정 === '씀') {
      새후보.push(줄);
      있던것[TR열쇠(하나.검색어)] = true;   // 같은 날 목록 안의 중복도 막는다
    }
  });

  // 두 번째 그물 — 실패해도 트렌드 결과는 그대로 남긴다
  var 뉴스후보 = [], 뉴스실패 = '';
  try {
    뉴스후보 = TR뉴스후보(있던것);
  } catch (오류) {
    뉴스실패 = String(오류.message || 오류);
  }
  뉴스후보.forEach(function (줄) {
    if (TR비슷한게있나(TR열쇠(줄.검색어), 있던것)) return;
    있던것[TR열쇠(줄.검색어)] = true;
    새후보.push(줄);
  });

  if (새후보.length) {
    var 탭 = TR탭가져오기();
    var 시작 = 탭.getLastRow() + 1;
    탭.getRange(시작, 1, 새후보.length, TR머리.length).setValues(새후보.map(function (줄) {
      return [오늘, 줄.검색어, 줄.출처, 줄.검색량, 줄.카테고리, 줄.이유, 줄.뉴스, 줄.뉴스주소, false, ''];
    }));
    try {
      var 체크 = SpreadsheetApp.newDataValidation().requireCheckbox().build();
      탭.getRange(시작, TR체크열, 새후보.length, 1).setDataValidation(체크);
    } catch (e) { }
  }

  if (트렌드실패 && 뉴스실패) {
    throw new Error('트렌드와 뉴스를 모두 받지 못했습니다.\n' + 트렌드실패 + '\n' + 뉴스실패);
  }
  return { 날짜: 오늘, 전부: 전부, 뉴스후보: 뉴스후보, 뉴스실패: 뉴스실패,
           트렌드실패: 트렌드실패, 새후보: 새후보.length };
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
    var 체크 = 줄[TR체크열 - 1] === true || String(줄[TR체크열 - 1]).toUpperCase() === 'TRUE';
    if (!체크 || 줄[TR옮긴날열 - 1]) return;   // 안 골랐거나 이미 옮김
    var 키워드 = String(줄[1] || '').trim();
    if (!키워드) return;
    var 행번호 = i + 2;

    if (!TR비슷한게있나(TR열쇠(키워드), 이미)) {
      var 새줄 = [];
      for (var c = 0; c < 열수; c++) 새줄.push('');
      새줄[열.keyword] = 키워드;
      새줄[열.category] = String(줄[4] || '').trim();
      오늘탭.getRange(오늘탭.getLastRow() + 1, 1, 1, 열수).setValues([새줄]);
      이미[TR열쇠(키워드)] = true;
      옮김.push(키워드);
    }
    탭.getRange(행번호, TR옮긴날열).setValue(오늘);
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

  var 줄 = ['① 구글 트렌드 한국 인기 검색어 (' + 결과.날짜 + ')', ''];
  var 표시 = { '씀': '✔ 씀', '이미 있음': '· 이미 있음', '안 씀': '✕ 안 씀', '건너뜀': '  건너뜀' };
  if (결과.트렌드실패) 줄.push('  받지 못했습니다 — ' + 결과.트렌드실패);
  결과.전부.forEach(function (하나) {
    줄.push((표시[하나.판정] || 하나.판정) + '  ' + 하나.검색어 +
            (하나.카테고리 ? '  → ' + 하나.카테고리 : ''));
  });
  줄.push('');
  줄.push('② 우리 분야 뉴스에서 새로 오르는 이름');
  줄.push('');
  if (결과.뉴스실패) 줄.push('  받지 못했습니다 — ' + 결과.뉴스실패);
  else if (!결과.뉴스후보.length) 줄.push('  오늘은 새로 걸린 이름이 없습니다.');
  결과.뉴스후보.forEach(function (하나) {
    줄.push('✔ 씀  ' + 하나.검색어 + '  → ' + 하나.카테고리 + '   (' + 하나.검색량 + ')');
  });
  줄.push('');
  줄.push(결과.새후보
    ? '새 후보 ' + 결과.새후보 + '개를 ' + TR탭 + ' 탭에 올렸습니다.\n쓸 것만 체크한 뒤 메뉴의 ➡️ 체크한 트렌드 오늘작성으로 를 누르세요.'
    : '오늘은 우리 분야에 걸린 새 검색어가 없습니다.');
  if (typeof 결과창 === 'function') 결과창('트렌드에서 건지기', 줄.join('\n'));
  else 화면.alert(줄.join('\n'));
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

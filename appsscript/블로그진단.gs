// ══════════════════════════════════════════════════════════
//  블로그 전체 진단 — 유지할 것 / 정리할 것
//
//  28일 성적표만으로 블로그를 정리하면 틀릴 수 있다.
//    - 1년 전엔 잘 되다 최근 떨어진 블로그와 처음부터 안 된 블로그는
//      처방이 다르다  →  28일 · 90일 · 1년 클릭을 나란히 본다
//    - 구글이 글을 '모르는' 것과 '읽고 거절한' 것은 고치는 법이 다르다
//      →  한 번도 안 나온 글을 블로그마다 20편 골라 구글에 직접 묻는다
//         (서치콘솔 URL 검사 API, 무료, 하루 2천 건까지)
//    - 클릭이 적어도 단가가 높으면 남길 이유가 있다
//      →  애드센스 3개월 수익을 적는 칸을 둔다. 다시 돌려도 지워지지 않는다
//
//  판정 기준은 아래 숫자로 정해 두었다. 판정은 제안이다. 정리는
//  회원님이 숫자를 보고 정한다.
// ══════════════════════════════════════════════════════════

var 진단탭 = 'SC_진단';
var 색인탭 = 'SC_색인검사';

// 구글애즈로 사람을 사서 보내던 블로그. 트래픽과 상관없이 계정 전체가 걸린 위험.
var 진단위험블로그 = ['sa.seaga.co.kr', '01.sa.seaga.co.kr', '02.sa.seaga.co.kr'];

var 색인표본수 = 20;           // 블로그마다 구글에 물어볼 글 수
var 진단집중클릭90 = 100;      // 90일 클릭이 이만큼이면 '집중'
var 진단집중수익 = 10000;      // 애드센스 3개월 수익이 이만큼(원)이면 '집중'
var 진단떨어짐클릭1년 = 30;    // 1년 클릭이 이만큼 있었는데 90일이 5 미만이면 '떨어짐'

var 진단수익머리 = '애드센스 3개월 수익(원, 직접 입력)';
var 진단메모머리 = '메모';


// ── 기간별 클릭 ─────────────────────────────────────────

function 진단날짜(일수) {
  var 하루 = 24 * 60 * 60 * 1000;
  var 끝 = new Date(Date.now() - SC지연일수 * 하루);
  var 시작 = new Date(끝.getTime() - (일수 - 1) * 하루);
  var 시간대 = Session.getScriptTimeZone() || 'Asia/Seoul';
  return { 시작: Utilities.formatDate(시작, 시간대, 'yyyy-MM-dd'),
           끝: Utilities.formatDate(끝, 시간대, 'yyyy-MM-dd') };
}

/** 한 기간의 블로그별 합계와, 노출된 글 목록 */
function 진단기간합계(사이트들, 일수) {
  var 날짜 = 진단날짜(일수);
  var 본글 = {}, 합 = {};
  사이트들.forEach(function (사이트) {
    SC조회(사이트, ['page'], 날짜).forEach(function (줄) {
      var 주소 = 줄.keys[0];
      if (본글[주소]) return;
      본글[주소] = true;
      var 호스트 = SC호스트(주소);
      var 칸 = 합[호스트] || (합[호스트] = { 클릭: 0, 노출: 0, 글: {} });
      칸.클릭 += 줄.clicks || 0;
      칸.노출 += 줄.impressions || 0;
      if ((줄.impressions || 0) > 0) 칸.글[진단주소열쇠(주소)] = true;
    });
  });
  return 합;
}

function 진단주소열쇠(주소) {
  return String(주소 || '').toLowerCase().replace(/^https?:\/\/(www\.)?/, '').replace(/[?#].*$/, '').replace(/\/+$/, '');
}


// ── 색인 검사 ──────────────────────────────────────────

var 진단검사주소 = 'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect';

/** 구글의 답(영어)을 우리말 갈래로 */
function 색인갈래(상태) {
  var s = String(상태 || '').toLowerCase();
  if (!s) return '답 없음';
  if (s.indexOf('submitted and indexed') !== -1 || s.indexOf('indexed, not submitted') !== -1) return '색인됨';
  if (s.indexOf('crawled - currently not indexed') !== -1) return '읽고 거절';
  if (s.indexOf('discovered - currently not indexed') !== -1) return '발견만';
  if (s.indexOf('unknown to google') !== -1) return '구글이 모름';
  if (s.indexOf('noindex') !== -1) return 'noindex 설정';
  if (s.indexOf('duplicate') !== -1 || s.indexOf('alternate') !== -1 || s.indexOf('canonical') !== -1) return '중복';
  if (s.indexOf('redirect') !== -1 || s.indexOf('404') !== -1 || s.indexOf('robots') !== -1) return '주소 문제';
  return '기타';
}

/** 뜻과 고치는 법 */
var 색인뜻 = {
  '색인됨': '목록엔 있는데 순위가 밖 — 글이 약하거나 경쟁이 셈',
  '읽고 거절': '구글이 읽고 목록에 안 올림 — 품질 문제',
  '발견만': '주소는 알지만 읽으러 오지도 않음 — 사이트 신뢰가 낮음',
  '구글이 모름': '글이 있는 줄도 모름 — 사이트맵 제출·글끼리 링크',
  'noindex 설정': '검색에 안 나오게 설정돼 있음 — 설정 확인',
  '중복': '다른 글과 같다고 봄 — 합치기',
  '주소 문제': '주소 넘김·404·차단',
  '기타': '',
  '답 없음': '',
  '검사 실패': ''
};

/** 한 블로그에서 한 번도 안 나온 글을 고르게 뽑는다 (오래된 글부터 새 글까지) */
function 색인표본(주소들, 나온글, 개수) {
  var 안나온 = 주소들.filter(function (주소) { return !나온글[진단주소열쇠(주소)]; });
  if (안나온.length <= 개수) return 안나온;
  var 뽑음 = [], 간격 = 안나온.length / 개수;
  for (var i = 0; i < 개수; i++) 뽑음.push(안나온[Math.floor(i * 간격)]);
  return 뽑음;
}

function 색인묻기(사이트, 주소들) {
  if (!주소들.length) return [];
  var 열쇠 = ScriptApp.getOAuthToken();
  var 답들 = UrlFetchApp.fetchAll(주소들.map(function (주소) {
    return {
      url: 진단검사주소, method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ inspectionUrl: 주소, siteUrl: 사이트, languageCode: 'en-US' }),
      headers: { Authorization: 'Bearer ' + 열쇠 }, muteHttpExceptions: true
    };
  }));
  return 답들.map(function (답, i) {
    var 코드 = 0, 글 = '';
    try { 코드 = 답.getResponseCode(); 글 = 답.getContentText(); } catch (e) { }
    if (코드 !== 200) {
      var 까닭 = '';
      try { 까닭 = JSON.parse(글).error.message; } catch (e) { 까닭 = String(글).slice(0, 120); }
      return { 주소: 주소들[i], 갈래: '검사 실패', 상태: 코드 + ' ' + 까닭, 마지막크롤: '' };
    }
    var 결과 = {};
    try { 결과 = JSON.parse(글).inspectionResult.indexStatusResult || {}; } catch (e) { 결과 = {}; }
    return {
      주소: 주소들[i],
      갈래: 색인갈래(결과.coverageState),
      상태: 결과.coverageState || '',
      마지막크롤: 결과.lastCrawlTime ? String(결과.lastCrawlTime).slice(0, 10) : ''
    };
  });
}


// ── 판정 ────────────────────────────────────────────────

function 진단판정(칸) {
  var 수익 = Number(String(칸.수익 || '').replace(/[^0-9.]/g, '')) || 0;
  var 색인말 = 칸.주된색인 ? ' 안 나온 글 표본: ' + 칸.주된색인 + ' — ' + (색인뜻[칸.주된색인] || '') : '';

  if (진단위험블로그.indexOf(칸.호스트) !== -1) {
    return { 순서: 0, 판정: '정리 1순위 (정책 위험)',
             이유: '구글애즈로 방문자를 사서 보내던 구조. 적발되면 애드센스 계정 전체(블로그 7개)가 막힘. 트래픽과 상관없이 정리.' };
  }
  // 서치콘솔에 없는 블로그의 0 은 '나쁨' 이 아니라 '모름' 이다
  if (!칸.사이트) {
    return { 순서: 5, 판정: '모름 — 서치콘솔 등록부터',
             이유: '서치콘솔에 등록돼 있지 않아 숫자가 없습니다. 등록하고 며칠 뒤 다시 진단.' +
                   (수익 ? ' 애드센스 수익 ' + 수익 + '원이 있으니 정리하지 마세요.' : '') };
  }
  if (칸.클릭90 >= 진단집중클릭90 || 수익 >= 진단집중수익) {
    return { 순서: 1, 판정: '집중',
             이유: '90일 클릭 ' + 칸.클릭90 + (수익 ? ' · 수익 ' + 수익 + '원' : '') + ' — 구글이 이미 보여 주는 곳. 새 글·보강을 여기에.' + 색인말 };
  }
  if (칸.클릭28 >= 1 || 칸.나온비율 >= 0.1 || 수익 > 0) {
    return { 순서: 2, 판정: '유지 — 손보기',
             이유: '조금씩 나오는 중. 새 글보다 SC_할일의 글 보강부터.' + 색인말 };
  }
  if (칸.클릭365 >= 진단떨어짐클릭1년 && 칸.클릭90 < 5) {
    return { 순서: 3, 판정: '떨어짐 — 원인 확인',
             이유: '1년 클릭 ' + 칸.클릭365 + '인데 최근 90일 ' + 칸.클릭90 + '. 구글 업데이트 영향일 수 있음. 정리 전에 원인부터.' + 색인말 };
  }
  return { 순서: 4, 판정: '새 글 중단',
           이유: '1년 클릭 ' + 칸.클릭365 + ' — 글을 더 써도 읽히지 않음. 이미 쓴 글은 두고, 정리 방법은 색인 결과로 정함.' + 색인말 };
}


// ── 본체 ────────────────────────────────────────────────

function 블로그진단() {
  var 시작시각 = Date.now();
  var 사이트들 = SC사이트목록();
  if (!사이트들.length) throw new Error('이 계정으로 볼 수 있는 서치콘솔 속성이 없습니다.');

  var 합28 = 진단기간합계(사이트들, 28);
  var 합90 = 진단기간합계(사이트들, 90);
  var 합365 = 진단기간합계(사이트들, 365);

  // 대상 블로그 = 서치콘솔에 숫자가 있는 곳 + 카테고리표의 내 블로그
  var 호스트표 = {};
  [합28, 합90, 합365].forEach(function (합) { Object.keys(합).forEach(function (h) { 호스트표[h] = true; }); });
  SC내블로그().forEach(function (h) { 호스트표[h] = true; });

  var 분야표 = {};
  if (typeof 카테고리표 !== 'undefined') {
    Object.keys(카테고리표).forEach(function (이름) {
      ['워드프레스', '티스토리'].forEach(function (곳) {
        var h = 카테고리표[이름][곳];
        if (h) { h = String(h).toLowerCase(); (분야표[h] = 분야표[h] || []).push(이름); }
      });
    });
  }

  var 있던입력 = 진단입력읽기();
  var 색인줄 = [];
  var 결과 = [];

  Object.keys(호스트표).forEach(function (호스트) {
    var 사이트 = SC속성고르기(사이트들, 호스트);
    var a = 합28[호스트] || { 클릭: 0, 노출: 0, 글: {} };
    var b = 합90[호스트] || { 클릭: 0, 노출: 0, 글: {} };
    var c = 합365[호스트] || { 클릭: 0, 노출: 0, 글: {} };
    var 주소들 = SC사이트맵주소들(호스트);
    var 전체 = 주소들.length;
    var 나온28 = Object.keys(a.글).length;

    var 칸 = {
      호스트: 호스트, 사이트: 사이트,
      분야: (분야표[호스트] || []).join(', '),
      전체글: 전체 || '',
      나온28: 나온28,
      나온비율: 전체 ? 나온28 / 전체 : 0,
      클릭28: a.클릭, 클릭90: b.클릭, 클릭365: c.클릭, 노출365: c.노출,
      수익: (있던입력[호스트] || {}).수익 || '',
      메모: (있던입력[호스트] || {}).메모 || '',
      색인요약: '', 주된색인: ''
    };

    // 색인 검사 — 1년 동안 나온 적 없는 글에서 뽑는다. 시간이 모자라면 건너뛴다.
    if (!사이트) {
      칸.색인요약 = '서치콘솔에 없음';
    } else if (!전체) {
      칸.색인요약 = '사이트맵을 못 읽음';
    } else if (Date.now() - 시작시각 > 4 * 60 * 1000) {
      칸.색인요약 = '시간 부족 — 다시 누르기';
    } else {
      var 표본 = 색인표본(주소들, c.글, 색인표본수);
      // 표본 주소(https 등)에 실제로 맞는 속성으로 묻는다
      var 검사속성 = SC속성고르기(사이트들, 호스트, 표본[0]) || 사이트;
      var 답 = 색인묻기(검사속성, 표본);
      var 셈 = {};
      답.forEach(function (d) {
        셈[d.갈래] = (셈[d.갈래] || 0) + 1;
        색인줄.push([호스트, d.주소, d.갈래, 색인뜻[d.갈래] || '', d.상태, d.마지막크롤]);
      });
      var 갈래들 = Object.keys(셈).sort(function (x, y) { return 셈[y] - 셈[x]; });
      칸.색인요약 = 답.length
        ? 갈래들.map(function (k) { return k + ' ' + 셈[k]; }).join(' · ') + ' (' + 답.length + '편 중)'
        : '안 나온 글 없음';
      칸.주된색인 = 갈래들.filter(function (k) { return k !== '검사 실패'; })[0] || '';
    }

    var 판 = 진단판정(칸);
    칸.판정 = 판.판정; 칸.이유 = 판.이유; 칸.순서 = 판.순서;
    결과.push(칸);
  });

  결과.sort(function (x, y) { return x.순서 - y.순서 || y.클릭90 - x.클릭90 || y.노출365 - x.노출365; });
  진단탭쓰기(결과);
  SC탭쓰기(색인탭, ['블로그', '글 주소', '구글의 답', '뜻', '원문', '마지막으로 읽은 날'], 색인줄);
  return 결과;
}

/** 회원님이 적은 수익·메모를 다시 돌려도 지우지 않는다 */
function 진단입력읽기() {
  var 탭 = SpreadsheetApp.getActive().getSheetByName(진단탭);
  var 담긴것 = {};
  if (!탭 || 탭.getLastRow() < 2) return 담긴것;
  var 값 = 탭.getRange(1, 1, 탭.getLastRow(), 탭.getLastColumn()).getValues();
  var 머리 = 값[0].map(String);
  var 수익열 = 머리.indexOf(진단수익머리), 메모열 = 머리.indexOf(진단메모머리);
  값.slice(1).forEach(function (줄) {
    var h = String(줄[0] || '').trim().toLowerCase();
    if (!h) return;
    담긴것[h] = { 수익: 수익열 >= 0 ? 줄[수익열] : '', 메모: 메모열 >= 0 ? 줄[메모열] : '' };
  });
  return 담긴것;
}

function 진단탭쓰기(결과) {
  SC탭쓰기(진단탭,
    ['블로그', '분야', '판정', '이유', '전체 글', '검색에 나온 글(28일)',
     '클릭 28일', '클릭 90일', '클릭 1년', '노출 1년', '안 나온 글 색인 검사',
     진단수익머리, 진단메모머리],
    결과.map(function (칸) {
      return [칸.호스트, 칸.분야, 칸.판정, 칸.이유, 칸.전체글,
              칸.전체글 ? 칸.나온28 + ' (' + SC퍼센트(칸.나온비율) + ')' : 칸.나온28,
              칸.클릭28, 칸.클릭90, 칸.클릭365, 칸.노출365, 칸.색인요약,
              칸.수익, 칸.메모];
    }));
}


// ── 메뉴 ────────────────────────────────────────────────

function 블로그진단메뉴() {
  var 화면 = SpreadsheetApp.getUi();
  var 결과;
  try {
    결과 = 블로그진단();
  } catch (오류) {
    화면.alert(typeof SC오류설명 === 'function' ? SC오류설명(오류) : String(오류));
    return;
  }
  var 묶음 = {};
  결과.forEach(function (칸) { (묶음[칸.판정] = 묶음[칸.판정] || []).push(칸); });
  var 줄 = ['블로그 전체 진단 — ' + 결과.length + '곳', ''];
  Object.keys(묶음).forEach(function (판정) {
    줄.push('■ ' + 판정 + ' (' + 묶음[판정].length + ')');
    묶음[판정].forEach(function (칸) {
      줄.push('   ' + 칸.호스트 + '  · 클릭 1년 ' + 칸.클릭365 + ' · 90일 ' + 칸.클릭90 +
              (칸.색인요약 ? '\n      ' + 칸.색인요약 : ''));
    });
    줄.push('');
  });
  줄.push('자세한 이유는 ' + 진단탭 + ' 탭, 글마다 구글의 답은 ' + 색인탭 + ' 탭에 있습니다.');
  줄.push("'" + 진단수익머리 + "' 칸에 애드센스 수익을 적고 다시 누르면 판정에 반영됩니다.");
  화면.alert(줄.join('\n'));
}

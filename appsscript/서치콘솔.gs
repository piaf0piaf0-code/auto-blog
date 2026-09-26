// ══════════════════════════════════════════════════════════
//  서치콘솔 성적표
//
//  왜 만들었나
//    지금까지 어느 글이 검색에 나오는지, 몇 위인지, 돈이 되는지를
//    시스템이 전혀 몰랐다. 눈을 감고 글을 쓰고 있었다.
//    수익은 '검색에 나오는 글' 에서만 생긴다. 안 나오는 글은 0원이다.
//    그래서 모든 판단의 출발점을 여기에 둔다.
//
//  하는 일
//    ① 서치콘솔에 등록된 블로그를 스스로 찾는다
//    ② 최근 28일 성적을 받아 탭 다섯 개에 정리한다
//         SC_블로그별   블로그마다 한 줄 — 클릭·노출·순위·안 나온 글 비율
//         SC_할일       돈 되는 순서로 손볼 글 — 순위 보강 / 제목 개선
//         SC_싸우는글   같은 검색어를 두고 내 글끼리 싸우는 곳
//         SC_글별       글 하나하나의 성적
//         SC_기록       날마다 쌓이는 블로그별 합계 (추세 보기용)
//    ③ 서치콘솔에 등록 안 된 내 블로그를 알려 준다
//
//  비용 0원. 서치콘솔 API 는 무료다.
//  시트 주인 계정으로 읽으므로 서비스 계정 설정이 필요 없다.
//  대신 appsscript.json 에 권한 한 줄(webmasters.readonly)이 있어야 한다.
// ══════════════════════════════════════════════════════════

var SC탭 = {
  블로그: 'SC_블로그별',
  할일: 'SC_할일',
  싸움: 'SC_싸우는글',
  글별: 'SC_글별',
  기록: 'SC_기록'
};

var SC기간일수 = 28;      // 최근 며칠을 볼지
var SC지연일수 = 3;       // 서치콘솔 숫자는 2~3일 늦게 확정된다

// 순위 보강 — 1페이지 끝~2페이지. 조금만 올리면 클릭이 몇 배로 뛴다.
var SC보강순위 = [8, 20];
var SC보강최소노출 = 20;  // 글 하나의 해당 검색어 노출 합이 이 이상일 때만

// 제목 개선 — 순위는 좋은데 아무도 안 누르는 글
var SC제목최대순위 = 7;
var SC제목최소노출 = 50;
var SC제목최대CTR = 0.02;

// 싸우는 글 — 같은 검색어에 내 글이 둘 이상 나올 때
var SC싸움최소노출 = 5;

var SC주소 = 'https://www.googleapis.com/webmasters/v3/';


// ── 서치콘솔에 묻기 ─────────────────────────────────────

function SC요청(길, 몸통) {
  var 옵션 = {
    muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }
  };
  if (몸통) {
    옵션.method = 'post';
    옵션.contentType = 'application/json';
    옵션.payload = JSON.stringify(몸통);
  }
  var 답 = UrlFetchApp.fetch(SC주소 + 길, 옵션);
  var 코드 = 답.getResponseCode();
  var 글 = 답.getContentText();
  if (코드 !== 200) {
    var 까닭 = '';
    try { 까닭 = JSON.parse(글).error.message; } catch (e) { 까닭 = String(글).slice(0, 200); }
    var 오류 = new Error('서치콘솔 응답 ' + 코드 + ': ' + 까닭);
    오류.코드 = 코드;
    throw 오류;
  }
  return JSON.parse(글);
}

/** 이 계정이 데이터를 볼 수 있는 서치콘솔 속성 목록 */
function SC사이트목록() {
  var 답 = SC요청('sites');
  return (답.siteEntry || [])
    .filter(function (하나) { return 하나.permissionLevel !== 'siteUnverifiedUser'; })
    .map(function (하나) { return 하나.siteUrl; });
}

function SC날짜() {
  var 하루 = 24 * 60 * 60 * 1000;
  var 끝 = new Date(Date.now() - SC지연일수 * 하루);
  var 시작 = new Date(끝.getTime() - (SC기간일수 - 1) * 하루);
  var 시간대 = Session.getScriptTimeZone() || 'Asia/Seoul';
  return {
    시작: Utilities.formatDate(시작, 시간대, 'yyyy-MM-dd'),
    끝: Utilities.formatDate(끝, 시간대, 'yyyy-MM-dd')
  };
}

/** 검색 성과 조회. 2만5천 줄이 넘으면 이어서 받는다. */
function SC조회(사이트, 차원, 날짜) {
  var 모음 = [];
  for (var 쪽 = 0; 쪽 < 4; 쪽++) {
    var 답 = SC요청('sites/' + encodeURIComponent(사이트) + '/searchAnalytics/query', {
      startDate: 날짜.시작,
      endDate: 날짜.끝,
      dimensions: 차원,
      type: 'web',
      rowLimit: 25000,
      startRow: 쪽 * 25000
    });
    var 받은것 = 답.rows || [];
    모음 = 모음.concat(받은것);
    if (받은것.length < 25000) break;
  }
  return 모음;
}


// ── 주소 다루기 ─────────────────────────────────────────

function SC호스트(주소) {
  var m = /^https?:\/\/([^\/?#]+)/i.exec(String(주소 || ''));
  return m ? m[1].toLowerCase().replace(/^www\./, '') : '';
}

/** 이 서치콘솔 속성이 이 블로그를 덮는가 */
function SC덮나(사이트, 호스트) {
  사이트 = String(사이트 || '');
  if (사이트.indexOf('sc-domain:') === 0) {
    var 도메인 = 사이트.slice(10).toLowerCase().replace(/^www\./, '');
    return 호스트 === 도메인 ||
           호스트.slice(-(도메인.length + 1)) === '.' + 도메인;
  }
  return SC호스트(사이트) === 호스트;
}

/**
 * 이 블로그에 쓸 서치콘솔 속성 하나를 고른다.
 *
 * 한 블로그를 속성 여러 개가 덮을 수 있다(도메인 속성 + 주소 속성,
 * http 와 https). 주소 속성은 앞머리까지 맞아야 쓸 수 있다.
 * 'http://seaga.seaga.co.kr/' 로는 https 글을 검사할 수 없다.
 * 그래서 ① 도메인 속성 ② https 주소 속성 ③ 나머지 순으로 고른다.
 * 예전엔 호스트만 비교해 목록의 첫 번째를 썼고, 한 블로그의 색인
 * 검사가 20편 모두 실패했다.
 */
function SC속성고르기(사이트들, 호스트, 표본주소) {
  var 맞는것 = 사이트들.filter(function (s) { return SC덮나(s, 호스트); });
  if (표본주소) {
    맞는것 = 맞는것.filter(function (s) {
      return s.indexOf('sc-domain:') === 0 ||
             String(표본주소).toLowerCase().replace('://www.', '://').indexOf(s.toLowerCase().replace('://www.', '://')) === 0;
    });
  }
  function 점수(s) {
    if (s.indexOf('sc-domain:') === 0) return 0;
    if (/^https:\/\//i.test(s)) return 1;
    return 2;
  }
  맞는것.sort(function (가, 나) { return 점수(가) - 점수(나); });
  return 맞는것[0] || '';
}

/** 카테고리표에 적힌 내 블로그 주소들 */
function SC내블로그() {
  var 모음 = {};
  if (typeof 카테고리표 !== 'undefined') {
    Object.keys(카테고리표).forEach(function (이름) {
      ['워드프레스', '티스토리'].forEach(function (곳) {
        var 주소 = 카테고리표[이름][곳];
        if (주소) 모음[String(주소).toLowerCase().replace(/^www\./, '')] = true;
      });
    });
  }
  return Object.keys(모음);
}


// ── 사이트맵으로 전체 글 수 세기 ─────────────────────────
//
//  '노출된 글 수' 만 보면 반쪽이다. 전체 글이 몇 편인지 알아야
//  '한 번도 검색에 안 나온 글' 이 얼마나 되는지 보인다.
//  이 비율이 높으면 새 글을 더 쓰는 건 헛수고다. 색인·품질부터 봐야 한다.

function SC사이트맵글수(호스트) {
  var 티스토리 = /tistory\.com$/.test(호스트);
  var 후보 = 티스토리
    ? ['/sitemap.xml']
    : ['/sitemap_index.xml', '/wp-sitemap.xml', '/sitemap.xml'];
  for (var i = 0; i < 후보.length; i++) {
    var 수 = SC사이트맵세기('https://' + 호스트 + 후보[i], 티스토리, 0);
    if (수 > 0) return 수;
  }
  return '';
}

function SC사이트맵세기(주소, 티스토리, 깊이, 모음) {
  if (깊이 > 2) return 0;
  var 원문 = '';
  try {
    var 머리 = (typeof 브라우저인척 !== 'undefined') ? 브라우저인척.headers : {};
    var 답 = UrlFetchApp.fetch(주소, { muteHttpExceptions: true, followRedirects: true, headers: 머리 });
    if (답.getResponseCode() === 200) 원문 = 답.getContentText();
  } catch (e) { 원문 = ''; }
  if (!원문) return 0;

  var 주소들 = [], 찾기 = /<loc>\s*([^<\s]+)\s*<\/loc>/g, m;
  while ((m = 찾기.exec(원문)) !== null) 주소들.push(m[1]);

  if (원문.indexOf('<sitemapindex') !== -1) {
    var 합 = 0;
    주소들.forEach(function (자식) {
      // 워드프레스는 글 사이트맵만 센다. 페이지·카테고리·태그·작성자는 뺀다.
      if (!티스토리 && !/post-sitemap|posts-post/i.test(자식)) return;
      합 += SC사이트맵세기(자식, 티스토리, 깊이 + 1, 모음);
    });
    return 합;
  }

  var 글주소 = 티스토리
    ? 주소들.filter(function (하나) { return /\/\d+\/?$/.test(하나) || /\/entry\//.test(하나); })
    : 주소들;
  if (모음) 글주소.forEach(function (하나) { 모음.push(하나); });
  return 글주소.length;
}

/** 사이트맵의 글 주소 목록 (색인 검사 표본을 뽑을 때 쓴다) */
function SC사이트맵주소들(호스트) {
  var 티스토리 = /tistory\.com$/.test(호스트);
  var 후보 = 티스토리
    ? ['/sitemap.xml']
    : ['/sitemap_index.xml', '/wp-sitemap.xml', '/sitemap.xml'];
  for (var i = 0; i < 후보.length; i++) {
    var 모음 = [];
    if (SC사이트맵세기('https://' + 호스트 + 후보[i], 티스토리, 0, 모음) > 0) return 모음;
  }
  return [];
}


// ── 본체 ────────────────────────────────────────────────

/**
 * 서치콘솔 성적을 받아 탭 다섯 개에 쓴다.
 * 매일 자동 실행에서도 부르므로 여기서는 화면(알림창)을 쓰지 않는다.
 */
function 서치콘솔받기() {
  // 메뉴를 연달아 누르거나 매일 7시 실행과 겹치면 두 실행이 같은 탭을
  // 동시에 만들다 부딪힌다('SC_글별 시트가 이미 있습니다'). 실제로 그랬다.
  var 잠금 = null;
  try { 잠금 = LockService.getScriptLock(); } catch (e) { 잠금 = null; }
  if (잠금 && !잠금.tryLock(2000)) {
    throw new Error('이미 성적표를 받는 중입니다. 1분쯤 뒤 끝나면 결과가 보입니다.');
  }
  try {
    return SC받기본체();
  } finally {
    if (잠금) { try { 잠금.releaseLock(); } catch (e) { } }
  }
}

function SC받기본체() {
  var 날짜 = SC날짜();
  var 사이트들 = SC사이트목록();
  var 실패 = [];

  var 글별 = {};        // 글 주소 → 성적
  var 질의 = {};        // '주소\t검색어' → 성적 (속성이 겹쳐도 한 번만)

  사이트들.forEach(function (사이트) {
    try {
      SC조회(사이트, ['page'], 날짜).forEach(function (줄) {
        var 주소 = 줄.keys[0];
        if (글별[주소]) return;   // 도메인 속성과 주소 속성이 같은 글을 둘 다 덮을 때
        글별[주소] = {
          호스트: SC호스트(주소), 주소: 주소,
          클릭: 줄.clicks || 0, 노출: 줄.impressions || 0, 순위: 줄.position || 0,
          대표검색어: '', 대표노출: 0
        };
      });
      SC조회(사이트, ['page', 'query'], 날짜).forEach(function (줄) {
        var 열쇠 = 줄.keys[0] + '\t' + 줄.keys[1];
        if (질의[열쇠]) return;
        질의[열쇠] = {
          호스트: SC호스트(줄.keys[0]), 주소: 줄.keys[0], 검색어: 줄.keys[1],
          클릭: 줄.clicks || 0, 노출: 줄.impressions || 0,
          ctr: 줄.ctr || 0, 순위: 줄.position || 0
        };
      });
    } catch (오류) {
      실패.push(사이트 + ' — ' + 오류.message);
    }
  });

  var 질의줄 = Object.keys(질의).map(function (k) { return 질의[k]; });

  // 글마다 가장 많이 나온 검색어
  질의줄.forEach(function (q) {
    var 글 = 글별[q.주소];
    if (글 && q.노출 > 글.대표노출) { 글.대표노출 = q.노출; 글.대표검색어 = q.검색어; }
  });

  var 블로그 = SC블로그별(글별, 사이트들);
  var 싸움 = SC싸움목록(질의줄);
  var 할일 = SC할일목록(글별, 질의줄, SC약한글표(싸움));

  SC블로그탭쓰기(블로그, 날짜);
  SC할일탭쓰기(할일);
  SC싸움탭쓰기(싸움);
  SC글별탭쓰기(글별);
  SC기록남기기(블로그, 날짜);

  return {
    날짜: 날짜, 사이트수: 사이트들.length, 실패: 실패,
    블로그: 블로그, 할일수: 할일.length, 싸움수: 싸움.length,
    글수: Object.keys(글별).length
  };
}


// ── 블로그별 요약 ───────────────────────────────────────

function SC블로그별(글별, 사이트들) {
  var 모음 = {};
  function 칸(호스트) {
    return 모음[호스트] || (모음[호스트] = {
      호스트: 호스트, 클릭: 0, 노출: 0, 순위곱: 0, 노출글: 0,
      등록: 사이트들.some(function (s) { return SC덮나(s, 호스트); })
    });
  }

  Object.keys(글별).forEach(function (주소) {
    var 글 = 글별[주소], 칸하나 = 칸(글.호스트);
    칸하나.클릭 += 글.클릭;
    칸하나.노출 += 글.노출;
    칸하나.순위곱 += 글.순위 * 글.노출;
    if (글.노출 > 0) 칸하나.노출글++;
  });
  // 서치콘솔에 숫자가 없어도 내 블로그는 줄을 만든다 (빠진 걸 보여 주려고)
  SC내블로그().forEach(function (호스트) { 칸(호스트); });

  return Object.keys(모음).map(function (호스트) {
    var 칸하나 = 모음[호스트];
    칸하나.평균순위 = 칸하나.노출 ? 칸하나.순위곱 / 칸하나.노출 : 0;
    칸하나.전체글 = SC사이트맵글수(호스트);
    칸하나.안나온비율 = (칸하나.전체글 && 칸하나.전체글 > 0)
      ? Math.max(0, 1 - 칸하나.노출글 / 칸하나.전체글) : '';
    칸하나.진단 = SC한줄진단(칸하나);
    return 칸하나;
  }).sort(function (가, 나) { return 나.클릭 - 가.클릭 || 나.노출 - 가.노출; });
}

function SC한줄진단(칸) {
  if (!칸.등록) return '서치콘솔에 등록 안 됨 — 등록부터 하세요. 성적을 알 수 없습니다.';
  if (!칸.노출) return '28일 동안 검색 노출 0 — 색인이 안 됐거나 막혀 있습니다. 새 글보다 이것부터.';
  if (칸.안나온비율 !== '' && 칸.안나온비율 >= 0.5) {
    return '글의 ' + Math.round(칸.안나온비율 * 100) + '%가 검색에 한 번도 안 나옴 — 새 글보다 색인·품질 점검이 먼저.';
  }
  if (칸.평균순위 > 20) return '대부분 2페이지 밖 — SC_할일 의 순위 보강부터.';
  return '돌아가는 중 — SC_할일 의 글부터 손보면 가장 빨리 늡니다.';
}


// ── 손볼 글: 순위 보강 · 제목 개선 ─────────────────────

function SC할일목록(글별, 질의줄, 약한글) {
  var 할일 = [];
  약한글 = 약한글 || {};

  // ① 순위 보강 — 8~20위에 걸린 검색어를 글 단위로 묶는다
  var 글마다 = {};
  질의줄.forEach(function (q) {
    if (q.순위 < SC보강순위[0] || q.순위 > SC보강순위[1]) return;
    if (q.노출 < 5) return;
    (글마다[q.주소] = 글마다[q.주소] || []).push(q);
  });
  Object.keys(글마다).forEach(function (주소) {
    var 목록 = 글마다[주소].sort(function (가, 나) { return 나.노출 - 가.노출; });
    var 노출 = 0, 클릭 = 0, 순위곱 = 0;
    목록.forEach(function (q) { 노출 += q.노출; 클릭 += q.클릭; 순위곱 += q.순위 * q.노출; });
    if (노출 < SC보강최소노출) return;

    // 같은 검색어로 내 다른 글과 싸우는 중인 약한 쪽은 보강하면 안 된다.
    // 보강할수록 센 글과 더 치열하게 싸운다. 합치는 게 답이다.
    if (약한글[주소]) {
      할일.push({
        종류: '합치기', 호스트: SC호스트(주소), 주소: 주소,
        노출: 노출, 클릭: 클릭, 순위: 순위곱 / 노출,
        검색어: 약한글[주소].검색어,
        할것: '같은 검색어로 ' + 약한글[주소].센글 + ' 과 싸우는 중입니다. ' +
              '이 글을 보강하지 말고 그 글에 내용을 합치세요. ' + SC합치는법(SC호스트(주소))
      });
      return;
    }

    할일.push({
      종류: '순위 보강', 호스트: SC호스트(주소), 주소: 주소,
      노출: 노출, 클릭: 클릭, 순위: 순위곱 / 노출,
      검색어: 목록.slice(0, 5).map(function (q) {
        return q.검색어 + ' (' + q.순위.toFixed(1) + '위)';
      }).join(' / '),
      할것: '이 검색어들에 답하는 내용을 보강하면 1페이지 위쪽으로 올라갈 수 있습니다.'
    });
  });

  // ② 제목 개선 — 순위는 좋은데 아무도 안 누른다
  Object.keys(글별).forEach(function (주소) {
    var 글 = 글별[주소];
    if (글.순위 > SC제목최대순위 || 글.노출 < SC제목최소노출) return;
    var ctr = 글.노출 ? 글.클릭 / 글.노출 : 0;
    if (ctr >= SC제목최대CTR) return;
    할일.push({
      종류: '제목 개선', 호스트: 글.호스트, 주소: 주소,
      노출: 글.노출, 클릭: 글.클릭, 순위: 글.순위,
      검색어: 글.대표검색어,
      할것: '순위는 ' + 글.순위.toFixed(1) + '위인데 누르는 사람이 ' +
            (ctr * 100).toFixed(1) + '%뿐입니다. 제목과 메타 설명을 바꾸면 됩니다.'
    });
  });

  // 노출이 큰 것이 돈이 큰 것이다
  return 할일.sort(function (가, 나) { return 나.노출 - 가.노출; }).slice(0, 300);
}


// ── 내 글끼리 싸우는 검색어 ─────────────────────────────

function SC싸움목록(질의줄) {
  var 묶음 = {};
  질의줄.forEach(function (q) {
    if (q.노출 < SC싸움최소노출) return;
    var 열쇠 = q.호스트 + '\t' + q.검색어;
    (묶음[열쇠] = 묶음[열쇠] || []).push(q);
  });

  var 싸움 = [];
  Object.keys(묶음).forEach(function (열쇠) {
    var 목록 = 묶음[열쇠];
    if (목록.length < 2) return;
    목록.sort(function (가, 나) { return 나.노출 - 가.노출; });
    var 합 = 0;
    목록.forEach(function (q) { 합 += q.노출; });
    싸움.push({
      호스트: 목록[0].호스트, 검색어: 목록[0].검색어,
      글수: 목록.length, 노출: 합,
      주소들: 목록.map(function (q) { return q.주소; }),
      글들: 목록.map(function (q) {
        return q.주소 + '  (' + q.순위.toFixed(1) + '위, 노출 ' + q.노출 + ')';
      }).join('\n')
    });
  });
  return 싸움.sort(function (가, 나) { return 나.노출 - 가.노출; }).slice(0, 200);
}


/**
 * 합친 뒤 약한 글을 어떻게 처리하는지. 플랫폼마다 다르다.
 *
 * 워드프레스는 주소 넘기기(301)가 된다. 랭크매스 무료판의 '리디렉션'
 * 기능으로 할 수 있다. 쌓인 순위가 센 글로 옮겨 간다.
 * 티스토리는 글 하나씩 주소를 넘기는 기능이 없다. 그래서 비공개로
 * 돌리고, 그 글로 가던 내 링크를 센 글로 바꾸는 것이 할 수 있는 최선이다.
 */
function SC합치는법(호스트) {
  if (/tistory\.com$/.test(String(호스트 || ''))) {
    return '티스토리는 주소 넘기기가 안 되므로, 약한 글은 비공개로 돌리고 그 글로 가던 내 링크를 센 글로 바꾸세요.';
  }
  return '약한 글 주소는 센 글로 넘기세요(랭크매스 → 리디렉션 → 301).';
}

/**
 * 싸움에서 진 쪽 글 → 이긴 쪽 글.
 * 노출이 가장 큰 글을 '센 글' 로 본다. 노출이 곧 구글이 고른 쪽이다.
 */
function SC약한글표(싸움) {
  var 표 = {};
  싸움.forEach(function (s) {
    var 센글 = s.주소들[0];
    s.주소들.slice(1).forEach(function (주소) {
      if (!표[주소]) 표[주소] = { 센글: 센글, 검색어: s.검색어 };
    });
  });
  return 표;
}


// ── 탭 쓰기 ─────────────────────────────────────────────

/** 탭을 가져오거나 만든다. 그 사이 다른 실행이 만들었으면 그것을 쓴다. */
function SC탭얻기(이름) {
  var 문서 = SpreadsheetApp.getActive();
  var 탭 = 문서.getSheetByName(이름);
  if (탭) return { 탭: 탭, 새로: false };
  try {
    return { 탭: 문서.insertSheet(이름), 새로: true };
  } catch (오류) {
    탭 = 문서.getSheetByName(이름);
    if (탭) return { 탭: 탭, 새로: false };
    throw 오류;
  }
}

function SC탭쓰기(이름, 머리, 줄들) {
  var 탭 = SC탭얻기(이름).탭;
  탭.clear();
  var 모두 = [머리].concat(줄들.map(function (줄) {
    var 한줄 = 줄.slice(0, 머리.length);
    while (한줄.length < 머리.length) 한줄.push('');
    return 한줄;
  }));
  탭.getRange(1, 1, 모두.length, 머리.length).setValues(모두);
  탭.getRange(1, 1, 1, 머리.length).setFontWeight('bold').setBackground('#e8eaed');
  탭.setFrozenRows(1);
  return 탭;
}

function SC퍼센트(값) {
  return (값 === '' || 값 === null || 값 === undefined) ? '' : Math.round(값 * 1000) / 10 + '%';
}

function SC블로그탭쓰기(블로그, 날짜) {
  var 탭 = SC탭쓰기(SC탭.블로그,
    ['블로그', '서치콘솔', '클릭', '노출', 'CTR', '평균순위',
     '노출된 글', '전체 글(사이트맵)', '한 번도 안 나온 글', '한 줄 진단'],
    블로그.map(function (칸) {
      return [
        칸.호스트, 칸.등록 ? '등록됨' : '없음',
        칸.클릭, 칸.노출,
        칸.노출 ? SC퍼센트(칸.클릭 / 칸.노출) : '',
        칸.노출 ? Math.round(칸.평균순위 * 10) / 10 : '',
        칸.노출글, 칸.전체글, SC퍼센트(칸.안나온비율), 칸.진단
      ];
    }));
  try { 탭.getRange(1, 1).setNote('기간: ' + 날짜.시작 + ' ~ ' + 날짜.끝 + ' (' + SC기간일수 + '일)'); } catch (e) { }
}

function SC할일탭쓰기(할일) {
  SC탭쓰기(SC탭.할일,
    ['종류', '블로그', '글 주소', '노출', '클릭', '평균순위', '걸린 검색어', '할 것', '처리함'],
    할일.map(function (h) {
      return [h.종류, h.호스트, h.주소, h.노출, h.클릭,
              Math.round(h.순위 * 10) / 10, h.검색어, h.할것, ''];
    }));
}

function SC싸움탭쓰기(싸움) {
  SC탭쓰기(SC탭.싸움,
    ['블로그', '검색어', '싸우는 글 수', '노출 합', '글 (순위, 노출)', '할 것'],
    싸움.map(function (s) {
      return [s.호스트, s.검색어, s.글수, s.노출, s.글들,
              '맨 위(노출이 가장 큰) 글 하나로 합치세요. ' + SC합치는법(s.호스트) +
              ' 이 검색어로 새 글을 또 쓰지 마세요.'];
    }));
}

function SC글별탭쓰기(글별) {
  var 줄들 = Object.keys(글별).map(function (k) { return 글별[k]; })
    .sort(function (가, 나) { return 나.클릭 - 가.클릭 || 나.노출 - 가.노출; })
    .map(function (글) {
      return [글.호스트, 글.주소, 글.클릭, 글.노출,
              글.노출 ? SC퍼센트(글.클릭 / 글.노출) : '',
              Math.round(글.순위 * 10) / 10, 글.대표검색어];
    });
  SC탭쓰기(SC탭.글별,
    ['블로그', '글 주소', '클릭', '노출', 'CTR', '평균순위', '가장 많이 나온 검색어'], 줄들);
}

/** 날마다 한 줄씩 쌓는다. 같은 날짜·블로그는 두 번 적지 않는다. */
function SC기록남기기(블로그, 날짜) {
  var 얻음 = SC탭얻기(SC탭.기록);
  var 탭 = 얻음.탭;
  var 머리 = ['기준일', '블로그', '클릭', '노출', '평균순위', '노출된 글', '전체 글'];
  if (얻음.새로 || 탭.getLastRow() === 0) {
    탭.getRange(1, 1, 1, 머리.length).setValues([머리]);
    탭.getRange(1, 1, 1, 머리.length).setFontWeight('bold').setBackground('#e8eaed');
    탭.setFrozenRows(1);
  }
  var 있던것 = {};
  var 마지막 = 탭.getLastRow();
  if (마지막 >= 2) {
    탭.getRange(2, 1, 마지막 - 1, 2).getValues().forEach(function (줄) {
      있던것[String(줄[0]) + '\t' + String(줄[1])] = true;
    });
  }
  var 새줄 = 블로그.filter(function (칸) {
    return 칸.등록 && !있던것[날짜.끝 + '\t' + 칸.호스트];
  }).map(function (칸) {
    return [날짜.끝, 칸.호스트, 칸.클릭, 칸.노출,
            칸.노출 ? Math.round(칸.평균순위 * 10) / 10 : '', 칸.노출글, 칸.전체글];
  });
  if (새줄.length) {
    탭.getRange(탭.getLastRow() + 1, 1, 새줄.length, 머리.length).setValues(새줄);
  }
}


// ── 메뉴 ────────────────────────────────────────────────

function 서치콘솔받기메뉴() {
  var 화면 = SpreadsheetApp.getUi();
  var 결과;
  try {
    결과 = 서치콘솔받기();
  } catch (오류) {
    화면.alert(SC오류설명(오류));
    return;
  }
  화면.alert(SC요약글(결과));
}

function SC오류설명(오류) {
  var 말 = String(오류 && 오류.message || 오류);

  // API 가 꺼져 있는 경우. 이것도 403 으로 오지만 권한 문제가 아니다.
  // 권한 안내를 보여 주면 엉뚱한 곳을 고치게 된다(실제로 그랬다).
  if (/has not been used in project|is disabled|SERVICE_DISABLED|accessNotConfigured/i.test(말)) {
    var 주소 = (/https:\/\/console\.(?:developers|cloud)\.google\.com\/[^\s)"]+/.exec(말) || [])[0] || '';
    var 링크칸 = SC링크남기기(주소);
    return '권한은 됐습니다. 구글 쪽 스위치 하나만 켜면 됩니다.\n\n' +
      '서치콘솔 API 가 이 스크립트 프로젝트에서 꺼져 있습니다.\n\n' +
      '① ' + (링크칸 ? "'" + 링크칸 + "' 탭 A2 칸의 링크를 누르세요" : '아래 주소를 여세요') + '\n' +
      '   (이 시트와 같은 구글 계정으로)\n' +
      "② 파란 '사용' 버튼\n" +
      '③ 3~5분 기다린 뒤 이 메뉴를 다시 누르기\n\n' +
      "링크에서 '추가 액세스 권한 필요' 가 뜨면 — 구글이 자동으로 만든\n" +
      '숨은 프로젝트라 거기서는 켤 수 없습니다. 서치콘솔_설치.md 의\n' +
      "'3. 구글 클라우드 프로젝트 연결' 순서대로 해 주세요." +
      (링크칸 ? '' : '\n\n' + 주소);
  }

  if (/insufficient|scope|권한|403|401/i.test(말)) {
    return '서치콘솔을 읽을 권한이 없습니다.\n\n' +
      '① appsscript.json 에 webmasters.readonly 줄을 넣으셨는지\n' +
      '② 넣은 뒤 이 메뉴를 다시 눌러 권한 허용 창에서 "허용" 을 누르셨는지\n' +
      '③ 이 시트를 연 구글 계정이 서치콘솔을 쓰는 계정과 같은지\n\n' +
      '확인해 주세요.\n\n(원래 메시지: ' + 말 + ')';
  }
  return '서치콘솔 성적표를 받다가 문제가 생겼습니다.\n\n' + 말;
}

/**
 * 알림창의 긴 주소는 옆으로 잘려 안 보인다(실제로 그랬다).
 * 누를 수 있게 시트 칸에 적어 둔다.
 */
function SC링크남기기(주소) {
  if (!주소) return '';
  try {
    var 탭 = SC탭얻기(SC탭.블로그).탭;
    탭.clear();
    탭.getRange(1, 1, 2, 1).setValues([['서치콘솔 API 켜기 — 아래 링크를 누르고 사용 버튼'], [주소]]);
    return SC탭.블로그;
  } catch (e) {
    return '';
  }
}

function SC요약글(결과) {
  var 줄 = [];
  줄.push('서치콘솔 성적표 (' + 결과.날짜.시작 + ' ~ ' + 결과.날짜.끝 + ')');
  줄.push('');

  if (!결과.사이트수) {
    줄.push('이 구글 계정으로 볼 수 있는 서치콘솔 속성이 없습니다.');
    줄.push('서치콘솔을 쓰는 계정과 이 시트를 연 계정이 같은지 확인해 주세요.');
    return 줄.join('\n');
  }

  결과.블로그.forEach(function (칸) {
    if (!칸.등록) return;
    var 글 = 칸.전체글 !== '' ? (칸.노출글 + '/' + 칸.전체글 + '편') : (칸.노출글 + '편');
    줄.push('• ' + 칸.호스트);
    줄.push('    클릭 ' + 칸.클릭 + ' · 노출 ' + 칸.노출 +
            (칸.노출 ? ' · 평균 ' + (Math.round(칸.평균순위 * 10) / 10) + '위' : '') +
            ' · 검색에 나온 글 ' + 글);
  });

  var 빠짐 = 결과.블로그.filter(function (칸) { return !칸.등록; });
  if (빠짐.length) {
    줄.push('');
    줄.push('서치콘솔에 없는 내 블로그 ' + 빠짐.length + '곳:');
    빠짐.forEach(function (칸) { 줄.push('    ' + 칸.호스트); });
  }

  줄.push('');
  줄.push('손볼 글 ' + 결과.할일수 + '개 → ' + SC탭.할일 + ' 탭');
  줄.push('내 글끼리 싸우는 검색어 ' + 결과.싸움수 + '개 → ' + SC탭.싸움 + ' 탭');

  if (결과.실패.length) {
    줄.push('');
    줄.push('못 읽은 속성:');
    결과.실패.forEach(function (하나) { 줄.push('    ' + 하나); });
  }
  return 줄.join('\n');
}


// ── 매일 아침 자동 ──────────────────────────────────────

var SC매일함수 = '서치콘솔매일실행';

function 서치콘솔매일켜기() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === SC매일함수) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger(SC매일함수).timeBased().everyDays(1).atHour(7).create();
  SpreadsheetApp.getUi().alert(
    '매일 아침 7시쯤 서치콘솔 성적표를 자동으로 받습니다.\n' +
    (typeof 트렌드받기 === 'function' ? '같은 때 구글 트렌드에서 우리 분야 검색어도 건져 TR_후보 탭에 올립니다.\n' : '') +
    '\n' +
    'PC 가 꺼져 있어도 구글 서버에서 돕니다.\n' +
    'SC_기록 탭에 날마다 한 줄씩 쌓여 추세를 볼 수 있습니다.');
}

function 서치콘솔매일끄기() {
  var 지운수 = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === SC매일함수) { ScriptApp.deleteTrigger(t); 지운수++; }
  });
  SpreadsheetApp.getUi().alert(지운수 ? '매일 자동 받기를 껐습니다.' : '켜져 있는 자동 받기가 없습니다.');
}

/** 트리거가 부르는 함수. 화면이 없으니 알림창을 쓰지 않는다. */
function 서치콘솔매일실행() {
  try {
    서치콘솔받기();
  } catch (오류) {
    console.error('서치콘솔 매일 받기 실패: ' + 오류);
  }
  // 트렌드.gs 가 있으면 같은 아침에 트렌드도 건진다. 하나가 실패해도 다른 하나는 돈다.
  if (typeof 트렌드받기 === 'function') {
    try {
      트렌드받기();
    } catch (오류) {
      console.error('트렌드 매일 받기 실패: ' + 오류);
    }
  }
}

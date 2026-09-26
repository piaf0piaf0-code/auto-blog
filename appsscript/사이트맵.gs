// ══════════════════════════════════════════════════════════
//  사이트맵 점검 · 제출
//
//  블로그 진단에서, 1년 동안 한 번도 검색에 안 나온 글을 구글에
//  물었더니 거의 모든 블로그에서 '구글이 모름(URL is unknown to Google)'
//  이 나왔다. 구글이 읽고 거절한 게 아니라, 글이 있는 줄도 모른다.
//
//  구글이 새 글을 아는 가장 기본적인 길이 사이트맵이다.
//  블로그마다
//    ① 서치콘솔에 사이트맵이 제출돼 있는지
//    ② 구글이 마지막으로 언제 읽어 갔는지, 오류는 없는지
//  를 보고, 없거나 오래됐거나 오류가 있으면 다시 제출한다.
//
//  읽기는 지금 권한(webmasters.readonly)으로 되고,
//  제출에는 appsscript.json 에 한 줄(webmasters)이 더 있어야 한다.
//  없으면 점검만 하고 무엇을 넣어야 하는지 알려 준다.
// ══════════════════════════════════════════════════════════

var 맵탭 = 'SC_사이트맵';
var 맵오래됨일수 = 30;   // 구글이 이보다 오래 안 읽어 갔으면 다시 제출

/** 블로그에서 실제로 열리는 사이트맵 주소들. 티스토리는 RSS 도 함께 낸다(새 글을 빨리 알림). */
function 맵주소찾기(호스트) {
  var 티스토리 = /tistory\.com$/.test(호스트);
  var 후보 = 티스토리 ? ['/sitemap.xml'] : ['/sitemap_index.xml', '/wp-sitemap.xml', '/sitemap.xml'];
  var 머리 = (typeof 브라우저인척 !== 'undefined') ? 브라우저인척.headers : {};
  for (var i = 0; i < 후보.length; i++) {
    var 주소 = 'https://' + 호스트 + 후보[i];
    try {
      var 답 = UrlFetchApp.fetch(주소, { muteHttpExceptions: true, followRedirects: true, headers: 머리 });
      var 글 = 답.getResponseCode() === 200 ? 답.getContentText() : '';
      if (글.indexOf('<urlset') !== -1 || 글.indexOf('<sitemapindex') !== -1) {
        return 티스토리 ? [주소, 'https://' + 호스트 + '/rss'] : [주소];
      }
    } catch (e) { }
  }
  return [];
}

function 맵목록(사이트) {
  var 답 = SC요청('sites/' + encodeURIComponent(사이트) + '/sitemaps');
  return 답.sitemap || [];
}

function 맵제출(사이트, 주소) {
  var 답 = UrlFetchApp.fetch(
    SC주소 + 'sites/' + encodeURIComponent(사이트) + '/sitemaps/' + encodeURIComponent(주소),
    { method: 'put', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() } });
  var 코드 = 답.getResponseCode();
  if (코드 === 200 || 코드 === 204) return '';
  var 까닭 = '';
  try { 까닭 = JSON.parse(답.getContentText()).error.message; } catch (e) { 까닭 = String(답.getContentText()).slice(0, 120); }
  return 코드 + ' ' + 까닭;
}

function 맵며칠전(날짜글) {
  if (!날짜글) return null;
  var t = new Date(날짜글).getTime();
  if (isNaN(t)) return null;
  return Math.floor((Date.now() - t) / (24 * 60 * 60 * 1000));
}

function 맵점검(제출할까) {
  var 사이트들 = SC사이트목록();
  var 호스트들 = SC내블로그();
  // 서치콘솔 속성의 호스트도 넣는다 (카테고리표에 없는 블로그도 보려고)
  사이트들.forEach(function (s) {
    var h = s.indexOf('sc-domain:') === 0 ? s.slice(10) : SC호스트(s);
    if (h && 호스트들.indexOf(h) === -1) 호스트들.push(h);
  });
  // 도메인 속성(sc-domain:seaga.co.kr) 아래의 하위 블로그(seaga5.seaga.co.kr 등)는
  // 카테고리표에도 속성 목록에도 따로 없다. 진단·성적표 탭에 있는 블로그를 더한다.
  ['SC_진단', 'SC_블로그별'].forEach(function (탭이름) {
    var 탭 = SpreadsheetApp.getActive().getSheetByName(탭이름);
    if (!탭 || 탭.getLastRow() < 2) return;
    탭.getRange(2, 1, 탭.getLastRow() - 1, 1).getValues().forEach(function (줄) {
      var h = String(줄[0] || '').trim().toLowerCase();
      if (/^[a-z0-9.-]+\.[a-z]{2,}$/.test(h) && 호스트들.indexOf(h) === -1) 호스트들.push(h);
    });
  });
  if (typeof 진단위험블로그 !== 'undefined') {
    // 정리할 블로그에는 사이트맵을 새로 내지 않는다
    호스트들 = 호스트들.filter(function (h) { return 진단위험블로그.indexOf(h) === -1; });
  }

  var 속성목록 = {};   // 속성 → 제출된 사이트맵들 (한 번만 받는다)
  var 줄들 = [], 셈 = { 정상: 0, 새로제출: 0, 다시제출: 0, 제출필요: 0, 실패: 0, 없음: 0 };
  var 권한부족 = false;

  호스트들.forEach(function (호스트) {
    var 사이트 = SC속성고르기(사이트들, 호스트, 'https://' + 호스트 + '/');
    if (!사이트) {
      줄들.push([호스트, '', '', '서치콘솔에 없음', '', '', '', '', '서치콘솔에 먼저 등록']);
      셈.없음++;
      return;
    }
    if (!속성목록[사이트]) {
      try { 속성목록[사이트] = 맵목록(사이트); } catch (e) { 속성목록[사이트] = []; }
    }
    var 이블로그것 = 속성목록[사이트].filter(function (m) { return SC호스트(m.path) === 호스트; });
    var 찾은주소 = 맵주소찾기(호스트);

    if (!이블로그것.length && !찾은주소.length) {
      줄들.push([호스트, 사이트, '', '사이트맵 없음', '', '', '', '', '블로그에서 사이트맵이 열리지 않음 — 설정 확인']);
      셈.없음++;
      return;
    }

    // 블로그에 있는데 서치콘솔에 안 낸 것 + 낸 것 중 문제 있는 것
    var 낸주소 = 이블로그것.map(function (m) { return m.path; });
    var 할일 = [];
    찾은주소.forEach(function (주소) {
      if (낸주소.indexOf(주소) === -1) 할일.push({ 주소: 주소, 까닭: '제출 안 돼 있음', 종류: '새로' });
    });
    이블로그것.forEach(function (m) {
      var 며칠 = 맵며칠전(m.lastDownloaded);
      var 오류 = Number(m.errors || 0);
      var 까닭 = 오류 > 0 ? '오류 ' + 오류 + '개'
               : (며칠 === null ? '구글이 한 번도 안 읽어 감'
               : (며칠 > 맵오래됨일수 ? '구글이 ' + 며칠 + '일째 안 읽어 감' : ''));
      var 글수 = (m.contents || []).reduce(function (합, c) { return 합 + Number(c.submitted || 0); }, 0);
      줄들.push([호스트, 사이트, m.path,
                 까닭 ? '문제 있음' : '정상',
                 m.lastSubmitted ? String(m.lastSubmitted).slice(0, 10) : '',
                 m.lastDownloaded ? String(m.lastDownloaded).slice(0, 10) : '',
                 m.errors || 0, m.warnings || 0, 까닭 || (글수 ? '제출된 글 ' + 글수 + '편' : '')]);
      if (까닭) 할일.push({ 주소: m.path, 까닭: 까닭, 종류: '다시' });
      else 셈.정상++;
    });

    할일.forEach(function (일) {
      if (!제출할까 || 권한부족) {
        줄들.push([호스트, 사이트, 일.주소, '제출 필요', '', '', '', '', 일.까닭]);
        셈.제출필요++;
        return;
      }
      var 실패 = 맵제출(사이트, 일.주소);
      if (!실패) {
        줄들.push([호스트, 사이트, 일.주소, 일.종류 === '새로' ? '새로 제출함' : '다시 제출함', '오늘', '', '', '', 일.까닭]);
        if (일.종류 === '새로') 셈.새로제출++; else 셈.다시제출++;
      } else {
        if (/insufficient|scope|permission|403/i.test(실패)) 권한부족 = true;
        줄들.push([호스트, 사이트, 일.주소, '제출 실패', '', '', '', '', 실패]);
        셈.실패++;
      }
    });
  });

  SC탭쓰기(맵탭, ['블로그', '서치콘솔 속성', '사이트맵', '상태', '마지막 제출', '구글이 마지막으로 읽은 날', '오류', '경고', '까닭'], 줄들);
  return { 셈: 셈, 권한부족: 권한부족, 블로그수: 호스트들.length };
}

function 사이트맵점검메뉴() {
  var 화면 = SpreadsheetApp.getUi();
  var 결과;
  try {
    결과 = 맵점검(true);
  } catch (오류) {
    화면.alert(typeof SC오류설명 === 'function' ? SC오류설명(오류) : String(오류));
    return;
  }
  var 셈 = 결과.셈;
  var 줄 = ['사이트맵 점검 — 블로그 ' + 결과.블로그수 + '곳', '',
            '정상 ' + 셈.정상, '새로 제출함 ' + 셈.새로제출, '다시 제출함 ' + 셈.다시제출,
            '제출 필요(못 함) ' + (셈.제출필요 + 셈.실패), '서치콘솔·사이트맵 없음 ' + 셈.없음, ''];
  if (결과.권한부족) {
    줄.push('제출할 권한이 없어 점검만 했습니다.');
    줄.push('appsscript.json 의 oauthScopes 에 이 줄을 더하고 저장한 뒤 다시 누르세요.');
    줄.push('   "https://www.googleapis.com/auth/webmasters"');
    줄.push('');
  }
  줄.push('자세한 것은 ' + 맵탭 + ' 탭에 있습니다.');
  줄.push('제출한 뒤 구글이 읽어 가기까지 며칠 걸립니다. 2~3주 뒤 🩺 블로그 전체 진단 을 다시 누르면');
  줄.push("'구글이 모름' 이 줄었는지 보입니다.");
  결과창('사이트맵 점검·제출', 줄.join('\n'));
}

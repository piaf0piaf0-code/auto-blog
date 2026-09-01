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
                      '작성상태', '발행URL', '쓴날짜'];

/** finwiz_링크관리 탭의 열 이름 (순서 그대로). */
var finwiz링크헤더 = ['ID', '글 제목', '상태', 'URL', '클러스터', '키워드'];


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
  var 이전작성 = {}, 이전발행 = {}, 이전날짜 = {};
  if (순서.getLastRow() >= 2) {
    순서.getRange(2, 1, 순서.getLastRow() - 1, finwiz순서헤더.length).getValues()
      .forEach(function (줄) {
        var id = String(줄[9] || '').trim();
        if (!id) return;
        if (String(줄[13] || '').trim()) 이전작성[id] = String(줄[13]).trim();
        if (String(줄[14] || '').trim()) 이전발행[id] = String(줄[14]).trim();
        if (String(줄[15] || '').trim()) 이전날짜[id] = String(줄[15]).trim();
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
      발행, 이전날짜[id] || ''
    ];
  });
  if (순서.getLastRow() >= 2) {
    순서.getRange(2, 1, 순서.getLastRow() - 1, 순서.getLastColumn()).clearContent();
  }
  순서.getRange(2, 1, 순서줄.length, finwiz순서헤더.length).setValues(순서줄);
  순서.autoResizeColumn(9);

  SpreadsheetApp.flush();
  return {
    글수: 순서줄.length,
    링크수: 링크줄.length,
    주소있음: 링크줄.filter(function (줄) { return !!줄[3]; }).length
  };
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
        쓴날짜: String(줄[15] || '').trim()
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
//  화면이 부르는 것들
// ══════════════════════════════════════════════════════════

/** 다음에 쓸 글 목록. 아직 안 쓴 것 위에서부터. */
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

  var 보여줄것 = 남은것.slice(0, 12).map(function (한줄) {
    var 링크 = finwiz링크고르기(한줄, 링크맵, 순서목록);
    return {
      순번: 한줄.순번, 단계: 한줄.단계, 클러스터: 한줄.클러스터,
      제목: 한줄.제목, 메인키워드: 한줄.메인키워드,
      작성전략: 한줄.작성전략, 확인URL: 한줄.확인URL,
      작성상태: 한줄.작성상태 || '미작성',
      링크수: 링크.length,
      링크: 링크
    };
  });

  var 주소있음 = 0;
  for (var id in 링크맵) if (링크맵[id].URL) 주소있음++;

  return {
    전체: 순서목록.length,
    남음: 남은것.length,
    끝남: 순서목록.length - 남은것.length,
    주소있음: 주소있음,
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

  var 규칙 = (typeof 공통규칙 !== 'undefined') ? 공통규칙.slice() : [];
  규칙.push('- 40대 이상 독자를 생각해 친절하고 쉬운 존댓말로 써 주세요.');
  규칙.push('- 제목은 아래 [고정 제목] 을 글자 하나 바꾸지 말고 그대로 쓰세요. 새로 짓지 마세요.');
  규칙.push('- 대출 조건·한도·금리는 단정하지 말고, 확인해야 할 기준과 순서를 알려 주세요.');
  규칙.push('- 특정 업체를 추천하거나 유도하지 마세요.');

  var 정보줄 = [
    '[글쓰기 정보]',
    '이번 글 순번: ' + 대상.순번 + '번 (' + 대상.단계 + ')',
    '주제 묶음: ' + 대상.클러스터,
    '메인 키워드: ' + 대상.메인키워드,
    '올릴 곳: 티스토리(finwiz.tistory.com)'
  ];
  if (대상.검색량) 정보줄.push('월 검색량(참고): ' + 대상.검색량);
  if (대상.작성전략) 정보줄.push('이 글의 차별점: ' + 대상.작성전략);
  if (대상.확인URL) 정보줄.push('먼저 확인할 원문: ' + 대상.확인URL);

  var 조각 = [
    정보줄.join('\n'),
    '',
    '[고정 제목]',
    대상.제목,
    ''
  ];

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

  조각.push('[요청]');
  if (대상.확인URL) {
    조각.push('위 원문을 먼저 웹 검색으로 확인해 주세요.');
  } else {
    조각.push('메인 키워드를 먼저 웹 검색으로 확인해 주세요.');
  }
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
           메인키워드: 대상.메인키워드, GPT주소: (typeof 마이GPT주소 !== 'undefined') ? 마이GPT주소 : '' };
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

  var 덩어리 = ['<h2>함께 보면 좋은 글</h2>', '<ul>'];
  빠진것.forEach(function (한개) {
    덩어리.push('<li><a href="' + 한개.URL + '" target="_blank" rel="noopener">' +
                한개.제목 + '</a></li>');
  });
  덩어리.push('</ul>');
  var 넣을것 = 덩어리.join('\n');

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
  if (열.html !== -1 && 열.html !== undefined) {
    var 본문 = String(탭.getRange(행번호, 열.html + 1).getValue() || '');
    채움 = finwiz링크채우기(본문, 링크);
    if (채움.채운것.length) {
      탭.getRange(행번호, 열.html + 1).setValue(채움.HTML);
    }
  }

  // 로드맵에 '작성완료' 로 표시 (주소는 발행 확인이 나중에 채운다)
  var 순서탭 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  순서탭.getRange(대상.행, 14).setValue('작성완료');
  순서탭.getRange(대상.행, 16).setValue(
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd'));
  SpreadsheetApp.flush();

  return {
    성공: true,
    상태: 결과.상태,
    행번호: 행번호,
    메모: 결과.메모 || [],
    넣은링크: 링크.length,
    채운링크: 채움.채운것.map(function (한개) { return 한개.제목; })
  };
}


// ══════════════════════════════════════════════════════════
//  발행 확인 뒤 — 찾은 주소를 링크관리에 적는다
//
//  이게 돌아야 다음 글이 이 글을 링크로 걸 수 있다.
//  발행확인() 이 끝날 때 자동으로 불린다. 메뉴로 따로 눌러도 된다.
// ══════════════════════════════════════════════════════════

function finwiz주소반영() {
  var 링크맵 = finwiz링크읽기();
  if (!Object.keys(링크맵).length) return { 반영: 0 };

  // 제목 → 링크관리 줄
  var 제목목록 = [];
  for (var id in 링크맵) 제목목록.push(링크맵[id]);

  var 문서 = finwiz문서();
  var 찾은주소 = {};   // ID → URL

  [탭이름, 완료탭이름].forEach(function (이름) {
    var 탭 = 문서.getSheetByName(이름);
    if (!탭 || 탭.getLastRow() < 2) return;
    var 헤더 = 탭.getRange(1, 1, 1, 탭.getLastColumn()).getValues()[0];
    var 열 = 열찾기(헤더);
    if (열.title === -1 && 열['ts제목'] === -1) return;

    탭.getRange(2, 1, 탭.getLastRow() - 1, 탭.getLastColumn()).getValues()
      .forEach(function (행) {
        var 주소 = '';
        if (열['ts주소'] !== -1 && 열['ts주소'] !== undefined) {
          주소 = String(행[열['ts주소']] || '').trim();
        }
        if (!주소) return;
        var 제목들 = [];
        if (열.title !== -1) 제목들.push(String(행[열.title] || '').trim());
        if (열['ts제목'] !== -1 && 열['ts제목'] !== undefined) {
          제목들.push(String(행[열['ts제목']] || '').trim());
        }
        제목목록.forEach(function (글) {
          if (찾은주소[글.ID] || 글.URL) return;
          for (var i = 0; i < 제목들.length; i++) {
            if (제목들[i] && 제목같나(제목들[i], 글.제목)) {
              찾은주소[글.ID] = 주소;
              return;
            }
          }
        });
      });
  });

  var 반영 = 0;
  var 링크탭 = finwiz탭(finwiz링크탭, finwiz링크헤더);
  var 순서탭 = finwiz탭(finwiz순서탭, finwiz순서헤더);
  var 순서목록 = finwiz순서읽기();
  var 순서행 = {};
  순서목록.forEach(function (한줄) { 순서행[한줄.ID] = 한줄.행; });

  for (var id2 in 찾은주소) {
    링크탭.getRange(링크맵[id2].행, 4).setValue(찾은주소[id2]);
    링크탭.getRange(링크맵[id2].행, 3).setValue('발행완료');
    if (순서행[id2]) {
      순서탭.getRange(순서행[id2], 15).setValue(찾은주소[id2]);
      순서탭.getRange(순서행[id2], 14).setValue('발행완료');
    }
    반영++;
  }
  if (반영) SpreadsheetApp.flush();
  return { 반영: 반영 };
}

function finwiz주소반영메뉴() {
  var ui = SpreadsheetApp.getUi();
  try {
    var 결과 = finwiz주소반영();
    ui.alert(결과.반영
      ? '새로 확인된 글 주소 ' + 결과.반영 + '개를 ' + finwiz링크탭 + ' 에 적었습니다.\n' +
        '이제 다음 글부터 이 글들을 링크로 걸 수 있습니다.'
      : '새로 적을 주소가 없습니다.\n' +
        '먼저 "🔎 발행됐는지 확인" 을 눌러 주소를 찾아 주세요.');
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

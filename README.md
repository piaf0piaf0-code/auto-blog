# auto-blog — 블로그 네트워크 수익화 자동화 시스템

> 목표: 고품질 콘텐츠 + 선택적 자동화로 **검색 상위 트래픽**을 확보해
> 애드센스 수익을 월 2천만원 수준까지 끌어올린다.
> 핵심 원칙: **"많은 블로그에 양산"이 아니라 "소수 고단가 사이트에 품질 + SEO 집중"**.

2년 운영 후 월 10만원 이하라는 현재 상태는, 도구가 아니라 **전략**이
구글 알고리즘(특히 2024년 이후의 Scaled Content Abuse 정책)과 충돌하고
있다는 신호다. 이 저장소는 그 전략을 처음부터 다시 세우고, 반복 노동만
자동화한다.

---

## 빠른 시작 (5분)

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY 등을 채운다

# 3. 키워드 기회 발굴 (예: 대출 분야)
python -m src.run_pipeline keywords --niche "대출" --count 20

# 4. 선택한 키워드로 콘텐츠 초안 생성 (사람 검수용)
python -m src.run_pipeline brief --keyword "전세자금대출 조건"

# 5. 검수 후 워드프레스에 초안(draft)으로 발행
python -m src.run_pipeline publish --file drafts/전세자금대출-조건.md --site finwiz
```

자세한 설명은 `docs/` 폴더를 순서대로 읽는다.

---

## 글 이관: A 사이트 글 → B 사이트 업그레이드 발행

A 워드프레스의 옛 글을 가져와 **현재 검색 트렌드 기준으로 업그레이드**한 뒤
B 워드프레스에 발행한다. 한 명령으로 다음이 자동 처리된다:

1. A 에서 글 가져오기 (REST API)
2. A 본문의 수동 애드센스 코드 **전부 제거**
3. Claude 가 오늘 날짜 기준으로 본문 갱신 + **클릭 유도형 새 제목**·메타설명·슬러그 생성
4. 본문 3곳(도입 직후/중간/결론 앞)에 **B 도메인 애드센스 코드 삽입**
5. **썸네일 이미지 자동 제작** → B 미디어 업로드 → 대표이미지 지정
6. B 에 draft(비공개)로 발행 → 검수 후 공개

```bash
# 0. .env 에 A/B 사이트 인증 + B 애드센스 등록 (.env.example 참고)
#    WP_A_URL / WP_A_USER / WP_A_APP_PASSWORD
#    WP_B_URL / WP_B_USER / WP_B_APP_PASSWORD
#    ADSENSE_B_CLIENT=ca-pub-...   ADSENSE_B_SLOTS=슬롯1,슬롯2,슬롯3

# 1. A 사이트 글 목록에서 이관할 글 고르기
python -m src.run_pipeline migrate --from-site a --to-site b --list 20

# 2. 이관 실행 (기본: B에 비공개 draft로 올라감)
python -m src.run_pipeline migrate --from-site a --to-site b --post-id 123

# URL로 지정하거나, 포커스 키워드를 주거나, 이관 후 A 원본을 비공개할 수도 있다
python -m src.run_pipeline migrate --from-site a --to-site b \
  --post-url "https://old-site.com/옛글-주소" \
  --keyword "2026 ○○ 신청 방법" \
  --retire-source
```

이관 후 체크리스트 (명령이 끝나면 자동으로 다시 알려준다):

- **중복 콘텐츠 방지**: A 원본을 비공개(`--retire-source`)하거나 A→B 301 리다이렉트
- B 도메인 루트의 `ads.txt` 에 B 게시자 ID가 등록돼 있는지 확인
- 모델이 표시한 "확인 필요 항목"(숫자·제도·날짜)을 1차 출처로 검증한 뒤 공개

---

## 문서 읽는 순서

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | [docs/01-진단과-전략.md](docs/01-진단과-전략.md) | 현재 자산 진단 + 왜 전략을 바꿔야 하는가 |
| 2 | [docs/02-90일-실행로드맵.md](docs/02-90일-실행로드맵.md) | 0→수익까지 단계별 90일 계획 |
| 3 | [docs/03-키워드-선정-가이드.md](docs/03-키워드-선정-가이드.md) | 고단가·저경쟁 키워드 찾는 법 |
| 4 | [docs/04-콘텐츠-품질-체크리스트.md](docs/04-콘텐츠-품질-체크리스트.md) | 페널티 피하면서 자동화하는 기준 |
| 5 | [docs/05-자동화-아키텍처.md](docs/05-자동화-아키텍처.md) | make.com + Claude + 워드프레스 설계 |

---

## 디렉터리 구조

```
auto-blog/
├── README.md
├── docs/                  # 전략·운영 문서 (한국어)
├── src/                   # 자동화 코드 (Python)
│   ├── config.py          # 설정/사이트 정보
│   ├── keyword_planner.py # Claude 기반 키워드 기회 발굴
│   ├── content_brief.py   # 검색의도 기반 콘텐츠 초안 생성
│   ├── wordpress_publisher.py # 워드프레스 REST API 발행
│   └── run_pipeline.py    # CLI 진입점
├── drafts/                # 생성된 초안 (검수 대기) — git 미추적
├── requirements.txt
└── .env.example
```

---

## 안전 가이드 (반드시 읽을 것)

- **구글애즈 → 애드센스 아비트라지(연결 블로그 + 브리지 페이지) 구조는 만들지 않는다.**
  애드센스 정책 위반이며, 적발 시 연결된 계정 전체가 정지될 수 있다.
- **AI로 글을 "대량 양산"하지 않는다.** 이 시스템은 *초안*을 만들고,
  사람이 검수·보강·검증한 뒤 발행하는 것을 전제로 한다. 발행 기본값은
  `draft`(비공개)이며, 검수 후 수동으로 공개한다.
- 모든 글은 `docs/04-콘텐츠-품질-체크리스트.md`를 통과해야 발행한다.

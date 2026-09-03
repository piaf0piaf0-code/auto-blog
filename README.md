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

### 유튜브 숏츠 만들기 (노코드)

명령어 없이 씁니다. **`실행.bat` 더블클릭** → 브라우저 화면이 열립니다.

```
① 유튜브 링크 붙여넣기 → [영상 불러오기]
② 구간 정하기 — [🤖 AI가 골라주기] 로 제안받아 클릭하거나,
   영상을 보다가 [⏱ 여기가 시작] / [⏹ 여기가 끝]
③ 숏츠 모양 고르기 (세로 9:16 변환 기본 켜짐 · 훅 문구 입력 가능)
④ [✂️ 클립 만들기] → [파일 저장하기]
```

시간을 손으로 적을 필요가 없습니다. 필요한 것(ffmpeg 포함)은 첫 실행 때
자동으로 설치됩니다. 파이썬만 미리 깔려 있으면 됩니다
(설치 시 **"Add python.exe to PATH" 체크**).

<details>
<summary>명령줄로 쓰기 (자동화용)</summary>

```bash
# 자막을 읽고 숏츠용 구간 추천받기 (Claude API 키 필요)
python -m src.run_pipeline suggest --url "https://youtu.be/XXXXXXXXXXX"

# 영상 정보·챕터 확인 (다운로드 없음)
python -m src.run_pipeline clip --url "https://youtu.be/XXXXXXXXXXX" --info

# 1:30 ~ 2:10 구간만 추출 → outputs/clips/
python -m src.run_pipeline clip --url "https://youtu.be/XXXXXXXXXXX" --start 1:30 --end 2:10

# 자른 뒤 숏츠용 세로(1080x1920)로 변환 + 훅 문구
python -m src.run_pipeline clip --url "https://youtu.be/XXXXXXXXXXX" --start 1:30 --duration 45 \
  --vertical --text "이 장면 하나로 조회수가 터졌습니다"

# 한 영상에서 여러 클립 (원본은 한 번만 내려받는다)
python -m src.run_pipeline clip --url "https://youtu.be/XXXXXXXXXXX" \
  --range 1:30-2:10 --range 5:00-5:45
```
</details>

자세한 내용: [docs/09-유튜브-숏츠-소재-추출.md](docs/09-유튜브-숏츠-소재-추출.md)

자세한 설명은 `docs/` 폴더를 순서대로 읽는다.

---

## 문서 읽는 순서

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | [docs/01-진단과-전략.md](docs/01-진단과-전략.md) | 현재 자산 진단 + 왜 전략을 바꿔야 하는가 |
| 2 | [docs/02-90일-실행로드맵.md](docs/02-90일-실행로드맵.md) | 0→수익까지 단계별 90일 계획 |
| 3 | [docs/03-키워드-선정-가이드.md](docs/03-키워드-선정-가이드.md) | 고단가·저경쟁 키워드 찾는 법 |
| 4 | [docs/04-콘텐츠-품질-체크리스트.md](docs/04-콘텐츠-품질-체크리스트.md) | 페널티 피하면서 자동화하는 기준 |
| 5 | [docs/05-자동화-아키텍처.md](docs/05-자동화-아키텍처.md) | make.com + Claude + 워드프레스 설계 |
| 6 | [docs/09-유튜브-숏츠-소재-추출.md](docs/09-유튜브-숏츠-소재-추출.md) | 유튜브 링크 → 구간 추출 → 세로 숏츠 영상 |

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
│   ├── clip_finder.py     # 자막 읽고 AI 가 구간 추천 (숏츠 0단계)
│   ├── youtube_clipper.py # 유튜브 구간 추출 (숏츠 1단계)
│   ├── vertical.py        # 9:16 세로 변환 + 훅 문구 (숏츠 2단계)
│   ├── shorts_ui.py       # 위 기능의 노코드 웹 화면
│   └── run_pipeline.py    # CLI 진입점
├── 실행.bat               # Windows: 더블클릭하면 숏츠 추출 화면이 열린다
├── tests/                 # 단위 테스트 (python -m unittest discover -s tests)
├── drafts/                # 생성된 초안 (검수 대기) — git 미추적
├── outputs/               # 추출한 클립·원본 — git 미추적
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

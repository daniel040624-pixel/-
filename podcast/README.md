# 🎙 팟캐스트 스튜디오

두 가지 방식으로 영상을 만들고 올립니다.

| 방식 | 언제 | 하는 일 |
|---|---|---|
| **A. 매일 촬영 모드** (메인) | 내가 직접 찍은 영상 | 오늘 촬영 카드 → 찍어서 `inbox/` 에 넣기 → **자동 편집(템플릿) → 시기별 시간표대로 예약/즉시 업로드** |
| B. AI 팟캐스트 모드 | 촬영이 어려운 날, 긴 토론 에피소드 | 주제+내 생각 → AI 대본·음성·영상 → 업로드 |

전략 문서:
- [`strategy/growth-playbook.md`](strategy/growth-playbook.md) — 구독자 0~100 / 100~1천 / 1천~1만 / 1만~10만 **시기별 방법론**
- [`strategy/brand-guide.md`](strategy/brand-guide.md) — 정체성, **2개 트랙(A 채널의 메시지 · B 시청자가 원하는 메시지)**, **2개 형식(혼자 · 대화)**, 톤앤매너, 주간 리듬
- [`plan/calendar.csv`](plan/calendar.csv) — **첫 30일 촬영 계획** (2026-10-19 시작, 엑셀로 열어 수정 가능)

---

## A. 매일 촬영 모드

```
 아침            촬영 15분                     자동                                         자동
studio today ─▶ 휴대폰으로 찍기 ─▶ inbox/에 넣기 ─▶ 공백 삭제·자막·화면비·제목·진행바·CTA ─▶ 시간표대로 예약 업로드
(오늘 카드)                                      (롱폼이면 하이라이트 쇼츠 자동 추출)           (단계별 시간 자동 전환)
```

### 하루 루틴

```bash
python -m studio today                 # 오늘 찍을 카드: 형식, 첫 마디(훅), 말할 것 3개, 마무리 질문, 파일명
# → 찍은 영상을 inbox/2026-10-19_solo_short.mp4 처럼 넣기 (watch 가 켜져 있으면 끝)
```

직접 실행하고 싶다면:

```bash
python -m studio cut 영상.mp4              # 편집만 (daily/<날짜-이름>/ 에 결과)
python -m studio cut 영상.mp4 --schedule   # 편집 + 다음 빈 시간으로 예약
python -m studio cut 영상.mp4 --now        # 편집 + 즉시 공개
python -m studio publish daily/<폴더> --at 2026-10-20T19:00   # 원하는 시각으로 예약
python -m studio status                    # 편집·예약·공개 현황
```

### 템플릿 (`templates/*.toml`)

| 템플릿 | 찍는 법 | 결과 |
|---|---|---|
| `solo_short` | 세로, 혼자, 30~60초 | 1080×1920, 공백 삭제, 큰 자막, 상단 질문 박스, 진행 바, 마지막 구독 CTA |
| `dialogue_short` | 가로, 둘이서, 1분 | 세로 변환(원본 + 흐린 배경), 자막, 질문 박스, CTA |
| `dialogue_long` | 가로, 둘이서, 15~30분 | 1920×1080, 긴 침묵만 삭제, 자막, 썸네일 + **하이라이트 쇼츠 3개 자동** |
| `solo_long` | 가로, 혼자, 5~12분 | 1920×1080, 자막, 썸네일 + 하이라이트 쇼츠 2개 |

- 템플릿은 **파일명**(`solo`/`dialogue`, `long`) → **오늘 계획표** → 화면 방향 순으로 자동 선택. `--template` 으로 직접 지정도 가능
- 글자 크기·위치, 공백 삭제 강도, 최대 길이, 클립 개수는 toml 에서 숫자만 바꾸면 됩니다. 새 형식은 파일을 복사해 이름만 바꾸면 추가됩니다
- 자막은 `faster-whisper` 로 자동 받아쓰기(첫 실행 때 모델 다운로드). 영상과 같은 이름의 `.srt`(예: CapCut 자동 자막)를 같이 넣으면 그걸 씁니다
- 제목·설명·태그·하이라이트 구간은 Claude 가 브랜드 가이드 톤으로 작성 (계획표에 제목이 있으면 그 제목 우선)

### 예약 업로드 · 시기별 자동 전환

`config.toml [[stages]]` 의 시간표대로 다음 빈 시간에 예약됩니다 (유튜브의 `publishAt` 예약 기능이라 컴퓨터가 꺼져 있어도 공개됨).

| 단계 | 직접 찍은 쇼츠 | 롱폼 | 롱폼 클립 쇼츠 |
|---|---|---|---|
| 1단계 0~100 | 매일 19:00 | 일 20:00 | 12:10 |
| 2단계 100~1천 | 매일 19:00 | 수·일 20:00 | 12:10 |
| 3단계 1천~1만 | 매일 19:00 | 수·일 20:00 | 12:10, 16:30 |
| 4단계 1만~10만 | 매일 19:00 | 월·수·금 20:00 | 12:10, 16:30 |

- `watch` 가 6시간마다 내 채널 구독자 수를 확인해 단계(시간표)를 자동으로 바꿉니다. 수동 확인: `python -m studio stage`
- 몰아 찍기: 파일명에 날짜를 붙이면(`2026-10-22_solo_short.mp4`) 그날 계획과 연결되고 그날 시간에 예약됩니다
- 롱폼의 클립은 원본 공개 **이후** 빈 시간에 예약되고, 설명란 첫 줄에 원본 링크가 자동으로 들어갑니다
- 쇼츠마다 `*_caption.txt` 가 생겨 인스타 릴스·틱톡에 그대로 붙여 올릴 수 있습니다

### 항상 켜두기

```bash
python -m studio watch               # inbox/ 감시 → 편집 → 시간표대로 예약
python -m studio watch --mode now    # 넣는 즉시 공개
```

cron · Windows 작업 스케줄러 · 클라우드 폴더 연동은 [`scripts/autostart.md`](scripts/autostart.md) 참고.

### 매주·매월 루틴

```bash
python -m studio listen              # (일) 내 영상 성과 + 댓글 수집
python -m studio plan --days 7       # (일) 다음 주 계획 자동 생성 — 브랜드 가이드·현재 단계·댓글 반영
python -m studio benchmark "인사이트 토크" "생각 정리" --ai   # (월 1회) 비슷한 구독자 1만+ 채널 분석
```

`benchmark` 는 `YOUTUBE_API_KEY` 환경 변수(Google Cloud 콘솔에서 발급하는 API 키)를 쓰고, 없으면 OAuth 로그인을 씁니다.

---

## B. AI 팟캐스트 모드

**주제와 내 생각만 적으면** 토론형 팟캐스트 대본 → 음성 → 편집 → 영상(본편 + 쇼츠) → 유튜브 업로드까지 명령 한 줄로 끝내는 자동화 파이프라인입니다.

```
episode.toml ──▶ ① script ──▶ ② voice ──▶ ③ edit ──▶ ④ render ──▶ ⑤ upload
 (주제·나의 생각)   Claude가        출연자별      음성 연결·       16:9 본편       YouTube 본편
                  토론 대본 작성    AI 음성 합성   배경음악·음량·    9:16 쇼츠       + 쇼츠 + 자막
                  (script.json)  (edge-tts)    자막·챕터        썸네일          + 썸네일 (+RSS)
```

| 단계 | 하는 일 | 결과물 (`episodes/<에피소드>/build/`) |
|---|---|---|
| ① script | 내 관점을 중심으로 **훅 → 쟁점 → 나의 주장 → 가장 강한 반론 → 토론 → 깨달음 → 실천** 구조의 대본 작성, 제목·썸네일 문구·쇼츠 구간까지 선정 | `script.json` |
| ② voice | 대사마다 출연자 목소리로 합성 (고친 대사만 다시 합성) | `lines/*.mp3` |
| ③ edit | 대사 사이 쉼, 인트로/아웃트로, 배경음악 자동 덕킹, -16 LUFS 음량 표준화, 자막·챕터 생성 | `episode.mp3`, `subtitles.srt`, `timeline.json` |
| ④ render | 배경 + 음성 파형 + 화자별 색 자막 + 현재 챕터 표시 영상, 세로 쇼츠 최대 3개, 썸네일 | `episode.mp4`, `short*.mp4`, `thumbnail.jpg` |
| ⑤ upload | 제목·설명(챕터·깨달음·질문·해시태그)·태그 작성 후 유튜브 업로드, 자막·썸네일·재생목록 설정 | `upload.json`, `description.txt` |

---

## 설치 (A·B 공통, 최초 1회)

```bash
# ffmpeg + 한글 폰트
sudo apt install ffmpeg fonts-noto-cjk      # macOS: brew install ffmpeg  (폰트는 기본 내장)

cd podcast
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11 이상
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...         # https://console.anthropic.com 에서 발급
```

그다음 `config.toml` 을 열어 **채널 이름, 사명, 출연자(이름·역할·목소리)** 를 내 채널에 맞게 고치세요.
첫 번째 출연자가 "나"이고, 대본은 항상 이 사람의 관점을 중심으로 쓰입니다.

## B-1. 에피소드 만들기

```bash
# 1) 새 에피소드 폴더 생성
python -m studio new "AI가 다 알려주는 시대, 스스로 생각해야 할까?"

# 2) 생성된 episodes/<날짜-주제>/episode.toml 에 '나의 생각'을 적는다 (예시: episodes/example)

# 3) 한 번에 실행
python -m studio run episodes/<날짜-주제>
```

**처음에는 업로드 전에 한 번 확인하는 걸 권장합니다:**

```bash
python -m studio run episodes/<에피소드> --until script   # 대본만 만들고 멈춤
#   → build/script.json 을 열어 말투·주장을 직접 다듬기 (내 목소리가 되도록!)
python -m studio run episodes/<에피소드> --until render   # 영상까지 만들고 멈춤
#   → build/episode.mp4 확인
python -m studio run episodes/<에피소드> --from upload    # 업로드
```

각 단계는 결과를 저장해 두기 때문에, 다시 실행하면 끝난 단계는 건너뜁니다.
특정 단계를 다시 하려면 `--force script`, 또는 단계별 명령 `python -m studio render <에피소드> --force` 를 쓰세요.

### 직접 녹음한 목소리로 만들기

내 목소리로 직접 이야기하고 싶다면 녹음 파일을 에피소드 폴더에 넣고 `episode.toml` 에 `recording = "raw.m4a"` 를 적으세요.
대본·TTS 단계를 건너뛰고 노이즈 제거 → 음량 표준화 → 영상 → 업로드만 진행합니다.
`pip install faster-whisper` 를 해두면 자막도 자동으로 받아씁니다.

## YouTube 연결 (A·B 공통, 최초 1회)

1. [Google Cloud Console](https://console.cloud.google.com/) 에서 프로젝트 생성
2. **API 및 서비스 → 라이브러리** 에서 `YouTube Data API v3` 사용 설정
3. **OAuth 동의 화면** 구성 (외부, 테스트 사용자에 내 구글 계정 추가)
4. **사용자 인증 정보 → OAuth 클라이언트 ID → 데스크톱 앱** 생성 후 JSON 다운로드
5. 파일을 `podcast/secrets/client_secret.json` 으로 저장 (`secrets/` 는 git 에 올라가지 않습니다)
6. 첫 업로드 때 브라우저가 열리면 채널 계정으로 로그인 → 토큰이 저장되어 이후엔 자동

> ⚠️ 알아둘 점
> - **가장 중요:** 구글의 검수를 받지 않은 API 프로젝트로 올린 영상은 **비공개로 고정**됩니다. 이 상태에서는 즉시 공개·예약 공개가 적용되지 않으니, 처음부터 [YouTube API 서비스 검수](https://support.google.com/youtube/contact/yt_api_form)를 신청하세요. 승인 전까지는 업로드된 비공개 영상을 YouTube Studio 에서 직접 공개/예약으로 바꾸면 됩니다 (편집·업로드 자동화는 그대로 쓸 수 있음).
> - 맞춤 썸네일은 채널 **전화번호 인증** 후에 적용됩니다.
> - API 업로드 할당량은 기본 하루 약 6개 영상 수준입니다 (영상 1개 = 1,600 단위 / 하루 10,000). 4단계처럼 하루 업로드가 많아지면 Google Cloud 에서 할당량 증설을 신청하세요.
> - AI 음성(TTS)을 쓴 영상은 유튜브의 **합성 콘텐츠 고지(containsSyntheticMedia)** 를 자동으로 켜고 설명란에도 표시합니다.

## 꾸미기

| 하고 싶은 것 | 방법 |
|---|---|
| 배경음악 | `assets/` 에 mp3 를 넣고 `config.toml [audio] bgm = "파일명.mp3"` (저작권 free 음원 사용) |
| 인트로/아웃트로 징글 | `[audio] intro`, `outro` |
| 배경 이미지 | `assets/` 에 이미지를 넣고 `[video] background` (자동으로 어둡게 처리되어 글자가 잘 보임) |
| 포인트 색 | `[video] accent`, 출연자별 자막 색은 `[[hosts]] color` |
| 목소리 | `edge-tts --list-voices \| grep ko-KR` 로 목록 확인 후 `[[hosts]] voice` |
| 출연자 추가 | `[[hosts]]` 블록 추가 (예: 사회자, 전문가 게스트) |
| 스포티파이·애플 팟캐스트 | `[rss] enabled = true`, `base_url` 지정 → `feed.xml` 생성, 각 `episode.mp3` 를 그 주소에 올리고 피드 URL 을 등록 |
| 인터넷 없이 시험 | `[voice] provider = "mock"` (가짜 톤 음성으로 편집·영상 단계 점검) |

## 폴더 구조

```
podcast/
├── config.toml              # 채널·브랜드·시기별 시간표·업로드 설정
├── strategy/
│   ├── growth-playbook.md   # 시기별 성장 방법론
│   └── brand-guide.md       # 톤앤매너 · 2트랙 · 2형식 · 주간 리듬 (Claude 도 이 파일을 읽음)
├── plan/
│   ├── calendar.csv         # 매일 촬영 계획 (엑셀로 수정 가능)
│   └── build_first_month.py # 첫 30일 계획 원본
├── templates/               # 편집 템플릿 (solo/dialogue × short/long)
├── inbox/                   # 찍은 영상을 넣는 곳 (git 제외)
├── daily/                   # 편집 결과물 · item.json(업로드/예약 기록) (git 제외)
├── research/                # benchmark · listen 결과 (git 제외)
├── assets/                  # 배경음악·배경 이미지·징글
├── secrets/                 # 유튜브 인증 파일 (git 제외)
├── episodes/                # B 모드 에피소드
└── studio/
    ├── cli.py               # python -m studio ...
    ├── footage.py           # 찍은 영상 자동 편집 (A)
    ├── schedule.py          # 단계 판단 · 예약/즉시 업로드 · watch (A)
    ├── plan.py              # 촬영 카드 · 계획 자동 생성 (A)
    ├── research.py          # 비슷한 채널 분석 · 내 댓글 수집 (A)
    ├── script.py · voice.py · edit.py · render.py   # AI 팟캐스트 (B)
    └── publish.py           # YouTube 업로드 공통 + RSS
```

## 좋은 에피소드를 위한 팁

- **`my_view` 가 채널의 정체성입니다.** 완성된 논리가 아니어도 좋으니 솔직한 생각과 직접 겪은 경험을 적으세요. 구체적일수록 대본이 "내 이야기"가 됩니다.
- 대본은 초안입니다. `script.json` 에서 내 말투가 아닌 문장, 과장된 주장, 사실 확인이 필요한 부분을 꼭 손보세요. 고친 대사만 다시 합성되니 부담 없이 수정해도 됩니다.
- 설명란의 "댓글로 생각을 나눠 주세요" 질문에 달린 댓글은 다음 에피소드의 `points` 로 쓰기 좋습니다.

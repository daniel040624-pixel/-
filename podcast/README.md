# 🎙 팟캐스트 스튜디오

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

## 1. 설치 (최초 1회)

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

## 2. 에피소드 만들기 — 매주 이것만 하면 됩니다

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

## 3. YouTube 연결 (최초 1회)

1. [Google Cloud Console](https://console.cloud.google.com/) 에서 프로젝트 생성
2. **API 및 서비스 → 라이브러리** 에서 `YouTube Data API v3` 사용 설정
3. **OAuth 동의 화면** 구성 (외부, 테스트 사용자에 내 구글 계정 추가)
4. **사용자 인증 정보 → OAuth 클라이언트 ID → 데스크톱 앱** 생성 후 JSON 다운로드
5. 파일을 `podcast/secrets/client_secret.json` 으로 저장 (`secrets/` 는 git 에 올라가지 않습니다)
6. 첫 업로드 때 브라우저가 열리면 채널 계정으로 로그인 → 토큰이 저장되어 이후엔 자동

> ⚠️ 알아둘 점
> - 구글의 검수를 받지 않은 API 프로젝트로 올린 영상은 **비공개로 고정**됩니다. 업로드 후 YouTube Studio 에서 직접 공개로 바꾸거나, [API 검수](https://support.google.com/youtube/contact/yt_api_form)를 신청하세요. 기본 설정도 `privacy = "private"` 입니다.
> - 맞춤 썸네일은 채널 **전화번호 인증** 후에 적용됩니다.
> - API 업로드 할당량은 하루 약 6개 영상 수준입니다 (본편 1 + 쇼츠 3 = 4개).
> - AI 음성(TTS)을 쓴 영상은 유튜브의 **합성 콘텐츠 고지(containsSyntheticMedia)** 를 자동으로 켜고 설명란에도 표시합니다.

## 4. 꾸미기

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

## 5. 폴더 구조

```
podcast/
├── config.toml            # 채널 전체 설정
├── requirements.txt
├── assets/                # 배경음악·배경 이미지·징글
├── secrets/               # 유튜브 인증 파일 (git 제외)
├── episodes/
│   └── example/
│       ├── episode.toml   # 주제 + 나의 생각  ← 내가 쓰는 유일한 파일
│       └── build/         # 자동 생성 결과물 (git 제외)
└── studio/
    ├── cli.py             # python -m studio ...
    ├── script.py          # ① 대본 (Claude API)
    ├── voice.py           # ② 음성 (edge-tts)
    ├── edit.py            # ③ 편집 (ffmpeg)
    ├── render.py          # ④ 영상·썸네일 (ffmpeg + Pillow)
    └── publish.py         # ⑤ 업로드 (YouTube Data API) + RSS
```

## 6. 좋은 에피소드를 위한 팁

- **`my_view` 가 채널의 정체성입니다.** 완성된 논리가 아니어도 좋으니 솔직한 생각과 직접 겪은 경험을 적으세요. 구체적일수록 대본이 "내 이야기"가 됩니다.
- 대본은 초안입니다. `script.json` 에서 내 말투가 아닌 문장, 과장된 주장, 사실 확인이 필요한 부분을 꼭 손보세요. 고친 대사만 다시 합성되니 부담 없이 수정해도 됩니다.
- 설명란의 "댓글로 생각을 나눠 주세요" 질문에 달린 댓글은 다음 에피소드의 `points` 로 쓰기 좋습니다.

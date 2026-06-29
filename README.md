# Vertew

시장 상인을 대신해 손님과 **음성으로 대화하는 홀로그램 캐릭터 서비스**. 손님이 디스플레이 앞에서 화면을 탭하고 말을 걸면, 친근한 2D 상인 캐릭터가 표정·몸짓과 함께 음성으로 응답합니다.

> 'street vendors를 위한 기술 창업' 대회 출품작 · 팀 **Eleven-Up**

---

## ✨ 주요 기능

- 🎙️ **핸즈프리 음성 대화** — 탭으로 시작하면 이후엔 버튼 없이 계속 듣고 답합니다. 1분간 말이 없으면 대기 상태로 돌아가요.
- 🗣️ **실시간 자막** — 말하는 내용이 화면에 실시간으로 표시되고, 대화는 채팅 로그로 쌓입니다.
- 🧑‍🎨 **2D 캐릭터** — 표정(기쁨/슬픔/놀람/화남/중립)과 동작(인사/가리킴/끄덕임/생각)을 LLM 응답에 맞춰 표현하고, 말할 때 입이 립싱크됩니다.
- 🌐 **네트워크 절약 설계** — UI·캐릭터·음성합성(TTS)은 전부 로컬에서 처리하고, 인터넷은 음성인식(STT)과 LLM 호출에만 사용합니다.
- 🛠️ **관리자 페이지** — 가게·상품 정보와 캐릭터 페르소나를 입력하면 대화 프롬프트에 반영됩니다.

## 🏗️ 아키텍처

```
[브라우저(Chromium 풀스크린): 캐릭터 렌더링 + STT 캡처 + TTS]
   ↕ localhost WebSocket (인터넷 X)
[FastAPI 서버: 대화 관리 + 프롬프트 구성 + SQLite + 관리자 라우트]
   ↕ 인터넷 (텍스트만)
[LLM (factchat 게이트웨이 또는 Google Gemini)]
```

## 🧱 기술 스택

| 영역 | 기술 |
|------|------|
| 서버 | Python, FastAPI (WebSocket), SQLite |
| LLM | factchat(CNU API Gateway, OpenAI 호환) 또는 Google Gemini |
| 음성 | 브라우저 Web Speech API (STT/TTS) |
| 프론트 | TypeScript, esbuild 번들, vitest |
| 캐릭터 | 인라인 SVG + CSS 애니메이션 |
| 배포 | Chromium 키오스크(풀스크린) — Raspberry Pi 5 |

## 📁 레포 구조

```
vertew/
├── backend/              # FastAPI 서버
│   ├── main.py           # 앱 진입점: WebSocket(/ws) + 정적 파일 + 관리자 라우트
│   ├── conversation.py   # 한 턴 처리 파이프라인
│   ├── prompt_builder.py # 프롬프트 조립
│   ├── llm.py            # LLM 호출(factchat/gemini) + 응답 파싱/폴백
│   ├── profanity.py      # 비속어 필터
│   ├── db.py             # SQLite 저장소
│   ├── admin.py          # 관리자 검증 + /admin 라우트
│   ├── models.py         # 데이터 모델 / 감정·제스처 값
│   ├── requirements.txt
│   └── .env.example      # 환경변수 템플릿 (복사해서 .env 작성)
├── frontend/             # 브라우저 Kiosk_UI
│   ├── index.html        # 손님 화면
│   ├── admin.html        # 관리자 화면
│   ├── css/style.css     # 캐릭터/UI 스타일
│   └── js/               # TypeScript 소스 (speech, character, kiosk, chat, chatlog, main)
├── scripts/kiosk.sh      # Chromium 풀스크린 자동실행 (Pi)
└── .kiro/specs/vertew/   # 요구사항·설계·작업 명세 (requirements / design / tasks)
```

## 🚀 빠른 시작 (로컬 실행)

### 0. 사전 준비
- Python 3.11+ , Node.js 18+
- LLM API 키 (아래 둘 중 하나)
  - **factchat (CNU Gateway)**: CNU API 키
  - **Google Gemini**: [Google AI Studio](https://aistudio.google.com/apikey) 무료 키

### 1. 클론
```bash
git clone https://github.com/Eleven-Up/Vertew.git
cd Vertew
```

### 2. 백엔드
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 환경변수 설정: .env.example 을 .env 로 복사 후 키 입력
copy .env.example .env        # Windows
# cp .env.example .env         # macOS/Linux
```

`.env` 예시 (factchat 사용 시):
```dotenv
LLM_PROVIDER=factchat
FACTCHAT_API_KEY=발급받은_키
FACTCHAT_MODEL=solar-pro3
VERTEW_DB_PATH=vertew.db
```
> `.env` 는 깃에 올라가지 않습니다(.gitignore). **키를 절대 커밋하지 마세요.**

서버 실행:
```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

### 3. 프론트엔드 빌드
새 터미널에서:
```bash
cd frontend
npm install
npm run build        # TypeScript → js/main.js 번들 생성
# 개발 중 자동 재빌드: npm run build:watch
```

### 4. 접속 (크롬 권장)
- 손님 화면: <http://localhost:8000>
- 관리자 화면: <http://localhost:8000/admin>

> 음성인식/합성은 Chrome에서 가장 안정적입니다. 마이크 권한을 허용하세요. 한국어 음성 출력이 어색하면 OS에 한국어 TTS 음성을 추가하세요.

## ⚙️ LLM 설정

`.env` 로 제공자/모델을 바꿉니다.

| 변수 | 설명 |
|------|------|
| `LLM_PROVIDER` | `factchat` 또는 `gemini` |
| `FACTCHAT_API_KEY` / `FACTCHAT_MODEL` | factchat 게이트웨이 키·모델 (예: `solar-pro3`, `gpt-5.4-mini`, `gemini-2.5-flash`) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Gemini 직접 호출용 |
| `LLM_TIMEOUT_SECONDS` | LLM 요청 타임아웃(기본 30) |
| `VERTEW_DB_PATH` | SQLite 파일 경로 |

## 🧪 테스트

```bash
# 백엔드
cd backend && pytest

# 프론트엔드
cd frontend && npm test && npm run typecheck
```

## 👥 팀 협업 워크플로

```bash
# 1) 최신 코드 받기
git pull origin main

# 2) 기능 브랜치에서 작업
git checkout -b feat/내-작업

# 3) 커밋 & 푸시
git add .
git commit -m "설명"
git push -u origin feat/내-작업

# 4) GitHub에서 Pull Request 생성 → 리뷰 → main 병합
```

규칙:
- **`.env`(키)는 커밋 금지** — 이미 `.gitignore`에 포함됨
- `node_modules/`, `.venv/`, `frontend/js/main.js`(빌드 산출물)는 커밋하지 않음 → 받은 사람은 `npm install && npm run build` 로 생성
- 가능하면 `main` 직접 푸시 대신 브랜치 + PR 사용

## 📺 키오스크 배포 (Raspberry Pi)

`scripts/kiosk.sh` 가 Chromium을 풀스크린/키오스크 모드로 자동 실행합니다 (주소창·네비게이션 숨김, 커서 자동 숨김, 실행 재시도). `KIOSK_URL` 환경변수로 대상 URL을 바꿀 수 있어요.

## 📄 명세 문서

요구사항·설계·작업 분해는 `.kiro/specs/vertew/` (requirements.md, design.md, tasks.md) 에 있습니다.

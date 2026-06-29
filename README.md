# Vertew

시장 상인을 대신해 손님과 음성으로 대화하는 홀로그램 캐릭터 서비스. 손님이 홀로그램 디스플레이 앞에서 화면을 탭하고 말을 걸면, 친근한 상인 캐릭터가 표정·몸짓과 함께 음성으로 응답합니다.

> 'street vendors를 위한 기술 창업' 대회 출품작

## 아키텍처

```
[브라우저(Pi, Chromium 풀스크린): 캐릭터 표시 + STT + TTS]
   ↕ localhost (인터넷 X)
[FastAPI 서버(Pi): 대화관리 + 프롬프트 구성 + SQLite + 관리자페이지]
   ↕ 인터넷 (텍스트만)
[Gemini API: LLM 추론]
→ Pi의 HDMI 출력 → 홀로그램 디스플레이(HW팀)
```

네트워크 절약 설계: UI·캐릭터·TTS는 전부 Pi 로컬에서 처리하고, 인터넷은 음성인식(STT)과 Gemini 텍스트 호출에만 사용합니다.

## 기술 스택

- 서버: Python + FastAPI (WebSocket), SQLite
- LLM: Google Gemini Flash (무료 등급)
- STT/TTS: 브라우저 Web Speech API
- 캐릭터: 2D 스프라이트/Live2D
- 키오스크: Chromium 풀스크린

## 레포 구조

```
vertew/
├── backend/      # FastAPI 서버, Gemini 호출, SQLite, 관리자 라우트
├── frontend/     # 손님용 캐릭터 화면, 관리자 화면, STT/TTS
├── scripts/      # kiosk.sh (Chromium 풀스크린 자동실행)
└── .kiro/specs/vertew/   # requirements / design / tasks
```

## 개발

스펙 문서는 `.kiro/specs/vertew/` 에 있습니다 (requirements.md, design.md, tasks.md).

### 백엔드

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate  |  Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
pytest        # 테스트 실행 (Hypothesis 기반 속성 테스트 포함)
```

Gemini API 키는 `backend/.env` 에 설정합니다 (`.env.example` 참고). 키는 절대 커밋하지 마세요.

### 프론트엔드

```bash
cd frontend
npm install
npm test      # fast-check 기반 속성 테스트
```

## 준비물

- Google AI Studio에서 Gemini API 키 발급 (무료)

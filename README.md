# Vertew — Interactive Hologram AI Sales Assistant

Vertew is a low-cost AI sales assistant for street stalls and small vendors. A hologram-style toucan attracts customers, introduces the menu, connects them to mobile QR ordering, sends orders to a vendor dashboard in real time, and announces when an order is ready.

This repository contains the Hologram UI, Customer Ordering Web, Vendor Dashboard, FastAPI backend, WebSocket event bus, session and language management, hardware-free mock controls, and Raspberry Pi/ESP32 adapters.

## English

### End-to-end flow

```text
Customer approaches
  → Greeting video
  → Menu introduction video
  → Speech recognition and language detection
  → QR ordering page
  → Vendor receives the order
  → Accept → Preparing → Ready
  → Customer popup + hologram announcement
  → Hologram returns to Idle after 10 seconds
  → Vendor marks the order Completed
```

### Applications

| Service | Location | Purpose |
|---|---|---|
| Hologram UI | `frontend/` | Character, official videos, STT/TTS, QR, debug controls and ready announcements |
| Customer Web | `apps/customer/` | Mobile menu, language selector, cart, direct ordering and ready popup |
| Vendor Dashboard | `apps/vendor/` | Live orders and status transitions |
| Backend | `backend/` | FastAPI, SQLite, sessions, orders, AI fallback, sensor APIs and WebSockets |

### Current MVP capabilities

- English is the default; English, Korean and Malay are supported.
- Customer-selected language has priority over automatic detection.
- Demo products are Watermelon, Mango, Banana and Apple; menu data is managed by the backend.
- Orders reach the vendor dashboard in real time.
- Statuses: `PENDING`, `ACCEPTED`, `PREPARING`, `READY`, `COMPLETED`, `REJECTED`.
- At `READY`, the customer receives a localized popup that remains until **OK** is pressed.
- The hologram independently displays and speaks the ready message, then closes it after 10 seconds and returns to Idle.
- Missed ready events are recovered through periodic REST checks.
- Official greeting and menu videos run through a non-interrupting event queue.
- Debug Mode simulates sensor, language, QR and order events without Raspberry Pi hardware.
- The demo runs without an AI API key using multilingual rule-based menu, price and ordering responses.
- Gemini or a Factchat-compatible provider can be enabled for richer conversation, with local fallback on failure.

### Known limitations

- Browser STT/TTS depends on microphone permission, browser, OS and installed voices.
- Korean or Malay TTS may use a fallback system voice.
- Without an external AI key, unrestricted generative conversation is not available.
- The character uses a 2D image with CSS motion. Natural joint animation and high-quality lip-sync require aligned transparent layers, Live2D, Spine or dedicated animation assets.
- Payment is mocked: confirming an order immediately creates it.

### Technology

- React, TypeScript, Vite and CSS
- Python, FastAPI, WebSocket and SQLite
- Browser Web Speech API for MVP STT/TTS
- Optional Gemini or OpenAI-compatible LLM provider
- Raspberry Pi / ESP32 sensor adapters

### Local setup

Requirements: Python 3.11+, Node.js 20+ and npm.

```bash
git clone https://github.com/Eleven-Up/vertex_8.17ver.git
cd vertex_8.17ver

python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

cd frontend && npm install && npm run build && cd ..
cd apps/customer && npm install && npm run build && cd ../..
cd apps/vendor && npm install && npm run build && cd ../..

cp backend/.env.example backend/.env
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open:

- Hologram: `http://localhost:8000/?display=hologram`
- Tablet mode: `http://localhost:8000/?display=tablet`
- Customer ordering: `http://localhost:8000/order/store/demo`
- Vendor dashboard: `http://localhost:8000/vendor?store=demo`
- Mock console: `http://localhost:8000/mock.html`

No Raspberry Pi or AI API key is required for the local demo.

### Optional AI configuration

Copy `backend/.env.example` to `backend/.env`. Never commit the real `.env`.

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash
```

Alternatively configure `LLM_PROVIDER=factchat`. When no valid key is present, Vertew uses its local multilingual fallback.

### Main API

| Method | Endpoint |
|---|---|
| `GET` | `/api/stores/{store_id}/menu` |
| `GET` | `/api/stores/{store_id}/media` |
| `POST` | `/api/sessions` |
| `GET` | `/api/sessions/{session_id}` |
| `PATCH` | `/api/sessions/{session_id}/language` |
| `POST` | `/api/orders` |
| `GET` | `/api/orders/{order_id}` |
| `GET` | `/api/stores/{store_id}/orders` |
| `PATCH` | `/api/orders/{order_id}/status` |
| `POST` | `/api/ai/analyze-transcript` |

Store WebSocket:

```text
/ws/store/{store_id}?client=hologram|customer|vendor|debug&session_id=...
```

Important events include `customer_detected`, `customer_close`, `language_detected`, `language_changed`, `new_order`, `order_accepted`, `order_preparing`, `order_ready`, `order_completed` and `order_rejected`.

### Testing

```bash
source .venv/bin/activate
pytest backend -q
cd frontend && npm test -- --run && npm run build
cd ../apps/customer && npm run build
cd ../vendor && npm run build
```

### Repository security

- Keep API keys only in `backend/.env` or deployment environment variables.
- Local databases, virtual environments, `node_modules`, compiled distributions, caches and logs are ignored.
- `.env.example`, lockfiles, source, official media and required runtime character assets are tracked.

---

## 한국어

### 프로젝트 소개

Vertew는 길거리 노점과 소규모 판매자를 위한 저비용 AI 판매 도우미입니다. 홀로그램 형태의 큰부리새 캐릭터가 고객의 관심을 끌고 메뉴를 소개하며, QR 모바일 주문으로 연결하고, 주문을 판매자에게 실시간 전달한 뒤 준비 완료 시 고객과 홀로그램 화면에 알려줍니다.

이 저장소에는 홀로그램 UI, 고객 주문 웹, 판매자 대시보드, FastAPI 백엔드, WebSocket 이벤트 시스템, 세션·언어 관리, 하드웨어 없는 Mock Mode, Raspberry Pi/ESP32 어댑터가 포함되어 있습니다.

### 전체 흐름

```text
고객 접근
  → Greeting 영상
  → 메뉴 소개 영상
  → 음성 인식 및 언어 감지
  → QR 주문 페이지
  → 판매자에게 실시간 주문 전달
  → 승인 → 준비 중 → 준비 완료
  → 고객 팝업 + 홀로그램 안내
  → 10초 후 홀로그램 Idle 복귀
  → 판매자가 주문 완료 처리
```

### 애플리케이션 구성

| 서비스 | 위치 | 역할 |
|---|---|---|
| Hologram UI | `frontend/` | 캐릭터, 공식 영상, STT/TTS, QR, 디버그 제어, 준비 완료 안내 |
| Customer Web | `apps/customer/` | 모바일 메뉴, 언어 선택, 장바구니, 바로 주문, 준비 완료 팝업 |
| Vendor Dashboard | `apps/vendor/` | 실시간 주문 목록과 상태 변경 |
| Backend | `backend/` | FastAPI, SQLite, 세션, 주문, AI Fallback, 센서 API, WebSocket |

### 현재 MVP 기능

- 전체 기본 언어는 영어이며 영어, 한국어, 말레이어를 지원합니다.
- 고객이 선택한 언어가 자동 감지 언어보다 우선합니다.
- 데모 상품은 Watermelon, Mango, Banana, Apple이며 메뉴는 백엔드에서 관리합니다.
- 고객 주문은 Vendor Dashboard에 실시간으로 전달됩니다.
- 주문 상태는 `PENDING`, `ACCEPTED`, `PREPARING`, `READY`, `COMPLETED`, `REJECTED`입니다.
- 주문이 `READY`가 되면 고객 페이지에 현재 언어로 팝업이 표시되고 **확인**을 누를 때까지 유지됩니다.
- 홀로그램 알림은 고객 확인 버튼과 독립적으로 동작하며 10초 후 종료되고 Idle로 복귀합니다.
- WebSocket 이벤트를 놓쳐도 REST 상태 조회로 준비 완료 알림을 복구합니다.
- Greeting 영상과 메뉴 소개 영상은 중단되지 않는 이벤트 큐에서 순차 재생됩니다.
- Raspberry Pi 없이 Debug Mode에서 센서, 언어, QR, 주문 이벤트를 테스트할 수 있습니다.
- AI API Key가 없어도 메뉴·가격·주문 의도에 관한 다국어 규칙 기반 응답으로 데모가 작동합니다.
- Gemini 또는 Factchat 호환 Provider를 연결하면 더 자연스러운 대화를 사용할 수 있으며 장애 시 로컬 응답으로 전환됩니다.

### 현재 한계

- 브라우저 음성 인식과 음성 출력은 마이크 권한, 브라우저, 운영체제, 설치된 음성에 영향을 받습니다.
- 한국어 또는 말레이어 음성이 없으면 시스템 기본 음성으로 출력될 수 있습니다.
- 외부 AI Key가 없으면 자유로운 생성형 대화는 지원되지 않습니다.
- 캐릭터는 현재 2D 이미지와 CSS 움직임을 사용합니다. 자연스러운 관절 움직임과 고품질 립싱크에는 정렬된 투명 레이어, Live2D, Spine 또는 별도 애니메이션 자산이 필요합니다.
- 실제 결제는 구현하지 않았으며 주문 확인 시 바로 주문이 생성됩니다.

### 기술 스택

- React, TypeScript, Vite, CSS
- Python, FastAPI, WebSocket, SQLite
- MVP STT/TTS용 Browser Web Speech API
- 선택형 Gemini 또는 OpenAI 호환 LLM Provider
- Raspberry Pi / ESP32 센서 어댑터

### 로컬 실행

필수 환경: Python 3.11 이상, Node.js 20 이상, npm.

```bash
git clone https://github.com/Eleven-Up/vertex_8.17ver.git
cd vertex_8.17ver

python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

cd frontend && npm install && npm run build && cd ..
cd apps/customer && npm install && npm run build && cd ../..
cd apps/vendor && npm install && npm run build && cd ../..

cp backend/.env.example backend/.env
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

접속 주소:

- 홀로그램: `http://localhost:8000/?display=hologram`
- 태블릿 모드: `http://localhost:8000/?display=tablet`
- 고객 주문: `http://localhost:8000/order/store/demo`
- 판매자 대시보드: `http://localhost:8000/vendor?store=demo`
- Mock Console: `http://localhost:8000/mock.html`

로컬 데모에는 Raspberry Pi나 AI API Key가 필요하지 않습니다.

### 선택형 AI 설정

`backend/.env.example`을 `backend/.env`로 복사합니다. 실제 `.env`는 Git에 커밋하지 않습니다.

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash
```

또는 `LLM_PROVIDER=factchat`을 설정할 수 있습니다. 유효한 Key가 없으면 로컬 다국어 Fallback을 사용합니다.

### 주요 API

| Method | Endpoint |
|---|---|
| `GET` | `/api/stores/{store_id}/menu` |
| `GET` | `/api/stores/{store_id}/media` |
| `POST` | `/api/sessions` |
| `GET` | `/api/sessions/{session_id}` |
| `PATCH` | `/api/sessions/{session_id}/language` |
| `POST` | `/api/orders` |
| `GET` | `/api/orders/{order_id}` |
| `GET` | `/api/stores/{store_id}/orders` |
| `PATCH` | `/api/orders/{order_id}/status` |
| `POST` | `/api/ai/analyze-transcript` |

매장 WebSocket:

```text
/ws/store/{store_id}?client=hologram|customer|vendor|debug&session_id=...
```

주요 이벤트는 `customer_detected`, `customer_close`, `language_detected`, `language_changed`, `new_order`, `order_accepted`, `order_preparing`, `order_ready`, `order_completed`, `order_rejected`입니다.

### 테스트

```bash
source .venv/bin/activate
pytest backend -q
cd frontend && npm test -- --run && npm run build
cd ../apps/customer && npm run build
cd ../vendor && npm run build
```

### 보안 및 저장소 관리

- API Key와 Secret은 `backend/.env` 또는 배포 환경변수에만 저장합니다.
- 로컬 DB, 가상환경, `node_modules`, 빌드 결과, 캐시, 로그는 Git에서 제외합니다.
- `.env.example`, lockfile, 소스 코드, 공식 영상과 실행에 필요한 캐릭터 자산은 Git에 포함합니다.

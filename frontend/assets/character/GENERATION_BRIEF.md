# Vertew 캐릭터 PNG 생성 브리프 (anime.js 리그용)

기존 자산(`assets/official/vertew-character-reference.png`, `assets/character/processed/*.png`)에서
확인한 스타일 — **네온 글로우 라인아트 호랑이부리새(hornbill)**, 투명 배경 — 을 그대로 유지한다.
현재 `js/character.ts` + `css/style.css`는 손그림 SVG 인체 캐릭터를 CSS 클래스로 움직이는 방식인데,
이걸 **PNG 레이어 + anime.js**로 교체하기 위한 자산 목록이다.

## 구조: 2-레이어 (Body pose + Emotion accent)

25개(emotion 5 × gesture 5) 조합을 전부 그릴 필요 없다. 기존 자산이 이미 이 패턴을 쓰고 있었다:

- **Body layer** — `gesture` 값 하나당 전신 포즈 PNG 1장 (표정은 중립/차분하게, 몸짓으로만 구분)
- **Accent layer** — `emotion` 값 하나당 머리 위/옆에 뜨는 작은 이펙트 아이콘 PNG 1장 (기존 3장 재사용 + 신규 2장)

이렇게 하면 두 레이어를 **독립적으로 생성한 래스터를 픽셀 단위로 맞출 필요 없이** (accent 아이콘은 위치 오차에 관대한 작은 소품이라 CSS로 대충 배치해도 자연스러움) anime.js가 두 레이어의 교체/등장 애니메이션만 담당하면 된다.

| 축 | 담당 레이어 | 개수 |
|---|---|---|
| `gesture` (idle/wave/point/nod/think) | Body pose PNG | 5장 + 립싱크용 1장 |
| `emotion` (happy/neutral/surprised/sad/angry) | Accent 아이콘 PNG | neutral 제외 4장 (기존 3장 재사용 + 신규 1장) |

`neutral`은 액센트 없음(기본 상태), `think` 제스처는 기존 `question.png`를 추가로 겹쳐서 강조(선택).

---

## 필요한 파일 목록

`frontend/assets/character/processed/` 에 저장.

### A. Body pose (신규 생성 필요, 6장)

| 파일명 | 용도 |
|---|---|
| `pose_idle.png` | 기본 대기 포즈 (부리 다문 상태) |
| `pose_idle_talk.png` | `pose_idle`과 동일 구도, 부리만 살짝 벌어짐 (립싱크 토글용) |
| `pose_wave.png` | 날개 들어 흔드는 포즈 |
| `pose_point.png` | 날개로 가리키는 포즈 |
| `pose_nod.png` | 고개를 끄덕이듯 살짝 숙인 포즈 |
| `pose_think.png` | 날개를 부리/턱 쪽에 대고 고민하는 포즈 |

### B. Emotion accent (기존 3장 재사용 + 신규 2장)

| 파일명 | 상태 | 매핑 |
|---|---|---|
| `fruit_halo.png` | ✅ 이미 있음 (재사용) | `emotion: happy` |
| `sweat.png` | ✅ 이미 있음 (재사용) | `emotion: sad` |
| `question.png` | ✅ 이미 있음 (재사용) | `gesture: think` 강조용 (감정과 별개) |
| `accent_surprised.png` | 🆕 신규 생성 | `emotion: surprised` |
| `accent_angry.png` | 🆕 신규 생성 | `emotion: angry` |

`neutral`은 액센트 이미지 없음.

**총 8장만 새로 생성하면 됨** (body 6 + accent 2).

---

## 스타일 고정 블록 (모든 프롬프트에 그대로 붙여넣기)

캐릭터 정체성과 화풍이 흔들리면 포즈끼리 이질감이 생기므로, 아래 블록을 모든 프롬프트 맨 앞에 **토씨 하나 안 바꾸고** 붙인다. 가능하면 같은 대화/세션에서 이전 결과물을 참조하며 연달아 생성해서 일관성을 더 높인다.

```
STYLE LOCK — Vertew mascot, neon glow line-art hornbill bird character.
Species: stylized hornbill/toucan hybrid with a large curved casque (crest) that
fades from magenta-pink at the base to warm orange at the tip, like a flame
shape swept backward. Long drooping banana-shaped orange-yellow beak with a
thin dark ridge line. Body is a solid black silhouette outlined by a bright
double-stroke cyan neon glow line (like a neon sign tube), no interior fill
color except the beak/crest gradient. Simple round white eye with a black
pupil and a thin pink neon ring around it, one small black comma-shaped brow
mark above the eye. Small pink neon accent line on the throat where the beak
meets the body. Two-toed cyan-outlined feet gripping a thin branch/perch.
Forked tail feathers, cyan outline only.
Background: fully transparent (PNG alpha, no black backdrop, no scene).
Composition: character centered in a square canvas, perched on the same thin
horizontal branch positioned at the same height (about 15% up from the bottom
edge), character fills roughly 55-65% of the frame height, same camera
distance and angle as the reference across all poses — do not zoom or
reframe between generations.
Rendering: flat vector-style line art, uniform line weight, soft outer glow
bloom on the cyan lines only, no shading, no gradients on the body, no
background glow/vignette, no other characters, no text, no watermark, not
photorealistic.
```

---

## Body pose 프롬프트

### 1. `pose_idle.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: calm resting pose, wings folded against the body, head facing slightly
forward with a gentle relaxed posture, beak fully closed, standing/perched
naturally on the branch. This is the default idle frame.
```

### 2. `pose_idle_talk.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: identical framing, body position, wing position, and head angle to
pose_idle — the ONLY difference is the beak is slightly open (a small dark
gap between upper and lower mandible), as if mid-speech. Everything else
pixel-for-pixel the same pose as pose_idle.
```

### 3. `pose_wave.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: one wing raised up and outward in a friendly waving motion (like
greeting a customer), the other wing stays folded at the side, head tilted
slightly toward the raised wing with a welcoming posture, beak closed.
```

### 4. `pose_point.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: one wing extended forward/outward with the wingtip clearly pointing
toward something off to the side (as if indicating a menu item), the other
wing folded, head turned slightly to follow the direction of the pointing
wing, beak closed.
```

### 5. `pose_nod.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: head dipped downward and slightly forward in a nodding/agreeing
posture, both wings folded calmly at the sides, slight forward lean of the
whole body as if in the middle of a nod, beak closed.
```

### 6. `pose_think.png`
```
STYLE LOCK (위 블록 그대로) +
POSE: one wingtip raised and touching near the side of the beak/chin in a
classic "thinking" gesture, head tilted to one side with a curious
expression, the other wing folded at the side, beak closed.
```

---

## Emotion accent 프롬프트 (신규 2장)

액센트는 **캐릭터 없이 소품/이펙트만** 투명 배경에 그린다 (기존 `question.png`, `fruit_halo.png`, `sweat.png`와 동일 형식). 캐릭터 머리 위쪽에 CSS로 얹을 것이므로 정사각형 캔버스 중앙에 아이콘만 배치.

### 7. `accent_surprised.png`
```
Neon glow line-art icon, transparent background, matching the Vertew mascot's
neon style (bright cyan double-stroke glow lines, occasional warm
yellow/orange accent, no fill, soft outer bloom, flat vector line art, no
character, no text, no watermark).
ICON: a small burst of 4-5 radiating spark/light-ray lines exploding outward
from a center point, plus a single bold exclamation mark ("!") to the side,
conveying a sudden "surprised/startled" reaction. Compact composition,
centered, sized to sit just above a character's head.
```

### 8. `accent_angry.png`
```
Neon glow line-art icon, transparent background, matching the Vertew mascot's
neon style (bright cyan double-stroke glow lines, one accent in warm
red-orange, no fill, soft outer bloom, flat vector line art, no character, no
text, no watermark).
ICON: a classic cartoon "anger mark" — a cross-shaped bulging vein symbol
(two crossing curved lines forming a plus/cross shape), with a couple of
small steam/heat lines rising above it, conveying a mild comic "annoyed/angry"
reaction (cute, not scary). Compact composition, centered, sized to sit just
above a character's head.
```

---

## anime.js 연결 방식 (참고용, 구현 시)

`js/character.ts`의 렌더링 로직을 SVG 클래스 토글 → **이미지 스와프 + 액센트 오버레이**로 교체:

- `#character` 안에 2개의 절대배치 레이어:
  - `<img class="char-body">` — `pose_*.png` 중 하나 (gesture로 결정)
  - `<img class="char-accent">` — `accent_*.png`/`fruit_halo.png`/`sweat.png`/`question.png` 중 하나 (emotion으로 결정, `neutral`이면 `display:none`)
- `render(emotion, gesture)` 호출 시:
  - body 이미지 src 교체 → anime.js로 150–250ms 크로스페이드(opacity 0→1) + 살짝 scale bounce (`1.05 → 1`, overshoot easing)
  - accent 이미지 교체/표시 → anime.js로 위에서 살짝 떨어지며 팝인 (`translateY(-10px)+scale(0.6)` → 원위치, `easeOutBack`)
  - `gesture: think`일 때는 accent 레이어에 `question.png`를 emotion 액센트 대신(또는 함께, 작게 겹쳐) 표시
- `playIdle()`: body 레이어 wrapper에 anime.js 무한 루프 timeline 적용 — 미세한 `translateY` bounce(숨쉬기) + 아주 약한 `rotate` sway, 별도 이미지 불필요 (지금 `character-idle-loop` CSS 애니메이션과 동일한 역할, 순수 transform이라 이미지 추가 없이 재사용 가능)
- 립싱크: `startLipSync()`/`stopLipSync()`가 `pose_idle.png` ↔ `pose_idle_talk.png`를 `LIPSYNC_FRAME_MS`(120ms) 간격으로 토글. 단, `gesture`가 idle이 아닐 때(wave/point/nod/think 중)는 굳이 부리 토글 없이 해당 포즈를 그대로 유지 — Turtle Talk with Crush도 입모양보다 몸짓 연기 위주라 이 정도 단순화면 충분함.

## 체크리스트

- [ ] 8장 신규 생성 (`pose_idle`, `pose_idle_talk`, `pose_wave`, `pose_point`, `pose_nod`, `pose_think`, `accent_surprised`, `accent_angry`)
- [ ] 전부 정사각형, 투명 배경(PNG-24 with alpha) 확인
- [ ] `pose_*` 6장을 한 화면에 겹쳐놓고 봤을 때 캐릭터 크기/위치/perch 높이가 흔들리지 않는지 육안 확인 (흔들리면 크롭/리사이즈로 보정)
- [ ] `frontend/assets/character/processed/`에 위 파일명 그대로 저장

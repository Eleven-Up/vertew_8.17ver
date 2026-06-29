# Implementation Plan: Vertew

## Overview

This plan builds the Vertew service bottom-up: first the deterministic, pure-function Python core of the Conversation_Server (data models, prompt building, response parsing, normalization, profanity filtering, persistence), then the server orchestration and endpoints, then the TypeScript browser layer (Speech_Module, Kiosk_UI state machine, Character_Renderer), and finally wiring and kiosk deployment.

Each correctness property from the design is implemented as a single property-based test (Hypothesis for Python, fast-check for TypeScript), placed next to the code it validates. Tasks build incrementally and end by wiring components together, leaving no orphaned code.

- Server language: **Python** (FastAPI, Hypothesis for PBT)
- Browser language: **TypeScript** (Web Speech API, fast-check for PBT)

## Tasks

- [x] 1. Set up project structure and test frameworks
  - Create `backend/` (main.py, llm.py, prompt_builder.py, profanity.py, db.py, admin.py, models.py, requirements.txt) and `frontend/` (index.html, admin.html, js/, assets/) directories per the design repo structure
  - Add `scripts/kiosk.sh` placeholder and project README
  - Configure Python testing with **Hypothesis** + pytest in `backend/`
  - Configure TypeScript testing with **fast-check** + a test runner in `frontend/`
  - Do not implement testing from scratch; use the established libraries
  - _Requirements: 9.1, 9.2_

- [x] 2. Implement core data models, value sets, and normalization
  - [x] 2.1 Define data models and allowed value sets
    - Implement `CharacterResponse`, `StoreInfo`, `ConversationTurn` dataclasses
    - Define `EMOTIONS`, `GESTURES`, `DEFAULT_EMOTION`, `DEFAULT_GESTURE` as the single source of truth
    - Implement `normalize_emotion` / `normalize_gesture` returning the value when in-set and `neutral`/`idle` when missing, empty, or out-of-set
    - _Requirements: 4.6, 5.2, 5.6_

  - [ ]* 2.2 Write property test for emotion/gesture normalization
    - **Property 7: Emotion and gesture normalization is total**
    - **Validates: Requirements 4.6, 5.2, 5.6**
    - Use generators mixing in-set values with arbitrary/empty strings; assert every allowed value maps to exactly one defined expression/motion

- [x] 3. Implement Prompt_Builder
  - [x] 3.1 Implement prompt assembly
    - Implement `build(persona, store_info, recent_turns, transcript)` assembling persona, current store/product info, transcript, and fixed instructions (JSON-only schema with non-empty text ≤500 chars, allowed emotion/gesture sets, grade-8 reading level, no-profanity, polite vendor tone)
    - Clamp recent turns to the last 5 in chronological order
    - When no store info exists, instruct the model to state no product info is available while still including persona and recent turns
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 7.6, 7.7, 10.3, 10.4_

  - [ ]* 3.2 Write property test for required prompt components and instructions
    - **Property 1: Prompt contains all required components and instructions**
    - **Validates: Requirements 3.1, 3.4, 3.5, 7.6, 7.7, 10.3, 10.4**

  - [ ]* 3.3 Write property test for absent store information
    - **Property 2: Absent store information yields a no-product-info instruction**
    - **Validates: Requirements 3.3**

  - [ ]* 3.4 Write property test for recent-turn clamping and order
    - **Property 3: Recent-turn history is clamped to the five most recent in order**
    - **Validates: Requirements 3.2**

- [x] 4. Implement LLM_Client parsing and Gemini call
  - [x] 4.1 Implement response parsing and fallbacks
    - Implement `parse(raw)`: valid JSON with non-empty text/emotion/gesture extracts fields and truncates text to ≤1000 chars
    - Invalid/missing-field responses return `FALLBACK_UNDERSTAND` (neutral/idle); define fallback constants
    - _Requirements: 4.2, 4.4_

  - [ ]* 4.2 Write property test for valid response parsing
    - **Property 4: Valid response parsing extracts fields and bounds text length**
    - **Validates: Requirements 4.2**

  - [ ]* 4.3 Write property test for malformed/incomplete response fallback
    - **Property 5: Malformed or incomplete responses become the neutral understanding fallback**
    - **Validates: Requirements 4.4**

  - [x] 4.4 Implement Gemini Flash call with timeout, single-call, and retry
    - Implement `complete(prompt)` behind an HTTP boundary interface: exactly one logical Gemini call per turn, 10s timeout, at most one retry on failure/timeout
    - _Requirements: 4.1, 9.3, 9.4, 9.6_

  - [ ]* 4.5 Write property test for single Gemini call per turn
    - **Property 12: Exactly one Gemini call per conversation turn**
    - **Validates: Requirements 9.3**
    - Mock the Gemini HTTP boundary and assert call count is exactly 1

  - [ ]* 4.6 Write property test for at-most-one network retry
    - **Property 11: Network requests retry at most once**
    - **Validates: Requirements 9.6**
    - Mock failing/timing-out requests; assert at most two attempts before reporting failure

- [x] 5. Implement ProfanityFilter
  - [x] 5.1 Implement profanity filtering
    - Implement `apply(resp)`: if text matches any configured term, replace the whole response with `FALLBACK_COURTEOUS` (which contains no flagged terms); otherwise return unchanged
    - _Requirements: 10.1, 10.2_

  - [ ]* 5.2 Write property test for profanity replacement and clean passthrough
    - **Property 8: Profanity filtering replaces flagged responses and preserves clean ones**
    - **Validates: Requirements 10.1, 10.2**
    - Use generators embedding configured terms at varying positions plus clean-text generators

- [x] 6. Implement Data_Store repository and SQLite persistence
  - [x] 6.1 Implement schema and repository operations
    - Create SQLite schema (`store_info` single-row, `conversation_turn` insert-only)
    - Implement `record_turn` (customer text, response text, completion timestamp) with at most one retry on write failure, and single-row upsert for store info
    - Implement startup load of store info; on load failure expose a flag that blocks new turns
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 6.2 Write property test for persistence round-trip across restarts
    - **Property 9: Persistence round-trip preserves committed records across restarts**
    - **Validates: Requirements 8.1, 8.2, 8.5**
    - Use a real temporary on-disk SQLite file and reopen it to exercise true persistence

  - [ ]* 6.3 Write property test for at-most-one storage write retry
    - **Property 10: Storage writes retry at most once**
    - **Validates: Requirements 8.3**
    - Mock failing writes; assert at most two attempts and that prior committed records remain unchanged

  - [ ]* 6.4 Write unit test for startup load failure blocking turns
    - Test that a failed startup load presents an error indication and blocks new turns
    - _Requirements: 8.4_

- [x] 7. Implement Admin_Interface validation and routes
  - [x] 7.1 Implement store-info validation
    - Validate store/product fields (1–2000 chars, required) and persona (1–500 chars when set); reject invalid submissions while identifying the invalid field
    - _Requirements: 7.3, 7.4_

  - [ ]* 7.2 Write property test for store-info validation
    - **Property 16: Store-info validation accepts only well-formed fields**
    - **Validates: Requirements 7.3, 7.4**

  - [x] 7.3 Implement `/admin` HTTP routes
    - Serve current store/product info (empty fields when none exist), accept submissions, persist via Data_Store, return confirmation; on storage failure retain entered values and show save error
    - _Requirements: 7.1, 7.2, 7.5, 7.6_

  - [ ]* 7.4 Write unit tests for admin display and save paths
    - Test display-within-time, empty-field display, confirmation, and storage-failure retention paths
    - _Requirements: 7.1, 7.2, 7.5_

- [x] 8. Implement Conversation_Server orchestration and endpoints
  - [x] 8.1 Implement transcript handling pipeline
    - Wire `handle_transcript`: Prompt_Builder → LLM_Client.complete → parse → ProfanityFilter → Data_Store.record_turn → return response
    - On Gemini unreachable/error/timeout, return `FALLBACK_UNAVAILABLE` (neutral/idle); abort with no partial output
    - _Requirements: 4.3, 4.5, 9.5_

  - [ ]* 8.2 Write property test for network-failure fallback
    - **Property 6: Network failures become the temporarily-unavailable fallback**
    - **Validates: Requirements 4.5**
    - Mock unreachable/error/timeout Gemini responses; assert temporarily-unavailable fallback with neutral/idle

  - [x] 8.3 Implement WebSocket and wiring to admin app
    - Expose the localhost WebSocket for the conversation loop and mount the admin routes in `main.py`
    - _Requirements: 9.1, 9.2_

  - [ ]* 8.4 Write integration tests for network constraints
    - Verify single Gemini call with 10s timeout (4.1, 9.4) and that the server's outbound requests are limited to STT + Gemini (9.2)
    - _Requirements: 4.1, 9.2, 9.4_

- [x] 9. Checkpoint - server core complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Implement browser Speech_Module
  - [x] 10.1 Implement STT provider and TTS engine behind interfaces
    - Implement `SttProvider` (mic start/stop, end-of-speech, no-match, error) and `TtsEngine` (local English synthesis, availability) so the STT backend is replaceable without changing UI or server
    - Forward only transcripts with ≥1 recognized word; skip synthesis for empty/whitespace text
    - Apply capture timing constants (500ms activation, 200ms indicator, 2–3s silence, 10s no-speech, 30s max, 5s STT result)
    - _Requirements: 2.1, 2.2, 2.3, 2.6, 6.1, 6.2, 6.3, 6.7_

  - [ ]* 10.2 Write property test for non-empty transcript/text gating
    - **Property 14: Only non-empty transcripts/texts proceed; whitespace-only is skipped**
    - **Validates: Requirements 2.3, 6.7**

  - [ ]* 10.3 Write unit/integration tests for STT/TTS boundaries
    - Test STT timeout path (2.5), STT conversion latency (2.1), TTS issuing zero network requests (6.2), English voice configured (6.3), provider behind replaceable interface (2.6)
    - _Requirements: 2.1, 2.5, 2.6, 6.2, 6.3_

- [x] 11. Implement Kiosk_UI state machine
  - [x] 11.1 Implement the conversation state machine and tap handling
    - Implement states idle/listening/processing/speaking/error with transitions per the design; honor taps only in idle, ignore in listening and speaking
    - Show listening indicator within 200ms and keep visible while mic active; return to idle within 1s after voice output
    - Handle error banners for mic-unavailable, STT empty/retry (≤3), network, and TTS failures with defined recovery states
    - _Requirements: 1.1, 1.3, 1.5, 1.7, 1.8, 2.4, 2.5, 6.4, 6.5, 6.6_

  - [ ]* 11.2 Write property test for tap handling restricted to idle
    - **Property 13: Screen taps are honored only in the idle state**
    - **Validates: Requirements 1.5, 6.4**

  - [ ]* 11.3 Write property test for STT empty/no-match retry cap
    - **Property 15: Empty/no-match STT retries are capped at three**
    - **Validates: Requirements 2.4**

  - [ ]* 11.4 Write unit tests for timing and timeout behavior
    - Test mic activation/indicator latency (1.2, 1.3), silence/max-capture/no-speech timers (1.4, 1.6, 1.7), TTS start and return-to-idle (6.1, 6.5, 6.6)
    - _Requirements: 1.2, 1.3, 1.4, 1.6, 1.7, 6.1, 6.5, 6.6_

- [x] 12. Implement Character_Renderer
  - [x] 12.1 Implement 2D character rendering
    - Render the single 2D character; map each emotion to one expression and each gesture to one motion, normalizing unknown/empty to neutral/idle without interrupting rendering
    - Loop idle animation when no conversation is active; start/stop lip-sync within 150ms of voice output
    - Begin rendering a recognized response within 500ms of receipt
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 12.2 Write unit tests for rendering, idle, and lip-sync timing
    - Test idle animation (5.3), lip-sync start/stop timing (5.4, 5.5), and unknown-value normalization rendering (5.2)
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

- [x] 13. Wire browser components and connect to the server
  - [x] 13.1 Integrate Speech_Module, Kiosk_UI, and Character_Renderer over localhost
    - Implement `chat.js` to send transcripts and receive `{text, emotion, gesture}` over the localhost WebSocket; route responses to Character_Renderer and Speech_Module; ignore taps during TTS
    - Ensure the Kiosk_UI issues no internet requests
    - _Requirements: 4.3, 5.1, 6.4, 9.1_

  - [ ]* 13.2 Write integration test for localhost-only communication
    - Verify the Kiosk_UI talks only to localhost and issues no internet requests (9.1)
    - _Requirements: 9.1_

- [x] 14. Implement kiosk deployment script
  - [x] 14.1 Implement Chromium fullscreen autostart
    - Implement `scripts/kiosk.sh` to launch Chromium in fullscreen/kiosk mode hiding navigation, address bar, and cursor after 5s idle; retry launch up to 3 times then show an on-screen failure indication
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ]* 14.2 Write smoke/config test for kiosk flags
    - Verify autostart fullscreen flags hide navigation/address bar (11.1, 11.3) and launch-retry behavior (11.5)
    - _Requirements: 11.1, 11.3, 11.5_

- [x] 15. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP.
- Each task references specific requirements clauses for traceability.
- Property tests validate universal correctness properties; each property is implemented as a single property-based test (Hypothesis for Python, fast-check for TypeScript), tagged with a comment in the format **Feature: vertew, Property {number}: {property_text}**, and run for a minimum of 100 iterations.
- External boundaries (Gemini HTTP, SQLite, Web Speech API) are mocked in property tests, except Property 9 which uses a real temporary on-disk SQLite file.
- Unit, integration, and smoke tests cover timing, external-service wiring, and deployment criteria that are not input-varying.
- Checkpoints ensure incremental validation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "4.1", "5.1", "6.1", "7.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "4.2", "4.3", "4.4", "5.2", "6.2", "6.3", "6.4", "7.2", "7.3"] },
    { "id": 4, "tasks": ["4.5", "4.6", "7.4", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3"] },
    { "id": 6, "tasks": ["8.4", "10.1", "12.1"] },
    { "id": 7, "tasks": ["10.2", "10.3", "11.1", "12.2"] },
    { "id": 8, "tasks": ["11.2", "11.3", "11.4", "13.1"] },
    { "id": 9, "tasks": ["13.2", "14.1"] },
    { "id": 10, "tasks": ["14.2"] }
  ]
}
```

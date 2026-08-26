// Shared browser-side types for the Vertew Kiosk_UI.
// Scaffolding only. Concrete logic is implemented in later tasks (10-13).

// "understanding" (mic closed, converting the recorded speech to text) and
// "thinking" (transcript sent to the server, waiting for its reply) split what
// used to be a single opaque "processing" state so the customer sees which of
// the two is happening; "ready" is the brief beat once the reply has arrived
// but before the character actually starts talking.
export type UIState = "idle" | "listening" | "understanding" | "thinking" | "ready" | "speaking" | "error";

// ---------------------------------------------------------------------------
// Allowed Emotion/Gesture value sets — the browser-side mirror of the server's
// single source of truth (backend/models.py). Each named value maps to exactly
// one facial expression / body motion of the single 2D character (Req 5.6).
// ---------------------------------------------------------------------------
export const EMOTIONS = ["happy", "neutral", "surprised", "sad", "angry"] as const;
export const GESTURES = ["wave", "idle", "point", "nod", "think", "fly", "jump", "approach"] as const;

export type Emotion = (typeof EMOTIONS)[number];
export type Gesture = (typeof GESTURES)[number];

export const DEFAULT_EMOTION: Emotion = "neutral";
export const DEFAULT_GESTURE: Gesture = "idle";

export interface CharacterResponse {
  text: string;
  emotion: string;
  gesture: string;
}

export type SttResult =
  | {
      kind: "transcript";
      text: string;
      detectedLanguage?: string;
      languageConfidence?: number;
    }
  | { kind: "no-match" }
  | { kind: "error"; reason: "network" | "timeout" | "mic-unavailable" };

// Speech_Module provider interfaces. The STT backend lives behind SttProvider so
// it can be replaced without touching the Kiosk_UI or Conversation_Server (Req 2.6).
export interface SttProvider {
  start(): void; // activate mic, begin capture
  stop(): void; // deactivate mic
  // Registers a callback invoked on end-of-speech, no-match, or error.
  onResult(cb: (r: SttResult) => void): void;
  // Optional: registers a callback for live interim transcripts emitted while the
  // user is still speaking, so the UI can show speech-to-text in real time.
  onPartial?(cb: (text: string) => void): void;
  // Optional: registers a callback fired once capture has ended and the
  // provider is turning the recorded audio into text (before the transcript
  // is known) -- e.g. while LocalSttProvider's POST to /api/stt/transcribe is
  // in flight -- so the UI can show an "understanding" state distinct from
  // "listening" instead of appearing to freeze once the mic closes.
  onTranscribing?(cb: () => void): void;
  // Switches the recognized/spoken language (e.g. "ko-KR") for the next capture.
  setLanguage(lang: string): void;
}

// Local (in-browser) text-to-speech engine. No data leaves the device (Req 6.2).
export interface TtsEngine {
  speak(text: string): Promise<void>; // local synthesis only; resolves when complete
  isAvailable(): boolean;
}

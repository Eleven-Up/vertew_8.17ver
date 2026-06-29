// Speech_Module: STT capture + local TTS behind a replaceable provider interface.
//
// The STT backend is a Web Speech API implementation hidden behind SttProvider so it
// can be swapped without touching the Kiosk_UI or Conversation_Server (Req 2.6). Local
// English speech synthesis runs entirely in-browser with no network traffic (Req 6.2/6.3).

import type { SttProvider, SttResult, TtsEngine } from "./types";

// ---------------------------------------------------------------------------
// Capture timing constants
//
// Each value is documented with the requirement it satisfies. These are exported so
// the Kiosk_UI state machine (task 11) and tests (10.2/10.3) share a single source of
// truth for capture/synthesis timing.
// ---------------------------------------------------------------------------

/** Microphone must activate within this budget after a tap (Req 1.2). */
export const MIC_ACTIVATION_MS = 500;

/** Listening indicator must appear within this budget after mic activation (Req 1.3). */
export const LISTENING_INDICATOR_MS = 200;

/**
 * Continuous silence that marks end-of-utterance. Requirement 1.4 specifies 2s and
 * Requirement 2.2 specifies 3s; we use the upper bound of the 2-3s window so a brief
 * pause is not mistaken for the end of speech (Req 1.4 / Req 2.2).
 */
export const END_OF_SPEECH_SILENCE_MS = 3000;

/** If no speech is detected after activation, deactivate the mic (Req 1.7). */
export const NO_SPEECH_TIMEOUT_MS = 10000;

/** Hard cap on a single capture; audio so far is treated as a finished utterance (Req 1.6). */
export const MAX_CAPTURE_MS = 30000;

/** A transcript must come back within this budget after end-of-speech (Req 2.1 / 2.5). */
export const STT_RESULT_TIMEOUT_MS = 5000;

// ---------------------------------------------------------------------------
// Pure gating helpers (exported for reuse and property testing in 10.2/10.3)
// ---------------------------------------------------------------------------

/**
 * True when the transcript contains at least one recognized word. A "word" is any
 * run of non-whitespace characters, so whitespace-only or empty strings are rejected.
 * Used to forward only meaningful transcripts to the server (Req 2.3).
 */
export function hasRecognizedWord(text: string): boolean {
  return /\S/.test(text);
}

/**
 * True when a transcript should be forwarded to the Conversation_Server. Alias of
 * hasRecognizedWord kept for readability at call sites (Req 2.3).
 */
export function isNonEmptyTranscript(text: string): boolean {
  return hasRecognizedWord(text);
}

/**
 * True when response text is worth synthesizing. Empty or whitespace-only text is
 * skipped so the UI can return straight to idle (Req 6.7).
 */
export function isNonEmptyText(text: string): boolean {
  return /\S/.test(text);
}

// ---------------------------------------------------------------------------
// Minimal Web Speech API type declarations
//
// SpeechRecognition is not part of every lib.dom configuration, so the subset used
// here is declared locally. These describe the browser API surface; they add no
// runtime behavior.
// ---------------------------------------------------------------------------

interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
  readonly confidence: number;
}

interface SpeechRecognitionResultLike {
  readonly length: number;
  readonly isFinal: boolean;
  item(index: number): SpeechRecognitionAlternativeLike;
  [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  item(index: number): SpeechRecognitionResultLike;
  [index: number]: SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null;
  onerror: ((ev: SpeechRecognitionErrorEventLike) => void) | null;
  onend: ((ev: Event) => void) | null;
  onspeechstart: ((ev: Event) => void) | null;
  onspeechend: ((ev: Event) => void) | null;
  onnomatch: ((ev: Event) => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

interface SpeechWindow {
  SpeechRecognition?: SpeechRecognitionCtor;
  webkitSpeechRecognition?: SpeechRecognitionCtor;
}

/** Resolve the SpeechRecognition constructor for the current environment, if any. */
function getSpeechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as SpeechWindow;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

/** Map a Web Speech API error code to the SttResult error reason union. */
function mapRecognitionError(
  code: string,
): "network" | "timeout" | "mic-unavailable" {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
    case "audio-capture":
      return "mic-unavailable";
    case "network":
      return "network";
    default:
      // "no-speech", "aborted", "language-not-supported", etc. surface as transient.
      return "timeout";
  }
}

// ---------------------------------------------------------------------------
// WebSpeechSttProvider
// ---------------------------------------------------------------------------

/**
 * Web Speech API implementation of SttProvider. Owns mic start/stop, the capture
 * timers, and the translation of recognition events into the SttResult union. Behind
 * the SttProvider interface so the backend is replaceable (Req 2.6).
 */
export class WebSpeechSttProvider implements SttProvider {
  private readonly ctor?: SpeechRecognitionCtor;
  private recognition: SpeechRecognitionLike | null = null;
  private callback: ((r: SttResult) => void) | null = null;
  private partialCallback: ((text: string) => void) | null = null;

  private noSpeechTimer: ReturnType<typeof setTimeout> | null = null;
  private maxCaptureTimer: ReturnType<typeof setTimeout> | null = null;
  private resultTimer: ReturnType<typeof setTimeout> | null = null;

  private speechStarted = false;
  private settled = false;

  constructor(private readonly lang: string = "en-US") {
    this.ctor = getSpeechRecognitionCtor();
  }

  onResult(cb: (r: SttResult) => void): void {
    this.callback = cb;
  }

  onPartial(cb: (text: string) => void): void {
    this.partialCallback = cb;
  }

  start(): void {
    if (!this.ctor) {
      // No recognition engine available behaves like an unavailable microphone.
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }

    this.cleanupTimers();
    this.speechStarted = false;
    this.settled = false;

    let recognition: SpeechRecognitionLike;
    try {
      recognition = new this.ctor();
    } catch {
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }

    recognition.lang = this.lang;
    recognition.continuous = false;
    // Interim results stream partial transcripts as the user speaks so the UI can
    // display speech-to-text in real time; the final result still settles the turn.
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onspeechstart = () => {
      this.speechStarted = true;
      this.clearTimer("noSpeech");
    };

    recognition.onspeechend = () => {
      // End-of-speech detected; allow a bounded window for the final transcript.
      this.armResultTimer();
    };

    recognition.onresult = (ev) => {
      // Separate final results (which settle the turn) from interim results (which
      // stream live to the partial callback). With continuous=false the engine
      // emits interim events as the user speaks and a final result at the end.
      let finalText = "";
      let interimText = "";
      for (let i = 0; i < ev.results.length; i++) {
        const result = ev.results.item(i);
        if (result.length === 0) continue;
        const piece = result.item(0).transcript;
        if (result.isFinal) {
          finalText += piece;
        } else {
          interimText += piece;
        }
      }

      if (!finalText.trim()) {
        // Still speaking: surface the interim transcript and wait for the final.
        const live = interimText.trim();
        if (live) {
          this.partialCallback?.(live);
        }
        return;
      }

      const text = finalText.trim();
      if (hasRecognizedWord(text)) {
        this.emit({ kind: "transcript", text });
      } else {
        this.emit({ kind: "no-match" });
      }
    };

    recognition.onnomatch = () => {
      this.emit({ kind: "no-match" });
    };

    recognition.onerror = (ev) => {
      this.emit({ kind: "error", reason: mapRecognitionError(ev.error) });
    };

    recognition.onend = () => {
      // If recognition ended without producing a result, treat as no-match.
      if (!this.settled) {
        this.emit({ kind: "no-match" });
      }
    };

    this.recognition = recognition;

    try {
      recognition.start();
    } catch {
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }

    // No speech after activation -> deactivate mic (Req 1.7).
    this.noSpeechTimer = setTimeout(() => {
      if (!this.speechStarted) {
        this.abortRecognition();
        this.emit({ kind: "no-match" });
      }
    }, NO_SPEECH_TIMEOUT_MS);

    // Hard capture cap -> finalize whatever was captured (Req 1.6).
    this.maxCaptureTimer = setTimeout(() => {
      this.stop();
    }, MAX_CAPTURE_MS);
  }

  stop(): void {
    this.clearTimer("noSpeech");
    this.clearTimer("maxCapture");
    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch {
        // Already stopped; ignore.
      }
      // Bound the wait for the post-stop final transcript (Req 2.1 / 2.5).
      this.armResultTimer();
    }
  }

  private armResultTimer(): void {
    if (this.settled || this.resultTimer) return;
    this.resultTimer = setTimeout(() => {
      this.abortRecognition();
      this.emit({ kind: "error", reason: "timeout" });
    }, STT_RESULT_TIMEOUT_MS);
  }

  private abortRecognition(): void {
    if (this.recognition) {
      try {
        this.recognition.abort();
      } catch {
        // Ignore abort failures.
      }
    }
  }

  private emit(result: SttResult): void {
    if (this.settled) return;
    this.settled = true;
    this.cleanupTimers();
    this.callback?.(result);
  }

  private clearTimer(which: "noSpeech" | "maxCapture" | "result"): void {
    const map = {
      noSpeech: () => {
        if (this.noSpeechTimer) clearTimeout(this.noSpeechTimer);
        this.noSpeechTimer = null;
      },
      maxCapture: () => {
        if (this.maxCaptureTimer) clearTimeout(this.maxCaptureTimer);
        this.maxCaptureTimer = null;
      },
      result: () => {
        if (this.resultTimer) clearTimeout(this.resultTimer);
        this.resultTimer = null;
      },
    } as const;
    map[which]();
  }

  private cleanupTimers(): void {
    this.clearTimer("noSpeech");
    this.clearTimer("maxCapture");
    this.clearTimer("result");
  }
}

// ---------------------------------------------------------------------------
// WebSpeechTtsEngine
// ---------------------------------------------------------------------------

/**
 * Local TTS engine backed by window.speechSynthesis. All synthesis is in-browser with
 * zero network traffic (Req 6.2) and English-only for the MVP demo (Req 6.3).
 */
export class WebSpeechTtsEngine implements TtsEngine {
  constructor(private readonly lang: string = "en-US") {}

  isAvailable(): boolean {
    return (
      typeof window !== "undefined" &&
      "speechSynthesis" in window &&
      typeof window.SpeechSynthesisUtterance !== "undefined"
    );
  }

  speak(text: string): Promise<void> {
    // Skip synthesis for empty/whitespace text; the UI returns to idle (Req 6.7).
    if (!isNonEmptyText(text)) {
      return Promise.resolve();
    }

    if (!this.isAvailable()) {
      return Promise.reject(new Error("tts-unavailable"));
    }

    const synth = window.speechSynthesis;

    return new Promise<void>((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = this.lang;

      // Prefer a voice matching the configured language (e.g. "ko" for "ko-KR"),
      // falling back to the browser default when none is installed.
      const langPrefix = this.lang.split("-")[0].toLowerCase();
      const matchingVoice = synth
        .getVoices()
        .find((v) => v.lang && v.lang.toLowerCase().startsWith(langPrefix));
      if (matchingVoice) {
        utterance.voice = matchingVoice;
      }

      utterance.onend = () => resolve();
      utterance.onerror = () => reject(new Error("tts-error"));

      synth.speak(utterance);
    });
  }
}

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
 * Continuous silence that marks end-of-utterance. Market orders are normally
 * short, so finalize quickly instead of leaving the kiosk in Listening for 3s.
 */
export const END_OF_SPEECH_SILENCE_MS = 900;

/** If no speech is detected after activation, deactivate the mic (Req 1.7). */
export const NO_SPEECH_TIMEOUT_MS = 4000;

/** Hard cap on a single capture; audio so far is treated as a finished utterance (Req 1.6). */
export const MAX_CAPTURE_MS = 10000;

/** A transcript must come back within this budget after end-of-speech (Req 2.1 / 2.5). */
export const STT_RESULT_TIMEOUT_MS = 5000;

/**
 * Market-mode foreground speech filter.
 *
 * Browser speech recognition doesn't expose beam-forming or speaker identity, so
 * the kiosk independently watches a noise-suppressed microphone stream.  It learns
 * the ambient floor at the start of each listen cycle and only accepts a transcript
 * after a substantially louder signal has remained present long enough to be a
 * nearby speaker rather than a short market-noise spike.
 */
export const FOREGROUND_CALIBRATION_MS = 300;
export const FOREGROUND_HOLD_MS = 120;
export const FOREGROUND_MIN_RMS = 0.012;
export const FOREGROUND_MAX_RMS = 0.15;
export const FOREGROUND_NOISE_MULTIPLIER = 1.6;
export const FOREGROUND_NOISE_MARGIN = 0.004;

/** Browser capture hints chosen for a noisy, single-speaker kiosk. */
export const MARKET_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  channelCount: { ideal: 1 },
  echoCancellation: { ideal: true },
  noiseSuppression: { ideal: true },
  // Automatic gain tends to amplify distant market conversations along with the
  // intended customer, which works against the foreground gate.
  autoGainControl: { ideal: false },
};

/** Adaptive level a voice must exceed to count as foreground speech. */
export function foregroundVoiceThreshold(noiseFloor: number): number {
  const adaptive = Math.max(0, noiseFloor) * FOREGROUND_NOISE_MULTIPLIER
    + FOREGROUND_NOISE_MARGIN;
  return Math.min(FOREGROUND_MAX_RMS, Math.max(FOREGROUND_MIN_RMS, adaptive));
}

/**
 * Stateful, browser-independent foreground detector.  `observe` returns whether
 * the current sample is above the adaptive gate; `detected` becomes true only after
 * that condition has held continuously for `FOREGROUND_HOLD_MS`.
 */
export class ForegroundVoiceGate {
  private readonly calibrationSamples: number[] = [];
  private noiseFloor: number | null = null;
  private foregroundSince: number | null = null;
  private _detected = false;

  constructor(private readonly startedAt: number) {}

  get detected(): boolean {
    return this._detected;
  }

  get threshold(): number {
    return foregroundVoiceThreshold(this.noiseFloor ?? 0);
  }

  /**
   * True while the *current* above-threshold streak has held for at least
   * {@link FOREGROUND_HOLD_MS} -- unlike {@link detected}, which latches
   * permanently once a foreground voice has ever been confirmed, this
   * re-evaluates every call. Used to require a sustained voice (not a single
   * loud market-noise blip) to keep blocking the end-of-speech silence timer,
   * so a brief clatter right after the customer stops talking doesn't restart
   * the whole wait and drag capture out toward the max-capture cap.
   */
  sustainedNow(now: number): boolean {
    return this.foregroundSince !== null && now - this.foregroundSince >= FOREGROUND_HOLD_MS;
  }

  observe(rms: number, now: number): boolean {
    const level = Number.isFinite(rms) ? Math.max(0, rms) : 0;
    if (now - this.startedAt < FOREGROUND_CALIBRATION_MS) {
      this.calibrationSamples.push(level);
      return false;
    }

    if (this.noiseFloor === null) {
      // A lower percentile is deliberately used: intermittent nearby clatter during
      // calibration shouldn't raise the floor enough to silence the customer.
      const sorted = [...this.calibrationSamples].sort((a, b) => a - b);
      const index = Math.floor(Math.max(0, sorted.length - 1) * 0.3);
      this.noiseFloor = sorted[index] ?? 0.007;
    }

    const threshold = this.threshold;
    const above = level >= threshold;
    if (above) {
      this.foregroundSince ??= now;
      if (now - this.foregroundSince >= FOREGROUND_HOLD_MS) {
        this._detected = true;
      }
    } else {
      this.foregroundSince = null;
      // Slowly follow changes in steady ambient noise, but never learn a loud voice
      // as part of the background while it is above the gate.
      if (!this._detected && level < threshold * 0.8) {
        this.noiseFloor = this.noiseFloor * 0.98 + level * 0.02;
      }
    }
    return above;
  }
}

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
  private transcribingCallback: (() => void) | null = null;

  private noSpeechTimer: ReturnType<typeof setTimeout> | null = null;
  private maxCaptureTimer: ReturnType<typeof setTimeout> | null = null;
  private resultTimer: ReturnType<typeof setTimeout> | null = null;

  private speechStarted = false;
  private settled = false;

  private foregroundStream: MediaStream | null = null;
  private foregroundAudioCtx: AudioContext | null = null;
  private foregroundRafId: number | null = null;
  private foregroundMonitorToken = 0;
  private foregroundDetected = false;
  private foregroundMonitorBypassed = false;

  constructor(private lang: string = "en-US") {
    this.ctor = getSpeechRecognitionCtor();
  }

  setLanguage(lang: string): void {
    this.lang = lang;
  }

  onResult(cb: (r: SttResult) => void): void {
    this.callback = cb;
  }

  onPartial(cb: (text: string) => void): void {
    this.partialCallback = cb;
  }

  onTranscribing(cb: () => void): void {
    this.transcribingCallback = cb;
  }

  start(): void {
    if (!this.ctor) {
      // No recognition engine available behaves like an unavailable microphone.
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }

    this.cleanupTimers();
    this.stopForegroundMonitor();
    this.speechStarted = false;
    this.settled = false;
    this.foregroundDetected = false;
    this.foregroundMonitorBypassed = false;
    this.startForegroundMonitor();

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
      // When monitoring is unavailable, retain the historical Web Speech behavior.
      // Otherwise only the independently measured nearby voice opens the gate.
      if (this.foregroundMonitorBypassed) {
        this.speechStarted = true;
        this.clearTimer("noSpeech");
      }
    };

    recognition.onspeechend = () => {
      // End-of-speech detected; allow a bounded window for the final transcript.
      this.transcribingCallback?.();
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
        if (live && this.acceptsForegroundVoice()) {
          this.partialCallback?.(live);
        }
        return;
      }

      const text = finalText.trim();
      if (hasRecognizedWord(text) && this.acceptsForegroundVoice()) {
        this.emit({ kind: "transcript", text });
      } else {
        // Valid words without a foreground signal are most likely another vendor,
        // a passer-by, music, or a loudspeaker and are intentionally discarded.
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
    this.stopForegroundMonitor();
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
    this.stopForegroundMonitor();
    this.callback?.(result);
  }

  private acceptsForegroundVoice(): boolean {
    return this.foregroundMonitorBypassed || this.foregroundDetected;
  }

  /**
   * Run a second, noise-suppressed level monitor alongside Web Speech.  The monitor
   * never records or uploads audio; it only calculates local RMS values used to
   * decide whether the recognized words came from a nearby foreground speaker.
   */
  private startForegroundMonitor(): void {
    const mediaDevices = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
    const audioCtor = typeof window === "undefined"
      ? undefined
      : window.AudioContext
        ?? (window as unknown as WindowWithWebkitAudioContext).webkitAudioContext;
    if (!mediaDevices?.getUserMedia || !audioCtor) {
      this.foregroundMonitorBypassed = true;
      return;
    }

    const token = ++this.foregroundMonitorToken;
    let ctx: AudioContext;
    try {
      ctx = new audioCtor();
      this.foregroundAudioCtx = ctx;
      void ctx.resume().catch(() => undefined);
    } catch {
      this.foregroundMonitorBypassed = true;
      return;
    }

    void mediaDevices.getUserMedia({ audio: MARKET_AUDIO_CONSTRAINTS })
      .then((stream) => {
        if (token !== this.foregroundMonitorToken || this.settled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        this.foregroundStream = stream;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.15;
        ctx.createMediaStreamSource(stream).connect(analyser);

        const samples = new Float32Array(analyser.fftSize);
        const gate = new ForegroundVoiceGate(performance.now());
        const tick = (): void => {
          if (token !== this.foregroundMonitorToken || this.settled) return;
          analyser.getFloatTimeDomainData(samples);
          let sumSquares = 0;
          for (const sample of samples) sumSquares += sample * sample;
          const rms = Math.sqrt(sumSquares / samples.length);
          gate.observe(rms, performance.now());
          if (gate.detected) {
            this.foregroundDetected = true;
            this.speechStarted = true;
            this.clearTimer("noSpeech");
          }
          this.foregroundRafId = requestAnimationFrame(tick);
        };
        this.foregroundRafId = requestAnimationFrame(tick);
      })
      .catch(() => {
        if (token !== this.foregroundMonitorToken) return;
        // SpeechRecognition will report a permission/device error if the mic really
        // is unavailable.  Fail open here for browsers that support Web Speech but
        // don't expose a separately shareable MediaStream.
        this.foregroundMonitorBypassed = true;
        void ctx.close();
        if (this.foregroundAudioCtx === ctx) this.foregroundAudioCtx = null;
      });
  }

  private stopForegroundMonitor(): void {
    this.foregroundMonitorToken += 1;
    if (this.foregroundRafId !== null) {
      cancelAnimationFrame(this.foregroundRafId);
      this.foregroundRafId = null;
    }
    this.foregroundStream?.getTracks().forEach((track) => track.stop());
    this.foregroundStream = null;
    if (this.foregroundAudioCtx) {
      void this.foregroundAudioCtx.close();
      this.foregroundAudioCtx = null;
    }
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
// LocalSttProvider
//
// Records the microphone via MediaRecorder + a volume-based silence detector,
// then posts the finished clip to the backend for fully-offline transcription
// (faster-whisper -- see backend/stt.py). Used when STT_PROVIDER=local, e.g.
// because the browser's built-in Web Speech API can't reach Google's speech
// service (blocked network/extension) or fully-offline operation is wanted.
//
// Unlike WebSpeechSttProvider this has no interim/streaming results -- Whisper
// here runs in one-shot batch mode over the whole clip -- so onPartial is a
// no-op and the transcript only arrives once, after the clip is sent.
// ---------------------------------------------------------------------------

/** RMS amplitude (0..1) above which mic input counts as "speech" for the
 * client-side end-of-speech detector (there is no server-side VAD feedback loop
 * here; faster-whisper's own VAD filter still cleans up the final transcript). */
interface WindowWithWebkitAudioContext {
  webkitAudioContext?: typeof AudioContext;
}

export class LocalSttProvider implements SttProvider {
  private callback: ((r: SttResult) => void) | null = null;
  private transcribingCallback: (() => void) | null = null;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private audioCtx: AudioContext | null = null;
  private rafId: number | null = null;

  private noSpeechTimer: ReturnType<typeof setTimeout> | null = null;
  private silenceTimer: ReturnType<typeof setTimeout> | null = null;
  private maxCaptureTimer: ReturnType<typeof setTimeout> | null = null;

  private speechStarted = false;
  private settled = false;

  constructor(_lang: string = "en-US") {}

  /**
   * No-op: unlike the browser's Web Speech API, Whisper (backend/stt.py)
   * always auto-detects the spoken language from the audio itself, so there is
   * no recognition language to keep in sync here. Forcing one previously meant
   * a customer's first non-English utterance was transcribed *as* English
   * (garbled), which could never produce the characters needed to detect and
   * switch away from English -- kept as a no-op only to satisfy SttProvider.
   */
  setLanguage(_lang: string): void {}

  onResult(cb: (r: SttResult) => void): void {
    this.callback = cb;
  }

  /** No interim transcripts in batch mode; kept as a no-op to satisfy SttProvider. */
  onPartial(_cb: (text: string) => void): void {}

  onTranscribing(cb: () => void): void {
    this.transcribingCallback = cb;
  }

  start(): void {
    this.settled = false;
    this.speechStarted = false;
    void this.beginCapture();
  }

  stop(): void {
    this.stopCapture();
  }

  private async beginCapture(): Promise<void> {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: MARKET_AUDIO_CONSTRAINTS });
    } catch {
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }
    if (this.settled) {
      // stop() (or a timeout) already fired while permission was pending.
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.stream = stream;

    const chunks: BlobPart[] = [];
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch {
      this.emit({ kind: "error", reason: "mic-unavailable" });
      return;
    }
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => void this.finish(new Blob(chunks, { type: recorder.mimeType }));
    this.recorder = recorder;
    recorder.start();

    this.setupSilenceDetection(stream);

    // No speech at all after activation -> give up (Req 1.7 parity).
    this.noSpeechTimer = setTimeout(() => {
      if (!this.speechStarted) this.stopCapture();
    }, NO_SPEECH_TIMEOUT_MS);

    // Hard capture cap -> finalize whatever was captured (Req 1.6 parity).
    this.maxCaptureTimer = setTimeout(() => this.stopCapture(), MAX_CAPTURE_MS);
  }

  private setupSilenceDetection(stream: MediaStream): void {
    const ctor =
      window.AudioContext ?? (window as unknown as WindowWithWebkitAudioContext).webkitAudioContext;
    if (!ctor) return; // no Web Audio API -> falls back to the no-speech/max-capture timers only

    const ctx = new ctor();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    this.audioCtx = ctx;
    const gate = new ForegroundVoiceGate(performance.now());

    const data = new Uint8Array(analyser.fftSize);
    const tick = (): void => {
      analyser.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i++) {
        const sample = (data[i] - 128) / 128;
        sumSquares += sample * sample;
      }
      const rms = Math.sqrt(sumSquares / data.length);

      const now = performance.now();
      gate.observe(rms, now);
      // A single loud frame (a plate clatter, a passer-by's word) shouldn't
      // reset the silence countdown -- only a *sustained* above-threshold
      // streak counts as someone still actively talking. Otherwise, in a
      // busy market, brief noise between customer phrases would keep
      // re-arming the timer and drag capture out toward MAX_CAPTURE_MS.
      if (gate.detected && gate.sustainedNow(now)) {
        this.speechStarted = true;
        this.clearTimer("noSpeech");
        this.clearTimer("silence");
      } else if (this.speechStarted && this.silenceTimer === null) {
        this.silenceTimer = setTimeout(() => this.stopCapture(), END_OF_SPEECH_SILENCE_MS);
      }
      this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  private stopCapture(): void {
    this.clearAllTimers();
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    if (this.audioCtx) {
      void this.audioCtx.close();
      this.audioCtx = null;
    }
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.stop(); // triggers onstop -> finish()
    } else if (!this.settled) {
      // Never actually started recording (e.g. stop() called before the mic
      // permission prompt resolved) -- settle here since onstop will never fire.
      this.emit({ kind: "no-match" });
    }
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  private async finish(blob: Blob): Promise<void> {
    if (!this.speechStarted) {
      this.emit({ kind: "no-match" });
      return;
    }
    // Capture has ended and the clip is now being sent off for transcription
    // -- the UI's cue to move from "listening" to a distinct "understanding"
    // state rather than appearing to hang until this resolves.
    this.transcribingCallback?.();
    try {
      const form = new FormData();
      form.append("audio", blob, "clip.webm");
      const response = await fetch("/api/stt/transcribe", { method: "POST", body: form });
      if (!response.ok) {
        this.emit({ kind: "error", reason: "network" });
        return;
      }
      const data = (await response.json()) as {
        text?: string;
        language?: string;
        language_probability?: number;
      };
      const text = (data.text ?? "").trim();
      this.emit(text ? {
        kind: "transcript",
        text,
        detectedLanguage: data.language,
        languageConfidence: data.language_probability,
      } : { kind: "no-match" });
    } catch {
      this.emit({ kind: "error", reason: "network" });
    }
  }

  private emit(result: SttResult): void {
    if (this.settled) return;
    this.settled = true;
    this.callback?.(result);
  }

  private clearTimer(which: "noSpeech" | "silence" | "maxCapture"): void {
    const map = {
      noSpeech: () => {
        if (this.noSpeechTimer) clearTimeout(this.noSpeechTimer);
        this.noSpeechTimer = null;
      },
      silence: () => {
        if (this.silenceTimer) clearTimeout(this.silenceTimer);
        this.silenceTimer = null;
      },
      maxCapture: () => {
        if (this.maxCaptureTimer) clearTimeout(this.maxCaptureTimer);
        this.maxCaptureTimer = null;
      },
    } as const;
    map[which]();
  }

  private clearAllTimers(): void {
    this.clearTimer("noSpeech");
    this.clearTimer("silence");
    this.clearTimer("maxCapture");
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
  constructor(private lang: string = "en-US") {}

  setLanguage(lang: string): void {
    this.lang = lang;
  }

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

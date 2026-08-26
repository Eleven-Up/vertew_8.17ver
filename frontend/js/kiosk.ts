// Kiosk_UI: conversation state machine, listening indicator, and error banners.
//
// Conversation model (updated for hands-free dialogue):
//   - A screen tap in the idle state STARTS a conversation and opens the mic.
//   - While a conversation is active the mic re-opens automatically after every
//     spoken reply, so the customer can keep talking WITHOUT tapping again.
//   - If no speech is heard for SILENCE_TO_IDLE_MS (1 minute), the conversation
//     ends and the UI returns to idle, where a tap is required to start again.
//
// The controller drives the injected Speech_Module (SttProvider + TtsEngine) and
// Character_Renderer, and reports state changes via onStateChange so the page can
// show greeting / listening / speaking visuals. Collaborators are injected so the
// machine can be exercised without a real browser.

import type {
  CharacterResponse,
  Emotion,
  Gesture,
  SttProvider,
  SttResult,
  TtsEngine,
  UIState,
} from "./types.js";
import { isNonEmptyText, isNonEmptyTranscript } from "./speech.js";
import type { CharacterRenderer } from "./character.js";
import { normalizeEmotion, normalizeGesture } from "./character.js";
import { splitIntoBeats, type Beat } from "./emotion-cues.js";

/** Silence (no recognized speech) that ends an active conversation, in ms. */
export const SILENCE_TO_IDLE_MS = 60_000;

/** Budget for returning to idle after a voice reply or error banner (Req 6.5). */
export const RETURN_TO_IDLE_MS = 1000;

/** DOM ids managed by {@link DomKioskView}. */
export const LISTENING_INDICATOR_ID = "listening-indicator";
export const ERROR_BANNER_ID = "error-banner";

/** Class toggled to show/hide the indicator and banner elements. */
export const VISIBLE_CLASS = "visible";

/** Categories of error surfaced to the customer. */
export type ErrorKind = "mic" | "stt-empty" | "network" | "tts";

/** User-facing copy for each {@link ErrorKind}. */
export const ERROR_MESSAGES: Record<ErrorKind, string> = {
  mic: "Microphone unavailable. Please check permissions and try again.",
  "stt-empty": "Sorry, I didn't catch that. Tap the screen and try again.",
  network: "Connection trouble. Please try again in a moment.",
  tts: "Couldn't play the voice response.",
};

// ---------------------------------------------------------------------------
// View boundary
// ---------------------------------------------------------------------------

/** Side-effecting UI surface: listening indicator + error banner. */
export interface KioskView {
  showListeningIndicator(): void;
  hideListeningIndicator(): void;
  showErrorBanner(kind: ErrorKind, message: string): void;
  clearErrorBanner(): void;
}

/** DOM-backed {@link KioskView}. */
export class DomKioskView implements KioskView {
  constructor(
    private readonly indicator: HTMLElement,
    private readonly banner: HTMLElement,
  ) {}

  showListeningIndicator(): void {
    this.indicator.classList.add(VISIBLE_CLASS);
  }
  hideListeningIndicator(): void {
    this.indicator.classList.remove(VISIBLE_CLASS);
  }
  showErrorBanner(kind: ErrorKind, message: string): void {
    this.banner.dataset.kind = kind;
    this.banner.textContent = message;
    this.banner.classList.add(VISIBLE_CLASS);
  }
  clearErrorBanner(): void {
    this.banner.classList.remove(VISIBLE_CLASS);
    this.banner.textContent = "";
    delete this.banner.dataset.kind;
  }
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

export interface KioskControllerDeps {
  stt: SttProvider;
  tts: TtsEngine;
  renderer: CharacterRenderer;
  view: KioskView;
  /** Forwarded a non-empty transcript when a turn is sent to the server. */
  onSendTranscript?: (
    text: string,
    detectedLanguage?: string,
    languageConfidence?: number,
  ) => void;
  /** Notified whenever the conversation state changes (for page-level visuals). */
  onStateChange?: (state: UIState) => void;
}

/**
 * Hands-free conversation state machine. A tap starts the conversation; the mic
 * then re-opens automatically after each reply until a minute of silence returns
 * the UI to idle.
 */
export class KioskController {
  private readonly stt: SttProvider;
  private readonly tts: TtsEngine;
  private readonly renderer: CharacterRenderer;
  private readonly view: KioskView;
  private readonly onSendTranscript?: (
    text: string,
    detectedLanguage?: string,
    languageConfidence?: number,
  ) => void;
  private readonly onStateChange?: (state: UIState) => void;

  private _state: UIState = "idle";
  private conversationActive = false;
  private lastInteractionAt = 0;
  private returnTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(deps: KioskControllerDeps) {
    this.stt = deps.stt;
    this.tts = deps.tts;
    this.renderer = deps.renderer;
    this.view = deps.view;
    this.onSendTranscript = deps.onSendTranscript;
    this.onStateChange = deps.onStateChange;

    this.stt.onResult((r) => this.handleSttResult(r));

    this.view.hideListeningIndicator();
    this.view.clearErrorBanner();
    this.renderer.playIdle();
    // Announce the initial state so the page shows the idle greeting.
    this.onStateChange?.(this._state);
  }

  get state(): UIState {
    return this._state;
  }

  /** True while a hands-free conversation is in progress. */
  get isConversationActive(): boolean {
    return this.conversationActive;
  }

  private setState(next: UIState): void {
    if (this._state === next) return;
    this._state = next;
    this.onStateChange?.(next);
  }

  /** Screen tap. Honored only in idle: it starts a hands-free conversation. */
  onTap(): void {
    if (this._state !== "idle") return;
    this.conversationActive = true;
    this.lastInteractionAt = Date.now();
    this.enterListening();
  }

  /** A transcript from the Speech_Module (meaningful only while listening). */
  onTranscript(
    text: string,
    detectedLanguage?: string,
    languageConfidence?: number,
  ): void {
    if (this._state !== "listening") return;
    if (!isNonEmptyTranscript(text)) {
      this.handleNoMatch();
      return;
    }
    this.lastInteractionAt = Date.now();
    this.stt.stop();
    this.view.hideListeningIndicator();
    this.setState("processing");
    this.onSendTranscript?.(text, detectedLanguage, languageConfidence);
  }

  /** A response from the Conversation_Server (meaningful only while processing). */
  onServerResponse(r: CharacterResponse): void {
    if (this._state !== "processing") return;
    this.setState("speaking");
    this.speak(r.text, normalizeEmotion(r.emotion), normalizeGesture(r.gesture));
  }

  /** Voice output finished. */
  onTtsComplete(): void {
    if (this._state !== "speaking") return;
    this.renderer.stopLipSync();
    this.lastInteractionAt = Date.now();
    this.finishSpeaking();
  }

  /** Show an error banner, end the conversation, and recover to idle. */
  showError(kind: ErrorKind): void {
    this.stt.stop();
    this.renderer.stopLipSync();
    this.view.hideListeningIndicator();
    this.view.showErrorBanner(kind, ERROR_MESSAGES[kind]);
    this.conversationActive = false;
    this.setState("error");
    this.scheduleReturnToIdle();
  }

  // -------------------------------------------------------------------------
  // Internal transitions
  // -------------------------------------------------------------------------

  private handleSttResult(r: SttResult): void {
    if (this._state !== "listening") return;
    switch (r.kind) {
      case "transcript":
        this.onTranscript(r.text, r.detectedLanguage, r.languageConfidence);
        return;
      case "no-match":
        this.handleNoMatch();
        return;
      case "error":
        if (r.reason === "mic-unavailable") this.showError("mic");
        else this.showError("network");
        return;
    }
  }

  /**
   * No speech was recognized. While a conversation is active we keep listening
   * silently until a full minute of silence has elapsed, then return to idle.
   */
  private handleNoMatch(): void {
    if (this.conversationActive) {
      if (Date.now() - this.lastInteractionAt >= SILENCE_TO_IDLE_MS) {
        this.endConversation();
      } else {
        this.enterListening(); // keep the mic open, no error shown
      }
      return;
    }
    this.returnToIdle();
  }

  /**
   * Speaks `text` as a sequence of per-sentence "beats" (see emotion-cues.ts)
   * so the character's emotion/gesture can change mid-reply -- e.g. a
   * head-tilt on a question, a bounce on an exclamation -- rather than
   * holding one pose for the whole answer. `fallbackEmotion`/`fallbackGesture`
   * are the server's own choice for this turn, used for any sentence that
   * matches no specific cue.
   */
  private speak(text: string, fallbackEmotion: Emotion, fallbackGesture: Gesture): void {
    if (!isNonEmptyText(text)) {
      this.renderer.render(fallbackEmotion, fallbackGesture);
      this.renderer.stopLipSync();
      this.lastInteractionAt = Date.now();
      this.finishSpeaking();
      return;
    }
    if (!this.tts.isAvailable()) {
      this.renderer.render(fallbackEmotion, fallbackGesture);
      this.showError("tts");
      return;
    }
    const beats = splitIntoBeats(text, { emotion: fallbackEmotion, gesture: fallbackGesture });
    this.renderer.startLipSync();
    this.speakBeat(beats, 0);
  }

  /** Renders and speaks one beat, then recurses to the next -- lip-sync stays
   * on continuously across the whole sequence (started in speak(), stopped by
   * the eventual onTtsComplete()) so it doesn't stutter between sentences. */
  private speakBeat(beats: Beat[], index: number): void {
    if (this._state !== "speaking") return; // a prior beat's failure already ended the turn
    if (index >= beats.length) {
      this.onTtsComplete();
      return;
    }
    const beat = beats[index];
    this.renderer.render(beat.emotion, beat.gesture);
    this.tts
      .speak(beat.text)
      .then(() => this.speakBeat(beats, index + 1))
      .catch(() => {
        if (this._state === "speaking") this.showError("tts");
      });
  }

  /** After a reply: keep listening if the conversation is active, else go idle. */
  private finishSpeaking(): void {
    if (this.conversationActive) {
      this.enterListening();
    } else {
      this.returnToIdle();
    }
  }

  private enterListening(): void {
    this.clearReturnTimer();
    this.view.clearErrorBanner();
    this.setState("listening");
    this.view.showListeningIndicator();
    this.renderer.playListening();
    this.stt.start();
  }

  private endConversation(): void {
    this.conversationActive = false;
    this.stt.stop();
    this.view.hideListeningIndicator();
    this.returnToIdle();
  }

  private returnToIdle(): void {
    this.clearReturnTimer();
    this.conversationActive = false;
    this.setState("idle");
    this.view.hideListeningIndicator();
    this.view.clearErrorBanner();
    this.renderer.playIdle();
  }

  private scheduleReturnToIdle(): void {
    this.clearReturnTimer();
    this.returnTimer = setTimeout(() => {
      this.returnTimer = null;
      this.returnToIdle();
    }, RETURN_TO_IDLE_MS);
  }

  private clearReturnTimer(): void {
    if (this.returnTimer !== null) {
      clearTimeout(this.returnTimer);
      this.returnTimer = null;
    }
  }
}

// ---------------------------------------------------------------------------
// DOM factory
// ---------------------------------------------------------------------------

export function createDomKioskView(container?: HTMLElement): DomKioskView {
  const root =
    container ?? document.getElementById("stage") ?? document.body ?? undefined;
  if (!root) {
    throw new Error("Kiosk_UI: no container and no #stage element found.");
  }
  const indicator = ensureChild(root, LISTENING_INDICATOR_ID);
  const banner = ensureChild(root, ERROR_BANNER_ID);
  return new DomKioskView(indicator, banner);
}

function ensureChild(root: HTMLElement, id: string): HTMLElement {
  const existing = root.ownerDocument.getElementById(id);
  if (existing) return existing;
  const el = root.ownerDocument.createElement("div");
  el.id = id;
  root.appendChild(el);
  return el;
}

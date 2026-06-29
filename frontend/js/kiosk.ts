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
  SttProvider,
  SttResult,
  TtsEngine,
  UIState,
} from "./types.js";
import { isNonEmptyText, isNonEmptyTranscript } from "./speech.js";
import type { CharacterRenderer } from "./character.js";

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
  mic: "마이크를 사용할 수 없어요. 권한을 확인하고 다시 시도해 주세요.",
  "stt-empty": "잘 못 들었어요. 화면을 탭하고 다시 말씀해 주세요.",
  network: "연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.",
  tts: "음성을 재생할 수 없어요.",
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
  onSendTranscript?: (text: string) => void;
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
  private readonly onSendTranscript?: (text: string) => void;
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
  onTranscript(text: string): void {
    if (this._state !== "listening") return;
    if (!isNonEmptyTranscript(text)) {
      this.handleNoMatch();
      return;
    }
    this.lastInteractionAt = Date.now();
    this.stt.stop();
    this.view.hideListeningIndicator();
    this.setState("processing");
    this.onSendTranscript?.(text);
  }

  /** A response from the Conversation_Server (meaningful only while processing). */
  onServerResponse(r: CharacterResponse): void {
    if (this._state !== "processing") return;
    this.setState("speaking");
    this.renderer.render(r.emotion, r.gesture);
    this.speak(r.text);
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
        this.onTranscript(r.text);
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

  private speak(text: string): void {
    if (!isNonEmptyText(text)) {
      this.renderer.stopLipSync();
      this.lastInteractionAt = Date.now();
      this.finishSpeaking();
      return;
    }
    if (!this.tts.isAvailable()) {
      this.showError("tts");
      return;
    }
    this.renderer.startLipSync();
    this.tts
      .speak(text)
      .then(() => this.onTtsComplete())
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

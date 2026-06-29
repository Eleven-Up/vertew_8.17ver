// ChatLog: on-screen conversation transcript + live speech caption.
//
// Renders the running conversation as chat bubbles (customer on the right,
// character on the left) and shows a live, in-progress caption of the customer's
// speech as it is transcribed in real time. This is a presentation-only helper for
// the Kiosk_UI; it holds no conversation logic and is wired in main.ts.

/** DOM id of the scrollable conversation transcript container. */
export const CHAT_LOG_ID = "chat-log";

/** DOM id of the live speech-to-text caption element. */
export const LIVE_CAPTION_ID = "live-caption";

/** Class toggled to reveal the live caption. */
const VISIBLE_CLASS = "visible";

/** Speakers shown in the transcript. */
export type Speaker = "user" | "character";

/**
 * Presentation helper that appends chat bubbles to a transcript container and
 * drives a live caption element. Construct via {@link createChatLog} to bind to
 * the default `#chat-log` / `#live-caption` elements.
 */
export class ChatLog {
  constructor(
    private readonly log: HTMLElement,
    private readonly caption: HTMLElement,
  ) {}

  /** Append a customer (user) message bubble. */
  addUser(text: string): void {
    this.append("user", text);
  }

  /** Append a character (assistant) message bubble. */
  addCharacter(text: string): void {
    this.append("character", text);
  }

  /** Show the live, in-progress speech transcript while the customer speaks. */
  setLiveCaption(text: string): void {
    this.caption.textContent = text;
    if (text.trim()) {
      this.caption.classList.add(VISIBLE_CLASS);
    } else {
      this.caption.classList.remove(VISIBLE_CLASS);
    }
  }

  /** Clear and hide the live caption (e.g., once the final transcript is sent). */
  clearLiveCaption(): void {
    this.caption.textContent = "";
    this.caption.classList.remove(VISIBLE_CLASS);
  }

  private append(speaker: Speaker, text: string): void {
    const bubble = this.log.ownerDocument.createElement("div");
    bubble.className = `msg msg--${speaker}`;
    bubble.textContent = text;
    this.log.appendChild(bubble);
    // Keep the newest message in view.
    this.log.scrollTop = this.log.scrollHeight;
  }
}

/**
 * Locate or create the `#chat-log` and `#live-caption` elements inside the given
 * container (default `#stage`) and return a {@link ChatLog}. Throws if no container
 * is available.
 */
export function createChatLog(container?: HTMLElement): ChatLog {
  const root =
    container ?? document.getElementById("stage") ?? document.body ?? undefined;
  if (!root) {
    throw new Error("ChatLog: no container and no #stage element found.");
  }
  const log = ensureChild(root, CHAT_LOG_ID, "aside");
  const caption = ensureChild(root, LIVE_CAPTION_ID, "div");
  return new ChatLog(log, caption);
}

function ensureChild(
  root: HTMLElement,
  id: string,
  tag: string,
): HTMLElement {
  const existing = root.ownerDocument.getElementById(id);
  if (existing) {
    return existing;
  }
  const el = root.ownerDocument.createElement(tag);
  el.id = id;
  root.appendChild(el);
  return el;
}

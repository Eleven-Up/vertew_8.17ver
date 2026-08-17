// chat.ts: localhost WebSocket client that connects the Kiosk_UI to the
// Conversation_Server.
//
// The Kiosk_UI talks ONLY to the local Conversation_Server over a localhost
// WebSocket and issues no internet network requests (Req 9.1). The socket URL is
// derived from the page's own location (see buildWsUrl) so it always targets the
// same origin that served the page — never an external/internet host.
//
// Per turn the client:
//   - sends the customer transcript as JSON `{transcript}` when the Kiosk_UI moves
//     from listening to processing (Req 4.3);
//   - receives a `{text, emotion, gesture}` response and routes it to the
//     KioskController, which renders the Character_Renderer and drives local TTS
//     (Req 5.1, taps already ignored during TTS by the controller — Req 6.4);
//   - surfaces a network error to the KioskController (showError("network")) when
//     the socket fails or closes while a turn is in flight.
//
// External boundaries are injected (a WebSocket factory) so the client can be
// exercised without a real socket (see the localhost-only test in task 13.2).

import type { CharacterResponse } from "./types.js";

/** Endpoint path of the Conversation_Server conversation loop (backend/main.py). */
export const WS_PATH = "/ws";

/** WebSocket.OPEN readyState value (declared locally to avoid a DOM dependency). */
const WS_OPEN = 1;

/**
 * Minimal structural subset of `WindowLocation` used to derive the socket URL.
 * Accepting this (rather than the full `Location`) keeps {@link buildWsUrl} pure
 * and trivially testable.
 */
export interface LocationLike {
  /** Page protocol, e.g. "http:" or "https:". */
  protocol: string;
  /** Host including port, e.g. "localhost:8000". */
  host: string;
}

/**
 * Minimal structural subset of the browser `WebSocket` the client drives. A real
 * `WebSocket` satisfies this; tests can supply a lightweight double.
 */
export interface WebSocketLike {
  send(data: string): void;
  close(): void;
  readonly readyState: number;
  onopen: ((this: unknown, ev: unknown) => void) | null;
  onmessage: ((this: unknown, ev: { data: unknown }) => void) | null;
  onerror: ((this: unknown, ev: unknown) => void) | null;
  onclose: ((this: unknown, ev: unknown) => void) | null;
}

/** Factory that opens a socket for the given URL. Injected for testability. */
export type WebSocketFactory = (url: string) => WebSocketLike;

/**
 * Derive the conversation WebSocket URL from the page location. Uses the page's
 * own protocol and host so the socket targets the same origin that served the
 * Kiosk_UI — localhost in the kiosk deployment — and never an internet host
 * (Req 9.1). `https:` pages upgrade to the secure `wss:` scheme.
 */
export function buildWsUrl(location: LocationLike, path: string = WS_PATH): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}`;
}

/** Options for {@link ChatClient}. */
export interface ChatClientOptions {
  /** The conversation socket URL (build with {@link buildWsUrl}). */
  url: string;
  /**
   * Opens a socket for a URL. Defaults to the global `WebSocket`. Injected so the
   * client can run against a double in tests (task 13.2).
   */
  socketFactory?: WebSocketFactory;
  /** Routed the parsed server response (Req 5.1). Wired to onServerResponse. */
  onResponse?: (response: CharacterResponse) => void;
  /**
   * Invoked when the socket fails or closes while a turn is in flight. Wired to
   * KioskController.showError("network") (design Error Handling, Req 2.5/9.5).
   */
  onNetworkError?: () => void;
}

/**
 * Localhost WebSocket client for the conversation loop. Maintains a single socket
 * to the Conversation_Server, sends transcripts, and routes responses back to the
 * Kiosk_UI. Connection failures that occur while awaiting a turn's response are
 * surfaced as a network error so the UI can recover to idle.
 */
export class ChatClient {
  private readonly url: string;
  private readonly socketFactory: WebSocketFactory;
  private socket: WebSocketLike | null = null;

  private responseHandler?: (response: CharacterResponse) => void;
  private networkErrorHandler?: () => void;

  /** True between sending a transcript and receiving (or failing) its response. */
  private turnInFlight = false;
  private pendingTranscript: string | null = null;

  constructor(options: ChatClientOptions) {
    this.url = options.url;
    this.socketFactory = options.socketFactory ?? defaultSocketFactory;
    this.responseHandler = options.onResponse;
    this.networkErrorHandler = options.onNetworkError;
  }

  /** Register/replace the response handler routed to the Kiosk_UI (Req 5.1). */
  onResponse(handler: (response: CharacterResponse) => void): void {
    this.responseHandler = handler;
  }

  /** Register/replace the network-error handler (showError("network")). */
  onNetworkError(handler: () => void): void {
    this.networkErrorHandler = handler;
  }

  /**
   * Open the conversation socket if it is not already open. Safe to call more than
   * once; an existing live socket is reused.
   */
  connect(): void {
    if (this.socket !== null) {
      return;
    }
    let socket: WebSocketLike;
    try {
      socket = this.socketFactory(this.url);
    } catch {
      // Could not even create the socket — treat as a connection failure so an
      // in-flight turn (none yet at connect time) would recover; reset state.
      this.socket = null;
      this.failTurn();
      return;
    }

    socket.onmessage = (ev) => this.handleMessage(ev.data);
    socket.onopen = () => this.flushPending();
    socket.onerror = () => this.handleConnectionDrop();
    socket.onclose = () => this.handleConnectionDrop();

    this.socket = socket;
  }

  /**
   * Send a customer transcript to the Conversation_Server as `{transcript}` JSON
   * (Req 4.3) and mark a turn in flight. If the socket is not open, the turn fails
   * immediately with a network error.
   */
  send(transcript: string): void {
    this.connect();
    const socket = this.socket;
    if (socket === null || socket.readyState !== WS_OPEN) {
      // The first utterance often arrives while the socket is still connecting.
      // Keep one in-flight transcript and send it as soon as onopen fires.
      this.turnInFlight = true;
      this.pendingTranscript = transcript;
      return;
    }
    this.turnInFlight = true;
    try {
      socket.send(JSON.stringify({ transcript }));
    } catch {
      this.failTurn();
    }
  }

  /** Close the socket and drop the reference. */
  close(): void {
    if (this.socket !== null) {
      try {
        this.socket.close();
      } catch {
        // Already closing/closed; ignore.
      }
      this.socket = null;
    }
    this.turnInFlight = false;
    this.pendingTranscript = null;
  }

  // -------------------------------------------------------------------------
  // Internal
  // -------------------------------------------------------------------------

  private handleMessage(data: unknown): void {
    const response = parseServerResponse(data);
    if (response === null) {
      // Unparseable frame while awaiting a turn -> treat as a failed turn so the
      // UI does not hang in the processing state.
      if (this.turnInFlight) {
        this.failTurn();
      }
      return;
    }
    this.turnInFlight = false;
    this.responseHandler?.(response);
  }

  private flushPending(): void {
    const transcript = this.pendingTranscript;
    const socket = this.socket;
    if (!transcript || !socket || socket.readyState !== WS_OPEN) return;
    this.pendingTranscript = null;
    try {
      socket.send(JSON.stringify({ transcript }));
    } catch {
      this.failTurn();
    }
  }

  private handleConnectionDrop(): void {
    this.socket = null;
    // Only surface an error if a turn was awaiting a response; an idle close is
    // benign and the next send() will reconnect.
    if (this.turnInFlight) {
      this.failTurn();
    }
  }

  private failTurn(): void {
    this.turnInFlight = false;
    this.pendingTranscript = null;
    this.networkErrorHandler?.();
  }
}

/** Default factory using the global browser `WebSocket`. */
function defaultSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url) as unknown as WebSocketLike;
}

/**
 * Parse an inbound WebSocket frame into a {@link CharacterResponse}. Accepts a
 * JSON string or an already-parsed object carrying `text`, `emotion`, and
 * `gesture` string fields (the server's wire shape). Returns `null` for anything
 * else. The server is authoritative for fallbacks/normalization, so this is a thin
 * structural extractor.
 */
export function parseServerResponse(data: unknown): CharacterResponse | null {
  let obj: unknown = data;
  if (typeof data === "string") {
    try {
      obj = JSON.parse(data);
    } catch {
      return null;
    }
  }
  if (typeof obj !== "object" || obj === null) {
    return null;
  }
  const record = obj as Record<string, unknown>;
  const { text, emotion, gesture } = record;
  if (
    typeof text !== "string" ||
    typeof emotion !== "string" ||
    typeof gesture !== "string"
  ) {
    return null;
  }
  return { text, emotion, gesture };
}

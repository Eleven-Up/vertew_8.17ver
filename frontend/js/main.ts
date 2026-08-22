import QRCode from "qrcode";
import { createCharacterRenderer, type CharacterRenderer } from "./character.js";
import { ChatClient, buildWsUrl } from "./chat.js";
import { createChatLog } from "./chatlog.js";
import { splitIntoBeats } from "./emotion-cues.js";
import { analyzeTranscript, createCustomerSession, getActiveOrders, getCustomerSession, getReadyOrders, getSttConfig, EventVideoQueue, loadMediaConfig, StoreEventClient, type BoardOrder, type StoreEvent } from "./hologram.js";
import { KioskController, createDomKioskView } from "./kiosk.js";
import { LocalSttProvider, WebSpeechSttProvider, WebSpeechTtsEngine } from "./speech.js";
import type { SttProvider } from "./types.js";

const STORE_ID = "demo";
const ORDER_BOARD_EVENTS = new Set([
  "new_order", "order_accepted", "order_preparing", "order_ready", "order_completed", "order_rejected",
]);
const readyCopy: Record<string, (number: number) => string> = {
  en: (number) => `Order number ${number}! Your order is ready!`,
  ko: (number) => `${number}번 고객님! 주문하신 상품이 준비되었습니다!`,
  ms: (number) => `Pesanan nombor ${number}! Pesanan anda sudah siap!`,
};

const MOTION_SHOWCASE_MOODS: [string, string][] = [
  ["neutral", "idle"], ["happy", "idle"], ["sad", "idle"], ["angry", "idle"], ["surprised", "idle"],
];
const MOTION_SHOWCASE_GESTURES: [string, string][] = [
  ["happy", "wave"], ["happy", "point"], ["happy", "nod"], ["neutral", "think"],
  ["happy", "fly"], ["surprised", "jump"], ["happy", "approach"],
];

// A sample multi-sentence reply exercising every emotion-cue rule in
// emotion-cues.ts, so the "beats within one answer" behavior is visible too:
// a question (head-tilt), an exclamation/offer (wings-out bounce), a
// greeting (wave), and a price mention (point) -- see splitIntoBeats.
const MOTION_SHOWCASE_REPLY =
  "Have you tried our mangoes? They're on special today! Hi there, welcome to the stall. The price is RM 5.";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Dev-only helper: plays every emotion's idle personality, every gesture's
 * motion, and a sample multi-sentence reply (to show emotion-cues.ts changing
 * pose mid-answer) back to back, so someone with the page open can watch the
 * full set without needing to script real conversation turns. Exposed on
 * `window` (not gated behind ?debug=true) so it's reachable from the devtools
 * console on an already-open tab. Not part of the Kiosk_UI's normal operation.
 */
function exposeMotionShowcase(renderer: CharacterRenderer): void {
  // Also expose the renderer itself so a single render(emotion, gesture) call
  // can be tried directly from the console, not just the full cycle.
  (window as unknown as { __vertewRenderer: CharacterRenderer }).__vertewRenderer = renderer;
  (window as unknown as { __vertewCycleMotions: () => Promise<void> }).__vertewCycleMotions = async () => {
    for (const [emotion, gesture] of MOTION_SHOWCASE_MOODS) {
      renderer.render(emotion, gesture);
      await sleep(6000); // long enough to see at least one idle flourish
    }
    for (const [emotion, gesture] of MOTION_SHOWCASE_GESTURES) {
      renderer.render(emotion, gesture);
      await sleep(2600); // just past GESTURE_HOLD_MS so it fully settles
    }
    const beats = splitIntoBeats(MOTION_SHOWCASE_REPLY, { emotion: "neutral", gesture: "idle" });
    for (const beat of beats) {
      renderer.render(beat.emotion, beat.gesture);
      await sleep(2600);
    }
    renderer.playIdle();
  };
}

export async function startKiosk(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const displayMode = params.get("display") === "tablet" ? "tablet" : "hologram";
  // Off by default -- the real 7" kiosk display never passes ?debug=true, so
  // the demo control panel only appears when explicitly requested for testing.
  const debug = params.get("debug") === "true";
  document.body.dataset.display = displayMode;
  document.body.classList.toggle("debug-enabled", debug);

  const [media, session, sttConfig] = await Promise.all([
    loadMediaConfig(STORE_ID),
    restoreSession(STORE_ID),
    getSttConfig(),
  ]);
  const sessionId = session.id;
  window.sessionStorage.setItem(`vertew-session-${STORE_ID}`, sessionId);
  const qrUrl = `${window.location.origin}/order/store/${STORE_ID}?session=${sessionId}`;
  await renderQr(qrUrl);

  const renderer = createCharacterRenderer();
  exposeMotionShowcase(renderer);
  // "local": record + POST to the backend (faster-whisper, fully offline) --
  // used when the browser's own Web Speech API can't reach Google's speech
  // service. Otherwise fall back to the browser's built-in recognizer.
  const stt: SttProvider =
    sttConfig.provider === "local" ? new LocalSttProvider("en-US") : new WebSpeechSttProvider("en-US");
  const tts = new WebSpeechTtsEngine("en-US");
  const view = createDomKioskView();
  const chatLog = createChatLog();
  const video = required<HTMLVideoElement>("hologram-video");
  const videoQueue = new EventVideoQueue(video, media, (state, playing) => {
    document.body.classList.toggle("video-playing", playing);
    document.body.dataset.hologramState = playing ? state : "interactive";
    required("state-label").textContent = playing ? media.assets[state]?.label ?? state : "Interactive";
  });

  const controller = new KioskController({
    stt, tts, renderer, view,
    onSendTranscript: (text) => {
      chatLog.addUser(text);
      chatLog.clearLiveCaption();
      void analyzeTranscript(sessionId, text)
        .then((result) => {
          currentLanguage = result.current_language;
          const locale = speechLocale(currentLanguage);
          stt.setLanguage(locale);
          tts.setLanguage(locale);
          required("language-label").textContent = currentLanguage.toUpperCase();
          chat.send(text);
        })
        .catch(() => chat.send(text));
    },
    onStateChange: (state) => { document.body.dataset.state = state; },
  });
  stt.onPartial?.((text) => chatLog.setLiveCaption(text));

  const chatPath = `/ws?session_id=${encodeURIComponent(sessionId)}`;
  const chat = new ChatClient({ url: buildWsUrl(window.location, chatPath) });
  chat.onResponse((response) => { controller.onServerResponse(response); chatLog.addCharacter(response.text); });
  chat.onNetworkError(() => controller.showError("network"));
  chat.connect();

  let currentLanguage = session.language;
  const announcedOrders = new Set<string>();
  const eventUrl = buildStoreWsUrl(sessionId, debug ? "debug" : "hologram");
  const events = new StoreEventClient(eventUrl, (event) => void handleEvent(event), (connected) => {
    required("connection-dot").classList.toggle("connected", connected);
  });
  events.connect();

  async function handleEvent(event: StoreEvent): Promise<void> {
    if (event.type === "customer_close") videoQueue.enqueue(event.type);
    // customer_detected used to play the greeting video; a 3D greeter flourish
    // (wave -> excited jump -> approach) draws attention instead, since
    // Higgsfield-generated video attempts from this line-art style came back
    // motionless (tested with two different models). Only while idle, so an
    // active conversation is never interrupted by a re-triggered sensor.
    if (event.type === "customer_detected" && controller.state === "idle") {
      void playGreeterSequence();
    }
    if (event.type === "show_qr") showQr(true);
    if (event.type === "language_changed" && event.session_id === sessionId) {
      currentLanguage = String(event.payload.language ?? "en");
      required("language-label").textContent = currentLanguage.toUpperCase();
      const locale = speechLocale(currentLanguage);
      stt.setLanguage(locale);
      tts.setLanguage(locale);
    }
    // The hologram is a shared store display: announce every ready order. The
    // event session identifies the customer/order, not the display connection.
    if (event.type === "order_ready") {
      const orderNumber = Number(event.payload.order_number);
      const language = String(event.payload.customer_language ?? currentLanguage);
      const orderId = String(event.payload.id ?? "");
      if (orderId && announcedOrders.has(orderId)) return;
      if (orderId) announcedOrders.add(orderId);
      await announceReady(orderNumber, language);
    }
    if (ORDER_BOARD_EVENTS.has(event.type)) void refreshOrderBoard();
  }

  // Attention-grabbing beat played once when a customer is first detected --
  // see the handleEvent comment above for why this is 3D motion, not video.
  const GREETER_SEQUENCE: [string, string][] = [
    ["happy", "wave"], ["surprised", "jump"], ["happy", "approach"],
  ];
  async function playGreeterSequence(): Promise<void> {
    for (const [emotion, gesture] of GREETER_SEQUENCE) {
      if (controller.state !== "idle") return; // a conversation started mid-sequence
      renderer.render(emotion, gesture);
      await sleep(2500);
    }
    if (controller.state === "idle") renderer.playIdle();
  }

  const ORDER_STATUS_LABEL: Record<string, string> = {
    PENDING: "NEW", ACCEPTED: "ACCEPTED", PREPARING: "PREPARING", READY: "READY",
  };
  const ORDER_STATUS_BADGE_CLASS: Record<string, string> = {
    PENDING: "order-badge--pending", ACCEPTED: "order-badge--accepted",
    PREPARING: "order-badge--preparing", READY: "order-badge--ready",
  };

  function renderOrderBoard(orders: BoardOrder[]): void {
    const list = required("order-board-list");
    list.textContent = "";
    if (orders.length === 0) {
      const empty = document.createElement("p");
      empty.className = "order-board-empty";
      empty.textContent = "No orders in progress.";
      list.appendChild(empty);
      return;
    }
    for (const order of [...orders].sort((a, b) => b.order_number - a.order_number)) {
      const card = document.createElement("article");
      card.className = "order-card";

      const head = document.createElement("div");
      head.className = "order-card-head";
      const num = document.createElement("b");
      num.textContent = `#${order.order_number}`;
      const badge = document.createElement("span");
      badge.className = `order-badge ${ORDER_STATUS_BADGE_CLASS[order.status] ?? "order-badge--pending"}`;
      badge.textContent = ORDER_STATUS_LABEL[order.status] ?? order.status;
      head.append(num, badge);

      const items = document.createElement("div");
      items.className = "order-items";
      items.textContent = order.items.map((item) => `${item.product_name.en ?? item.product_id} ×${item.quantity}`).join(", ");

      card.append(head, items);
      list.appendChild(card);
    }
  }

  const refreshOrderBoard = async (): Promise<void> => {
    try { renderOrderBoard(await getActiveOrders(STORE_ID)); }
    catch { /* keep showing the last known board; retried on the next tick */ }
  };

  let readyDismissTimer: ReturnType<typeof window.setTimeout> | null = null;
  async function announceReady(orderNumber: number, language: string): Promise<void> {
    const message = (readyCopy[language] ?? readyCopy.en)(orderNumber);
    required("ready-number").textContent = `#${orderNumber}`;
    required("ready-message").textContent = message;
    document.body.classList.add("order-ready");
    renderer.render("happy", "wave");
    const voice = new WebSpeechTtsEngine(language === "ko" ? "ko-KR" : language === "ms" ? "ms-MY" : "en-US");
    try { await voice.speak(message); } catch { /* large visual alert remains */ }
    if (readyDismissTimer !== null) window.clearTimeout(readyDismissTimer);
    readyDismissTimer = window.setTimeout(() => {
      document.body.classList.remove("order-ready");
      renderer.playIdle();
      readyDismissTimer = null;
    }, 10000);
  }

  required("stage").addEventListener("pointerdown", (event) => {
    if ((event.target as HTMLElement).closest("button, select, .qr-panel")) return;
    if (!videoQueue.playing) controller.onTap();
  });
  required("qr-panel").addEventListener("click", () => showQr(true));
  required("qr-close").addEventListener("click", () => showQr(false));
  required("mode-toggle").addEventListener("click", () => {
    const next = document.body.dataset.display === "hologram" ? "tablet" : "hologram";
    document.body.dataset.display = next;
    required("mode-toggle").textContent = next === "hologram" ? "Tablet Mode" : "Hologram Mode";
  });
  document.querySelectorAll<HTMLButtonElement>("[data-debug-event]").forEach((button) => {
    button.addEventListener("click", () => events.send(button.dataset.debugEvent ?? "", { distance: Number(button.dataset.distance || 0) }));
  });
  required("debug-ready").addEventListener("click", () => void announceReady(12, currentLanguage));
  required("debug-ko").addEventListener("click", () => { currentLanguage = "ko"; required("language-label").textContent = "KO"; });
  required("debug-en").addEventListener("click", () => { currentLanguage = "en"; required("language-label").textContent = "EN"; });
  required("debug-ms").addEventListener("click", () => { currentLanguage = "ms"; required("language-label").textContent = "MS"; });
  required("debug-transcript-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = required<HTMLInputElement>("debug-transcript-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    // Bypass the mic entirely: fake a tap (if needed) then feed the typed text
    // straight in as if the Speech_Module had produced it, so the rest of the
    // flow (server round-trip, character render, TTS) can be tested without STT.
    if (controller.state === "idle") controller.onTap();
    controller.onTranscript(text);
  });
  const recoverReadyOrder = async () => {
    try {
      const readyOrders = await getReadyOrders(STORE_ID);
      for (const order of readyOrders.reverse()) {
        if (announcedOrders.has(order.id)) continue;
        announcedOrders.add(order.id);
        await announceReady(order.order_number, order.customer_language || currentLanguage);
      }
    } catch { /* WebSocket remains the primary path; retry on the next interval. */ }
  };
  window.setInterval(() => void recoverReadyOrder(), 3000);
  void recoverReadyOrder();
  window.setInterval(() => void refreshOrderBoard(), 3000);
  void refreshOrderBoard();
  renderer.playIdle();
}

async function restoreSession(storeId: string): Promise<{ id: string; language: string; order_id?: string | null }> {
  const key = `vertew-session-${storeId}`;
  // sessionStorage survives a page refresh (so READY events still match) but a
  // newly opened kiosk session starts clean for the next customer. Migrate the
  // earlier localStorage value once so an order already in progress is not lost.
  const existing = window.sessionStorage.getItem(key) ?? window.localStorage.getItem(key);
  if (existing) {
    try {
      const session = await getCustomerSession(existing);
      window.sessionStorage.setItem(key, existing);
      window.localStorage.removeItem(key);
      return session;
    }
    catch { window.sessionStorage.removeItem(key); window.localStorage.removeItem(key); }
  }
  return createCustomerSession(storeId);
}

async function renderQr(value: string): Promise<void> {
  const dataUrl = await QRCode.toDataURL(value, { width: 420, margin: 2, color: { dark: "#05060aff", light: "#ffffffff" } });
  document.querySelectorAll<HTMLImageElement>(".session-qr").forEach((image) => { image.src = dataUrl; });
  // Also show the plain link so the same machine can open/copy it directly
  // without needing an actual phone to scan the code (handy for dev/testing).
  const link = document.getElementById("qr-link") as HTMLAnchorElement | null;
  if (link) { link.href = value; link.textContent = value; }
}

function showQr(show: boolean): void { document.body.classList.toggle("qr-expanded", show); }
function speechLocale(language: string): string {
  return language === "ko" ? "ko-KR" : language === "ms" ? "ms-MY" : "en-US";
}
function buildStoreWsUrl(sessionId: string, client: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/store/${STORE_ID}?client=${client}&session_id=${sessionId}`;
}
function required<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing #${id}`);
  return element as T;
}

if (typeof document !== "undefined") {
  const boot = () => void startKiosk().catch((error) => {
    document.body.dataset.state = "error";
    const banner = document.getElementById("boot-error");
    if (banner) { banner.textContent = error instanceof Error ? error.message : "Hologram failed to start"; banner.classList.add("visible"); }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
}

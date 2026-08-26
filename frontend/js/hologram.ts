export interface MediaAsset {
  type: "video" | "image";
  url: string;
  label: string;
}

export interface MediaConfig {
  store_id: string;
  assets: Record<string, MediaAsset>;
}

export interface StoreEvent {
  type: string;
  store_id: string;
  session_id: string | null;
  payload: Record<string, unknown>;
}

export class EventVideoQueue {
  private queue: string[] = [];
  private current: string | null = null;

  constructor(
    private readonly video: HTMLVideoElement,
    private readonly config: MediaConfig,
    private readonly onState: (state: string, playing: boolean) => void,
  ) {
    video.addEventListener("ended", () => this.finish());
    video.addEventListener("error", () => this.finish());
  }

  enqueue(eventType: string): void {
    const asset = this.config.assets[eventType];
    if (!asset || asset.type !== "video") return;
    if (this.current === eventType || this.queue.includes(eventType)) return;
    this.queue.push(eventType);
    if (this.current === null) void this.playNext();
  }

  get playing(): boolean { return this.current !== null; }

  private async playNext(): Promise<void> {
    const eventType = this.queue.shift();
    if (!eventType) { this.current = null; return; }
    const asset = this.config.assets[eventType];
    this.current = eventType;
    this.onState(eventType, true);
    this.video.src = asset.url;
    this.video.currentTime = 0;
    try { await this.video.play(); }
    catch { this.finish(); }
  }

  private finish(): void {
    const completed = this.current;
    this.current = null;
    if (completed) this.onState(completed, false);
    void this.playNext();
  }
}

export class StoreEventClient {
  private socket: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly onEvent: (event: StoreEvent) => void,
    private readonly onConnection: (connected: boolean) => void,
  ) {}

  connect(): void {
    this.stopped = false;
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => this.onConnection(true);
    this.socket.onmessage = (message) => {
      try { this.onEvent(JSON.parse(String(message.data)) as StoreEvent); }
      catch { /* ignore malformed debug frames */ }
    };
    this.socket.onerror = () => this.socket?.close();
    this.socket.onclose = () => {
      this.onConnection(false);
      this.socket = null;
      if (!this.stopped) this.retry = setTimeout(() => this.connect(), 1500);
    };
  }

  send(type: string, payload: Record<string, unknown> = {}): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type, payload }));
    }
  }

  close(): void {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    this.socket?.close();
  }
}

export async function loadMediaConfig(storeId: string): Promise<MediaConfig> {
  const response = await fetch(`/api/stores/${storeId}/media`);
  if (!response.ok) throw new Error("Could not load hologram media configuration");
  return response.json() as Promise<MediaConfig>;
}

/**
 * Which STT backend the Kiosk_UI should use ("browser" | "local"; see
 * backend/stt.py). Degrades to "browser" -- the historical default -- on any
 * failure rather than throwing, so a hiccup here never blocks kiosk boot.
 */
export async function getSttConfig(): Promise<{ provider: string }> {
  try {
    const response = await fetch("/api/stt/config");
    if (!response.ok) return { provider: "browser" };
    return (await response.json()) as { provider: string };
  } catch {
    return { provider: "browser" };
  }
}

export async function createCustomerSession(storeId: string): Promise<{ id: string; language: string }> {
  const response = await fetch("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ store_id: storeId }),
  });
  if (!response.ok) throw new Error("Could not create customer session");
  return response.json() as Promise<{ id: string; language: string }>;
}

export async function getCustomerSession(sessionId: string): Promise<{ id: string; language: string; order_id: string | null }> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error("Could not load customer session");
  return response.json() as Promise<{ id: string; language: string; order_id: string | null }>;
}

export async function updateCustomerLanguage(
  sessionId: string,
  language: "en" | "ko" | "ms",
): Promise<{ id: string; language: string }> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/language`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language, language_source: "user_selected" }),
  });
  if (!response.ok) throw new Error("Could not update customer language");
  return response.json() as Promise<{ id: string; language: string }>;
}

export async function getOrder(orderId: string): Promise<{ id: string; status: string; order_number: number; customer_language: string }> {
  const response = await fetch(`/api/orders/${encodeURIComponent(orderId)}`);
  if (!response.ok) throw new Error("Could not load order");
  return response.json() as Promise<{ id: string; status: string; order_number: number; customer_language: string }>;
}

export async function getReadyOrders(storeId: string): Promise<Array<{ id: string; status: string; order_number: number; customer_language: string }>> {
  const response = await fetch(`/api/stores/${encodeURIComponent(storeId)}/orders?status=READY`);
  if (!response.ok) throw new Error("Could not load ready orders");
  const data = await response.json() as { orders: Array<{ id: string; status: string; order_number: number; customer_language: string }> };
  return data.orders;
}

/** One order as shown on the hologram's read-only order-status board. */
export interface BoardOrder {
  id: string;
  order_number: number;
  status: "PENDING" | "ACCEPTED" | "PREPARING" | "READY" | "COMPLETED" | "REJECTED";
  items: Array<{ product_id: string; quantity: number; product_name: Record<string, string> }>;
}

const BOARD_STATUSES = new Set(["PENDING", "ACCEPTED", "PREPARING", "READY"]);

/** Every order the vendor is still working (excludes COMPLETED/REJECTED) --
 * what waiting customers can check on the shared hologram display. */
export async function getActiveOrders(storeId: string): Promise<BoardOrder[]> {
  const response = await fetch(`/api/stores/${encodeURIComponent(storeId)}/orders`);
  if (!response.ok) throw new Error("Could not load store orders");
  const data = await response.json() as { orders: BoardOrder[] };
  return data.orders.filter((order) => BOARD_STATUSES.has(order.status));
}

export async function analyzeTranscript(
  sessionId: string,
  transcript: string,
  detectedLanguage?: string,
  languageConfidence?: number,
): Promise<{ language: string; confidence: number; intent: string; current_language: string }> {
  const response = await fetch("/api/ai/analyze-transcript", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      transcript,
      detected_language: detectedLanguage,
      language_confidence: languageConfidence,
    }),
  });
  if (!response.ok) throw new Error("Could not analyze transcript");
  return response.json() as Promise<{
    language: string; confidence: number; intent: string; current_language: string;
  }>;
}

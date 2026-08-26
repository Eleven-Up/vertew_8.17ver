import { useEffect, useRef, useState } from "react";
import { checkoutDraft, createSession, getDraft, getOrder, getSession, setLanguage, updateDraft } from "./api";
import { copy } from "./i18n";
import type { Draft, Language, Order, Session } from "./types";

// The customer orders by talking to Vertew; this page is reached by scanning
// the payment QR Vertew shows once it recognizes an order, and only reviews +
// edits (quantity/remove) + pays that draft -- it never lets the customer
// browse the full menu, by design (ordering happens through the conversation).
type Screen = "review" | "paying" | "complete";

// No real payment gateway is wired up (mock/demo payment): a brief simulated
// processing delay stands in for a card/QR-pay charge before the draft is
// actually turned into a real order.
const MOCK_PAYMENT_DELAY_MS = 900;

// How often to re-fetch the draft while reviewing, since Vertew can keep
// recognizing new items by voice while this page stays open on the customer's
// phone -- there's no live push for the draft itself (unlike order-ready).
const DRAFT_POLL_MS = 2000;

const dishEmoji: Record<string, string> = {
  nasi_lemak: "🍛", tteokbokki: "🌶️", nasi_goreng: "🍚",
  beef_noodle_soup: "🍜", hainan_chicken_rice: "🍗",
};

function storeIdFromPath(): string {
  const match = window.location.pathname.match(/\/store\/([^/]+)/);
  return match?.[1] ?? "demo";
}

function money(minor: number): string {
  return `RM ${(minor / 100).toFixed(2)}`;
}

function storeWsUrl(storeId: string, sessionId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/store/${storeId}?client=customer&session_id=${encodeURIComponent(sessionId)}`;
}

function draftTotal(draft: Draft): number {
  return draft.items.reduce((sum, item) => sum + item.unit_price_minor * item.quantity, 0);
}

export function App() {
  const storeId = storeIdFromPath();
  const [session, setSession] = useState<Session | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [screen, setScreen] = useState<Screen>("review");
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [readyOrder, setReadyOrder] = useState<Order | null>(null);
  // Product ids whose photo failed to load, so the cart falls back to the
  // dish emoji instead of a broken-image icon.
  const [brokenImages, setBrokenImages] = useState<Set<string>>(new Set());
  const dismissedReady = useRef(new Set<string>());
  // Special-request text the customer is actively typing, keyed by product_id
  // -- kept separate from `draft` so the 2s background poll (which may bring
  // back items recognized by voice) never clobbers an in-progress edit before
  // it's saved on blur.
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});

  const language = session?.language ?? "en";
  const t = copy(language);

  useEffect(() => {
    void bootstrap();
  }, []);

  // Live "order ready" push (unaffected by the payment-flow change above).
  useEffect(() => {
    if (!session) return;
    let disposed = false;
    let socket: WebSocket | null = null;
    let retry: number | null = null;
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(storeWsUrl(storeId, session.id));
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(String(message.data)) as { type: string; session_id: string | null; payload: Order };
          if (event.type !== "order_ready" || event.session_id !== session.id) return;
          if (!dismissedReady.current.has(event.payload.id)) setReadyOrder(event.payload);
        } catch { /* Ignore malformed event frames. */ }
      };
      socket.onclose = () => { if (!disposed) retry = window.setTimeout(connect, 1500); };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { disposed = true; socket?.close(); if (retry) window.clearTimeout(retry); };
  }, [session?.id, storeId]);

  useEffect(() => {
    if (!order) return;
    const check = async () => {
      try {
        const latest = await getOrder(order.id);
        if (latest.status === "READY" && !dismissedReady.current.has(latest.id)) setReadyOrder(latest);
      } catch { /* The WebSocket remains the primary notification path. */ }
    };
    void check();
    const timer = window.setInterval(() => void check(), 3000);
    return () => window.clearInterval(timer);
  }, [order?.id]);

  // Keep the draft in sync while reviewing -- Vertew may still be recognizing
  // items by voice even after the customer opens this page.
  useEffect(() => {
    if (!session || screen !== "review") return;
    let cancelled = false;
    const load = async () => {
      try {
        const latest = await getDraft(session.id);
        if (!cancelled) setDraft(latest);
      } catch { /* Keep showing the last known draft; retried on the next tick. */ }
    };
    void load();
    const timer = window.setInterval(load, DRAFT_POLL_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [session?.id, screen]);

  async function bootstrap() {
    setBusy(true);
    setError("");
    try {
      const params = new URLSearchParams(window.location.search);
      const requestedId = params.get("session");
      let activeSession: Session;
      try {
        activeSession = requestedId ? await getSession(requestedId) : await createSession(storeId);
        if (activeSession.store_id !== storeId) throw new Error("Session belongs to another store");
      } catch {
        activeSession = await createSession(storeId);
      }
      params.set("session", activeSession.id);
      window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
      setSession(activeSession);
      setDraft(await getDraft(activeSession.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load your order");
    } finally {
      setBusy(false);
    }
  }

  async function changeQuantity(productId: string, delta: number) {
    if (!session || !draft) return;
    const nextItems = draft.items
      .map((item) => (item.product_id === productId ? { ...item, quantity: item.quantity + delta } : item))
      .filter((item) => item.quantity > 0);
    setDraft({ ...draft, items: nextItems, total_minor: draftTotal({ ...draft, items: nextItems }) }); // optimistic
    try {
      setDraft(await updateDraft(session.id, nextItems.map((item) => ({ product_id: item.product_id, quantity: item.quantity, note: item.note }))));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update your order");
    }
  }

  function removeItem(productId: string) {
    if (!draft) return;
    void changeQuantity(productId, -(draft.items.find((item) => item.product_id === productId)?.quantity ?? 0));
  }

  // Persists the special-request text typed for one item once the customer
  // leaves the field, so a keystroke-per-request round trip isn't needed.
  async function saveNote(productId: string) {
    if (!session || !draft) return;
    const note = noteDrafts[productId] ?? "";
    const nextItems = draft.items.map((item) => (item.product_id === productId ? { ...item, note } : item));
    setDraft({ ...draft, items: nextItems });
    setNoteDrafts((prev) => {
      const { [productId]: _omit, ...rest } = prev;
      return rest;
    });
    try {
      setDraft(await updateDraft(session.id, nextItems.map((item) => ({ product_id: item.product_id, quantity: item.quantity, note: item.note }))));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update your order");
    }
  }

  async function changeLanguage(language: Language) {
    if (!session) return;
    try {
      setSession(await setLanguage(session.id, language));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not change language");
    }
  }

  async function payAndCheckout() {
    if (!session || !draft || draft.items.length === 0) return;
    setError("");
    setScreen("paying");
    window.scrollTo(0, 0);
    // Simulated payment processing (no real gateway) so the moment reads as an
    // actual charge rather than an instant, unconvincing jump to "complete".
    await new Promise((resolve) => setTimeout(resolve, MOCK_PAYMENT_DELAY_MS));
    try {
      const created = await checkoutDraft(session.id);
      setOrder(created);
      setScreen("complete");
      window.scrollTo(0, 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not place the order");
      setScreen("review");
    }
  }

  function acknowledgeReady() {
    if (readyOrder) dismissedReady.current.add(readyOrder.id);
    setReadyOrder(null);
  }

  if (busy && !session) {
    return <main className="center"><div className="loader"/><p>{t.loading}</p></main>;
  }
  if (error && !session) {
    return <main className="center"><p className="error">{error}</p><button onClick={() => void bootstrap()}>{t.retry}</button></main>;
  }

  return (
    <div className="app-shell">
      {readyOrder && <div className="ready-modal" role="dialog" aria-modal="true" aria-labelledby="ready-title">
        <div className="ready-card">
          <div className="ready-icon">✓</div>
          <p>ORDER READY</p>
          <h1 id="ready-title">{t.readyTitle}</h1>
          <strong>#{readyOrder.order_number}</strong>
          <span>{t.readyBody}</span>
          <button onClick={acknowledgeReady}>{t.acknowledge}</button>
        </div>
      </div>}
      <header>
        <div><div className="brand">VERTEW</div><div className="tagline">{t.fresh}</div></div>
        <label className="language"><span>{t.language}</span>
          <select value={language} onChange={(event) => void changeLanguage(event.target.value as Language)}>
            <option value="en">English</option><option value="ko">한국어</option><option value="ms">Bahasa Melayu</option>
          </select>
        </label>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      {screen === "review" && <main className="order-panel">
        <h1>{t.yourOrder}</h1>
        {!draft || draft.items.length === 0 ? (
          <p className="empty">{t.noDraftYet}</p>
        ) : (
          <>
            {draft.items.map((item) => (
              <div className="cart-item" key={item.product_id}>
                <div className="cart-row">
                  {item.image && !brokenImages.has(item.product_id) ? (
                    <img
                      className="cart-dish-image"
                      src={item.image}
                      alt={item.name[language] ?? item.name.en}
                      onError={() => setBrokenImages((prev) => new Set(prev).add(item.product_id))}
                    />
                  ) : (
                    <span className="cart-dish">{dishEmoji[item.product_id] ?? "🍽️"}</span>
                  )}
                  <div><strong>{item.name[language] ?? item.name.en}</strong><small>{money(item.unit_price_minor)} {t.each}</small></div>
                  <div className="stepper">
                    <button onClick={() => void changeQuantity(item.product_id, -1)}>−</button>
                    <b>{item.quantity}</b>
                    <button onClick={() => void changeQuantity(item.product_id, 1)}>+</button>
                  </div>
                  <button className="remove" aria-label="Remove" onClick={() => removeItem(item.product_id)}>✕</button>
                </div>
                <input
                  className="cart-note"
                  type="text"
                  maxLength={200}
                  placeholder={t.notePlaceholder}
                  value={noteDrafts[item.product_id] ?? item.note}
                  onChange={(event) => setNoteDrafts((prev) => ({ ...prev, [item.product_id]: event.target.value }))}
                  onBlur={() => void saveNote(item.product_id)}
                />
              </div>
            ))}
            <div className="total"><span>{t.total}</span><strong>{money(draftTotal(draft))}</strong></div>
            <button className="wide" onClick={() => void payAndCheckout()}>{t.pay}</button>
          </>
        )}
      </main>}

      {screen === "paying" && <main className="center">
        <div className="loader" /><p>{t.paying}</p>
      </main>}

      {screen === "complete" && order && <main className="complete">
        <div className="check">✓</div><h1>{t.paid}</h1><p>{t.complete}</p><p>{t.number}</p><div className="order-number">#{order.order_number}</div><p>{t.wait}<br/>{t.notify}</p>
      </main>}
    </div>
  );
}

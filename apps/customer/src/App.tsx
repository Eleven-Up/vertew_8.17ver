import { useEffect, useMemo, useRef, useState } from "react";
import { createOrder, createSession, getMenu, getOrder, getSession, setLanguage } from "./api";
import { copy } from "./i18n";
import type { Cart, Language, Order, Product, Session } from "./types";

type Screen = "menu" | "cart" | "confirm" | "complete";

const fruitEmoji: Record<string, string> = {
  watermelon: "🍉", mango: "🥭", banana: "🍌", apple: "🍎",
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

export function App() {
  const storeId = storeIdFromPath();
  const [session, setSession] = useState<Session | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [cart, setCart] = useState<Cart>({});
  const [screen, setScreen] = useState<Screen>("menu");
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [readyOrder, setReadyOrder] = useState<Order | null>(null);
  const dismissedReady = useRef(new Set<string>());

  const language = session?.language ?? "en";
  const t = copy(language);
  const selectedProducts = useMemo(
    () => products.filter((product) => (cart[product.id] ?? 0) > 0),
    [cart, products],
  );
  const count = Object.values(cart).reduce((sum, quantity) => sum + quantity, 0);
  const total = selectedProducts.reduce(
    (sum, product) => sum + product.price_minor * cart[product.id], 0,
  );

  useEffect(() => {
    void bootstrap();
  }, []);

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
      const menu = await getMenu(storeId);
      setSession(activeSession);
      setProducts(menu.filter((product) => product.available));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the menu");
    } finally {
      setBusy(false);
    }
  }

  function changeQuantity(productId: string, delta: number) {
    setCart((current) => {
      const next = Math.max(0, (current[productId] ?? 0) + delta);
      const updated = { ...current };
      if (next === 0) delete updated[productId];
      else updated[productId] = next;
      return updated;
    });
  }

  async function changeLanguage(language: Language) {
    if (!session) return;
    try {
      setSession(await setLanguage(session.id, language));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not change language");
    }
  }

  function orderNow(productId: string) {
    setCart({ [productId]: 1 });
    setScreen("confirm");
    window.scrollTo(0, 0);
  }

  async function submitOrder() {
    if (!session || selectedProducts.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const created = await createOrder(storeId, session, cart);
      setOrder(created);
      setScreen("complete");
      setCart({});
      window.scrollTo(0, 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not place the order");
    } finally {
      setBusy(false);
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

      {screen === "menu" && <>
        <section className="hero"><span>FRESH</span><h1>{t.fresh}</h1><p>Watermelon · Mango · Banana · Apple</p></section>
        <main className="product-grid">
          {products.map((product) => <article className="product-card" key={product.id}>
            <div className="fruit" aria-hidden="true">{fruitEmoji[product.id] ?? "🍏"}</div>
            <div className="product-copy"><h2>{product.name[language]}</h2><p>{product.description[language]}</p><strong>{money(product.price_minor)}</strong></div>
            <div className="product-actions">
              <button className="secondary" onClick={() => changeQuantity(product.id, 1)}>{t.add}</button>
              <button onClick={() => orderNow(product.id)}>{t.orderNow}</button>
            </div>
          </article>)}
        </main>
      </>}

      {(screen === "cart" || screen === "confirm") && <main className="order-panel">
        <button className="back" onClick={() => setScreen(screen === "cart" ? "menu" : count > 1 ? "cart" : "menu")}>← {t.back}</button>
        <h1>{screen === "confirm" ? t.confirm : t.cart}</h1>
        {selectedProducts.length === 0 ? <p className="empty">{t.empty}</p> : selectedProducts.map((product) =>
          <div className="cart-row" key={product.id}>
            <span className="cart-fruit">{fruitEmoji[product.id]}</span>
            <div><strong>{product.name[language]}</strong><small>{money(product.price_minor)} {t.each}</small></div>
            {screen === "cart" ? <div className="stepper"><button onClick={() => changeQuantity(product.id, -1)}>−</button><b>{cart[product.id]}</b><button onClick={() => changeQuantity(product.id, 1)}>+</button></div> : <b>× {cart[product.id]}</b>}
          </div>)}
        <div className="total"><span>{t.total}</span><strong>{money(total)}</strong></div>
        {screen === "cart" ? <button className="wide" disabled={!count} onClick={() => setScreen("confirm")}>{t.place}</button> : <button className="wide" disabled={busy || !count} onClick={() => void submitOrder()}>{t.confirm}</button>}
      </main>}

      {screen === "complete" && order && <main className="complete">
        <div className="check">✓</div><h1>{t.complete}</h1><p>{t.number}</p><div className="order-number">#{order.order_number}</div><p>{t.wait}<br/>{t.notify}</p>
      </main>}

      {screen === "menu" && <button className="cart-button" onClick={() => setScreen("cart")}><span>🛒</span>{t.cart}<b>{count}</b></button>}
    </div>
  );
}

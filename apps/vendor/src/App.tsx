import { useEffect, useMemo, useRef, useState } from "react";
import { changeOrderStatus, getOrders } from "./api";
import type { Order, OrderStatus, StoreEvent } from "./types";

const storeId = new URLSearchParams(window.location.search).get("store") ?? "demo";
const eventTypes = new Set(["new_order", "order_accepted", "order_rejected", "order_preparing", "order_ready", "order_completed"]);
const languageNames: Record<string, string> = { en: "English", ko: "Korean", ms: "Malay" };

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/store/${storeId}?client=vendor`;
}

function playNotification() {
  const AudioContextClass = window.AudioContext;
  if (!AudioContextClass) return;
  const context = new AudioContextClass();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.frequency.setValueAtTime(880, context.currentTime);
  oscillator.frequency.setValueAtTime(1174, context.currentTime + .12);
  gain.gain.setValueAtTime(.12, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(.001, context.currentTime + .32);
  oscillator.connect(gain).connect(context.destination);
  oscillator.start(); oscillator.stop(context.currentTime + .32);
  oscillator.onended = () => void context.close();
}

function upsert(orders: Order[], incoming: Order): Order[] {
  const remaining = orders.filter((order) => order.id !== incoming.id);
  return [incoming, ...remaining].sort((a, b) => b.order_number - a.order_number);
}

export function App() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [toast, setToast] = useState<Order | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);
  const reconnectTimer = useRef<number | null>(null);

  const active = useMemo(() => orders.filter((order) => !["COMPLETED", "REJECTED"].includes(order.status)), [orders]);
  const history = useMemo(() => orders.filter((order) => ["COMPLETED", "REJECTED"].includes(order.status)), [orders]);
  const counts = useMemo(() => ({ pending: active.filter((o) => o.status === "PENDING").length, preparing: active.filter((o) => ["ACCEPTED", "PREPARING"].includes(o.status)).length, ready: active.filter((o) => o.status === "READY").length }), [active]);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    void load();
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(wsUrl());
      socket.onopen = () => setConnected(true);
      socket.onmessage = (message) => {
        const event = JSON.parse(String(message.data)) as StoreEvent;
        if (!eventTypes.has(event.type)) return;
        const order = event.payload as Order;
        setOrders((current) => upsert(current, order));
        if (event.type === "new_order") {
          setToast(order); playNotification();
          window.setTimeout(() => setToast(null), 5000);
        }
      };
      socket.onclose = () => { setConnected(false); if (!disposed) reconnectTimer.current = window.setTimeout(connect, 1500); };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { disposed = true; socket?.close(); if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current); };
  }, []);

  async function load() {
    setLoading(true); setError("");
    try { setOrders(await getOrders(storeId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load orders"); }
    finally { setLoading(false); }
  }

  async function transition(order: Order, status: OrderStatus) {
    setUpdating(order.id); setError("");
    try {
      const updated = await changeOrderStatus(order.id, status);
      setOrders((current) => upsert(current, updated));
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update order"); }
    finally { setUpdating(null); }
  }

  return <div className="dashboard">
    <header>
      <div><div className="brand">VERTEW</div><h1>Vendor Dashboard</h1></div>
      <div className={`connection ${connected ? "online" : "offline"}`}><i/>{connected ? "LIVE" : "RECONNECTING"}</div>
    </header>

    {toast && <div className="toast"><span>🔔</span><div><b>New Order #{toast.order_number}</b><small>{summary(toast)}</small></div></div>}
    {error && <div className="error" role="alert">{error}<button onClick={() => void load()}>Retry</button></div>}

    <section className="stats">
      <div><span>NEW</span><strong>{counts.pending}</strong></div>
      <div><span>PREPARING</span><strong>{counts.preparing}</strong></div>
      <div><span>READY</span><strong>{counts.ready}</strong></div>
    </section>

    <nav><button className={!historyOpen ? "active" : ""} onClick={() => setHistoryOpen(false)}>Active Orders <b>{active.length}</b></button><button className={historyOpen ? "active" : ""} onClick={() => setHistoryOpen(true)}>History <b>{history.length}</b></button></nav>

    <main>
      {loading ? <div className="empty"><div className="loader"/>Loading orders…</div> : (historyOpen ? history : active).length === 0 ? <div className="empty">{historyOpen ? "No completed orders yet." : "Waiting for new orders…"}</div> : <div className="orders">{(historyOpen ? history : active).map((order) => <OrderCard key={order.id} order={order} busy={updating === order.id} transition={transition}/>)}</div>}
    </main>
  </div>;
}

function OrderCard({ order, busy, transition }: { order: Order; busy: boolean; transition: (order: Order, status: OrderStatus) => Promise<void> }) {
  return <article className={`order-card status-${order.status.toLowerCase()}`}>
    <div className="order-head"><div><span className="order-label">ORDER</span><h2>#{order.order_number}</h2></div><span className="status">{label(order.status)}</span></div>
    <div className="items">{order.items.map((item) => <div key={item.product_id}><span>{item.product_name.en}</span><strong>× {item.quantity}</strong></div>)}</div>
    <div className="meta"><span>🌐 {languageNames[order.customer_language] ?? order.customer_language}</span><span>{new Date(order.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>
    <div className="actions">{actions(order).map((action) => <button key={action.status} disabled={busy} className={action.className} onClick={() => void transition(order, action.status)}>{busy ? "Updating…" : action.label}</button>)}</div>
  </article>;
}

function actions(order: Order): { status: OrderStatus; label: string; className?: string }[] {
  switch (order.status) {
    case "PENDING": return [{ status: "REJECTED", label: "Reject", className: "reject" }, { status: "ACCEPTED", label: "Accept" }];
    case "ACCEPTED": return [{ status: "PREPARING", label: "Start Preparing" }];
    case "PREPARING": return [{ status: "READY", label: "Ready" }];
    case "READY": return [{ status: "COMPLETED", label: "Complete" }];
    default: return [];
  }
}

function label(status: OrderStatus): string { return ({ PENDING: "NEW ORDER", ACCEPTED: "ACCEPTED", PREPARING: "PREPARING", READY: "READY", COMPLETED: "COMPLETED", REJECTED: "REJECTED" })[status]; }
function summary(order: Order): string { return order.items.map((item) => `${item.product_name.en} × ${item.quantity}`).join(", "); }

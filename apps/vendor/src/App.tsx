import { useEffect, useMemo, useRef, useState } from "react";
import { changeOrderStatus, createProduct, deleteProduct, getMenu, getOrders, updateProduct } from "./api";
import type { CallVendorPayload, Order, OrderStatus, Product, ProductWrite, StoreEvent, VendorCall } from "./types";

type View = "orders" | "menu";

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
  const [view, setView] = useState<View>("orders");
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [toast, setToast] = useState<Order | null>(null);
  const [calls, setCalls] = useState<VendorCall[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const callSeq = useRef(0);

  const dismissCall = (id: string) => setCalls((current) => current.filter((call) => call.id !== id));

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
        if (event.type === "call_vendor") {
          const payload = event.payload as CallVendorPayload;
          const call: VendorCall = {
            id: `${event.timestamp}-${callSeq.current++}`,
            question: payload?.question ?? "",
            language: payload?.language ?? "",
            at: event.timestamp,
            sessionId: event.session_id,
          };
          setCalls((current) => [call, ...current]);
          playNotification();
          return;
        }
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

    <nav className="view-tabs">
      <button className={view === "orders" ? "active" : ""} onClick={() => setView("orders")}>Orders</button>
      <button className={view === "menu" ? "active" : ""} onClick={() => setView("menu")}>Menu</button>
    </nav>

    {view === "menu" ? <MenuManager storeId={storeId}/> : <>
      {toast && <div className="toast"><span>💰</span><div><b>Payment received · Order #{toast.order_number}</b><small>Prepare: {summary(toast)}</small></div></div>}
      {error && <div className="error" role="alert">{error}<button onClick={() => void load()}>Retry</button></div>}

      {calls.length > 0 && <section className="calls" role="alert" aria-live="assertive">
        {calls.map((call) => <div className="call-card" key={call.id}>
          <span className="call-icon">🙋</span>
          <div className="call-body">
            <b>Customer needs you</b>
            <small>{call.question ? `“${call.question}”` : "A customer asked to speak with you."} · {languageNames[call.language] ?? call.language}</small>
          </div>
          <button onClick={() => dismissCall(call.id)}>Handled</button>
        </div>)}
      </section>}

      <section className="stats">
        <div><span>NEW</span><strong>{counts.pending}</strong></div>
        <div><span>PREPARING</span><strong>{counts.preparing}</strong></div>
        <div><span>READY</span><strong>{counts.ready}</strong></div>
      </section>

      <nav><button className={!historyOpen ? "active" : ""} onClick={() => setHistoryOpen(false)}>Active Orders <b>{active.length}</b></button><button className={historyOpen ? "active" : ""} onClick={() => setHistoryOpen(true)}>History <b>{history.length}</b></button></nav>

      <main>
        {loading ? <div className="empty"><div className="loader"/>Loading orders…</div> : (historyOpen ? history : active).length === 0 ? <div className="empty">{historyOpen ? "No completed orders yet." : "Waiting for new orders…"}</div> : <div className="orders">{(historyOpen ? history : active).map((order) => <OrderCard key={order.id} order={order} busy={updating === order.id} transition={transition}/>)}</div>}
      </main>
    </>}
  </div>;
}

function OrderCard({ order, busy, transition }: { order: Order; busy: boolean; transition: (order: Order, status: OrderStatus) => Promise<void> }) {
  return <article className={`order-card status-${order.status.toLowerCase()}`}>
    <div className="order-head"><div><span className="order-label">ORDER</span><h2>#{order.order_number}</h2></div><span className="status">{label(order.status)}</span></div>
    <div className="items">{order.items.map((item) => <div key={item.product_id} className="order-item">
      <div><span>{item.product_name.en}</span><strong>× {item.quantity}</strong></div>
      {item.note && <small className="item-note">📝 {item.note}</small>}
    </div>)}</div>
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

function label(status: OrderStatus): string { return ({ PENDING: "PAID · NEW", ACCEPTED: "ACCEPTED", PREPARING: "PREPARING", READY: "READY", COMPLETED: "COMPLETED", REJECTED: "REJECTED" })[status]; }
function summary(order: Order): string { return order.items.map((item) => `${item.product_name.en} × ${item.quantity}`).join(", "); }

// ---------------------------------------------------------------------------
// Menu tab: create/edit/delete menu items, set an example image URL, and
// manage per-item stock (reaching 0 auto-marks a listing unavailable -- see
// backend/db.py DataStore.create_order).
// ---------------------------------------------------------------------------

const EMPTY_PRODUCT_FORM: ProductWrite = {
  name_en: "", name_ko: "", name_ms: "",
  description_en: "", description_ko: "", description_ms: "",
  price_minor: 0, image: "", stock_count: 20, available: true,
  origin_en: "", origin_ko: "", origin_ms: "",
};

function productToForm(product: Product): ProductWrite {
  return {
    name_en: product.name.en ?? "", name_ko: product.name.ko ?? "", name_ms: product.name.ms ?? "",
    description_en: product.description.en ?? "", description_ko: product.description.ko ?? "", description_ms: product.description.ms ?? "",
    price_minor: product.price_minor, image: product.image, stock_count: product.stock_count, available: product.available,
    origin_en: product.origin?.en ?? "", origin_ko: product.origin?.ko ?? "", origin_ms: product.origin?.ms ?? "",
  };
}

function MenuManager({ storeId }: { storeId: string }) {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => { void load(); }, [storeId]);

  async function load() {
    setLoading(true); setError("");
    try { setProducts(await getMenu(storeId)); }
    catch (reason) {
      // A store with no products yet returns 404 from /menu -- that's an
      // empty menu here, not an error.
      if (reason instanceof Error && reason.message.includes("404")) setProducts([]);
      else setError(reason instanceof Error ? reason.message : "Could not load the menu");
    }
    finally { setLoading(false); }
  }

  async function saveNew(form: ProductWrite) {
    setBusy(true); setError("");
    try {
      const created = await createProduct(storeId, form);
      setProducts((current) => [...current, created]);
      setCreating(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not add the item"); }
    finally { setBusy(false); }
  }

  async function saveEdit(productId: string, form: ProductWrite) {
    setBusy(true); setError("");
    try {
      const updated = await updateProduct(storeId, productId, form);
      setProducts((current) => current.map((product) => (product.id === productId ? updated : product)));
      setEditingId(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not save changes"); }
    finally { setBusy(false); }
  }

  async function remove(productId: string) {
    if (!window.confirm("Remove this menu item? This cannot be undone.")) return;
    setBusy(true); setError("");
    try {
      await deleteProduct(storeId, productId);
      setProducts((current) => current.filter((product) => product.id !== productId));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not remove the item"); }
    finally { setBusy(false); }
  }

  return <section className="menu-manager">
    {error && <div className="error" role="alert">{error}</div>}
    {loading ? <div className="empty"><div className="loader"/>Loading menu…</div> : <>
      <div className="products">
        {products.map((product) => (
          editingId === product.id
            ? <ProductForm key={product.id} initial={productToForm(product)} busy={busy}
                onCancel={() => setEditingId(null)} onSubmit={(form) => void saveEdit(product.id, form)}/>
            : <ProductCard key={product.id} product={product}
                onEdit={() => setEditingId(product.id)} onDelete={() => void remove(product.id)}/>
        ))}
      </div>
      {creating
        ? <ProductForm initial={EMPTY_PRODUCT_FORM} busy={busy} onCancel={() => setCreating(false)} onSubmit={(form) => void saveNew(form)}/>
        : <button className="add-product" onClick={() => setCreating(true)}>+ Add menu item</button>}
    </>}
  </section>;
}

function ProductCard({ product, onEdit, onDelete }: { product: Product; onEdit: () => void; onDelete: () => void }) {
  return <article className={`product-card ${product.available ? "" : "out-of-stock"}`}>
    {product.image
      ? <img className="product-image" src={product.image} alt={product.name.en ?? product.id}/>
      : <div className="product-image placeholder">🍽️</div>}
    <div className="product-info">
      <h3>{product.name.en ?? product.id}</h3>
      {product.description.en && <p>{product.description.en}</p>}
      <div className="product-meta">
        <span>RM {(product.price_minor / 100).toFixed(2)}</span>
        <span className={product.stock_count === 0 ? "stock-empty" : ""}>Stock: {product.stock_count}</span>
        <span className={product.available ? "badge-available" : "badge-unavailable"}>{product.available ? "Available" : "Out of stock"}</span>
      </div>
      {product.origin?.en && <p className="product-origin">📍 {product.origin.en}</p>}
    </div>
    <div className="product-actions">
      <button onClick={onEdit}>Edit</button>
      <button className="reject" onClick={onDelete}>Delete</button>
    </div>
  </article>;
}

function ProductForm(
  { initial, busy, onCancel, onSubmit }:
  { initial: ProductWrite; busy: boolean; onCancel: () => void; onSubmit: (form: ProductWrite) => void },
) {
  const [form, setForm] = useState<ProductWrite>(initial);
  const priceMajor = (form.price_minor / 100).toFixed(2);

  function update<K extends keyof ProductWrite>(key: K, value: ProductWrite[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return <form className="product-form" onSubmit={(event) => { event.preventDefault(); onSubmit(form); }}>
    <div className="rowfields">
      <label>Name (English)<input required value={form.name_en} onChange={(e) => update("name_en", e.target.value)}/></label>
      <label>Name (한국어)<input value={form.name_ko} onChange={(e) => update("name_ko", e.target.value)}/></label>
      <label>Name (Melayu)<input value={form.name_ms} onChange={(e) => update("name_ms", e.target.value)}/></label>
    </div>
    <div className="rowfields">
      <label>Description (English)<input value={form.description_en} onChange={(e) => update("description_en", e.target.value)}/></label>
      <label>Description (한국어)<input value={form.description_ko} onChange={(e) => update("description_ko", e.target.value)}/></label>
      <label>Description (Melayu)<input value={form.description_ms} onChange={(e) => update("description_ms", e.target.value)}/></label>
    </div>
    <div className="rowfields">
      <label>Price (RM)<input type="number" min="0" step="0.01" value={priceMajor}
        onChange={(e) => update("price_minor", Math.round(Number(e.target.value || 0) * 100))}/></label>
      <label>Stock count<input type="number" min="0" value={form.stock_count}
        onChange={(e) => update("stock_count", Math.max(0, Number(e.target.value || 0)))}/></label>
      <label className="inline-check"><input type="checkbox" checked={form.available}
        onChange={(e) => update("available", e.target.checked)}/> Available</label>
    </div>
    <label>Image URL<input value={form.image} onChange={(e) => update("image", e.target.value)} placeholder="/images/mango.png or https://…"/></label>
    <div className="rowfields">
      <label>Origin (English)<input value={form.origin_en} onChange={(e) => update("origin_en", e.target.value)} placeholder="e.g. Sarawak, Malaysia"/></label>
      <label>Origin (한국어)<input value={form.origin_ko} onChange={(e) => update("origin_ko", e.target.value)}/></label>
      <label>Origin (Melayu)<input value={form.origin_ms} onChange={(e) => update("origin_ms", e.target.value)}/></label>
    </div>
    <div className="form-actions">
      <button type="button" className="secondary" onClick={onCancel} disabled={busy}>Cancel</button>
      <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
    </div>
  </form>;
}

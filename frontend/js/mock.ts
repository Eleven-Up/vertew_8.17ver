type OrderStatus = "ACCEPTED" | "PREPARING" | "READY" | "COMPLETED" | "REJECTED";
interface Session { id: string; language: string; }
interface Order { id: string; order_number: number; status: string; }

const storeId = "demo";
let session: Session;
let order: Order | null = null;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) { const body = await response.json().catch(() => ({})) as { detail?: string }; throw new Error(body.detail ?? `Request failed (${response.status})`); }
  return response.json() as Promise<T>;
}

async function boot() {
  session = await request<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ store_id: storeId }) });
  required<HTMLAnchorElement>("customer-link").href = `/order/store/${storeId}?session=${session.id}`;
  connectEvents(); bindControls(); updateOrder();
}

function connectEvents() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/store/${storeId}?client=debug&session_id=${session.id}`);
  socket.onopen = () => connection(true);
  socket.onclose = () => { connection(false); window.setTimeout(connectEvents,1500); };
  socket.onmessage = (message) => { const event = JSON.parse(String(message.data)) as { type:string; timestamp?:string; payload:unknown }; logEvent(event.type,event.payload,event.timestamp); };
}

function bindControls() {
  document.querySelectorAll<HTMLButtonElement>("[data-sensor]").forEach((button) => button.addEventListener("click", () => void run(async () => {
    await request(`/api/stores/${storeId}/debug/events`, { method:"POST", body:JSON.stringify({ event:button.dataset.sensor, distance:Number(button.dataset.distance || 0) || null, session_id:session.id }) });
  })));
  document.querySelectorAll<HTMLButtonElement>("[data-speech]").forEach((button) => button.addEventListener("click", () => void run(async () => {
    const result = await request<{ current_language:string; intent:string; confidence:number }>("/api/ai/analyze-transcript", { method:"POST", body:JSON.stringify({ session_id:session.id, transcript:button.dataset.speech }) });
    required("language").textContent = result.current_language.toUpperCase(); required("intent").textContent = `${result.intent} · ${(result.confidence*100).toFixed(0)}%`;
  })));
  required("create-order").addEventListener("click", () => void run(async () => {
    order = await request<Order>("/api/orders", { method:"POST", body:JSON.stringify({ store_id:storeId, session_id:session.id, items:[{ product_id:"nasi_goreng",quantity:1 }], customer_language:required("language").textContent?.toLowerCase() || "en", order_source:"qr" }) }); updateOrder();
  }));
  document.querySelectorAll<HTMLButtonElement>("[data-status]").forEach((button) => button.addEventListener("click", () => void run(async () => {
    if (!order) throw new Error("Create an order first");
    order = await request<Order>(`/api/orders/${order.id}/status`, { method:"PATCH", body:JSON.stringify({ status:button.dataset.status as OrderStatus }) }); updateOrder();
  })));
  required("clear-log").addEventListener("click", () => { required("event-log").replaceChildren(); });
}

function updateOrder() {
  required("order-number").textContent = order ? `#${order.order_number}` : "—";
  required("order-status").textContent = order?.status ?? "NO ORDER";
  document.querySelectorAll<HTMLButtonElement>("[data-status]").forEach((button) => { button.disabled = !order; });
}
async function run(action:()=>Promise<void>) { try { await action(); toast("Event sent successfully"); } catch(error) { toast(error instanceof Error?error.message:"Action failed"); } }
function logEvent(type:string,payload:unknown,timestamp?:string) { const li=document.createElement("li"); li.innerHTML=`<time>${new Date(timestamp??Date.now()).toLocaleTimeString()}</time><b>${escapeHtml(type)}</b><span>${escapeHtml(JSON.stringify(payload))}</span>`; required("event-log").prepend(li); }
function connection(online:boolean) { const el=required("connection"); el.classList.toggle("online",online); required("connection").querySelector("b")!.textContent=online?"LIVE":"RECONNECTING"; }
function toast(message:string) { const el=required("toast"); el.textContent=message; el.classList.add("visible"); window.setTimeout(()=>el.classList.remove("visible"),1800); }
function escapeHtml(value:string) { const div=document.createElement("div"); div.textContent=value; return div.innerHTML; }
function required<T extends HTMLElement=HTMLElement>(id:string):T { const el=document.getElementById(id); if(!el) throw new Error(`Missing #${id}`); return el as T; }

void boot().catch((error) => toast(error instanceof Error ? error.message : "Console failed to start"));

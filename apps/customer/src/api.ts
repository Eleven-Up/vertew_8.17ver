import type { Cart, Language, Order, Product, Session } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(data?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getMenu(storeId: string): Promise<Product[]> {
  const data = await request<{ products: Product[] }>(`/api/stores/${storeId}/menu`);
  return data.products;
}

export function getSession(sessionId: string): Promise<Session> {
  return request(`/api/sessions/${sessionId}`);
}

export function createSession(storeId: string): Promise<Session> {
  return request("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ store_id: storeId }),
  });
}

export function setLanguage(sessionId: string, language: Language): Promise<Session> {
  return request(`/api/sessions/${sessionId}/language`, {
    method: "PATCH",
    body: JSON.stringify({ language, language_source: "user_selected" }),
  });
}

export function createOrder(
  storeId: string,
  session: Session,
  cart: Cart,
): Promise<Order> {
  return request("/api/orders", {
    method: "POST",
    body: JSON.stringify({
      store_id: storeId,
      session_id: session.id,
      items: Object.entries(cart).map(([product_id, quantity]) => ({ product_id, quantity })),
      customer_language: session.language,
      order_source: "qr",
    }),
  });
}

export function getOrder(orderId: string): Promise<Order> {
  return request(`/api/orders/${orderId}`);
}

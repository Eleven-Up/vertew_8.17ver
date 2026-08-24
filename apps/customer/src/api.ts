import type { Draft, Language, Order, Session } from "./types";

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

// The order-so-far, as Vertew recognized it from the voice conversation (or as
// edited here). Not yet paid/vendor-visible -- see checkoutDraft.
export function getDraft(sessionId: string): Promise<Draft> {
  return request(`/api/sessions/${sessionId}/draft`);
}

// Absolute replace of the draft's quantities and notes (omit an item to
// remove it) -- used by this page's +/-/remove buttons and special-request
// field, not by the voice conversation.
export function updateDraft(
  sessionId: string,
  items: { product_id: string; quantity: number; note?: string }[],
): Promise<Draft> {
  return request(`/api/sessions/${sessionId}/draft`, {
    method: "PATCH",
    body: JSON.stringify({ items }),
  });
}

// Mock payment: turns the current draft into a real, vendor-visible order.
export function checkoutDraft(sessionId: string): Promise<Order> {
  return request(`/api/sessions/${sessionId}/draft/checkout`, { method: "POST" });
}

export function getOrder(orderId: string): Promise<Order> {
  return request(`/api/orders/${orderId}`);
}

import type { Order, OrderStatus } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getOrders(storeId: string): Promise<Order[]> {
  const data = await request<{ orders: Order[] }>(`/api/stores/${storeId}/orders`);
  return data.orders;
}

export function changeOrderStatus(orderId: string, status: OrderStatus): Promise<Order> {
  return request(`/api/orders/${orderId}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
}

import type { Order, OrderStatus, Product, ProductWrite } from "./types";

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

export async function getMenu(storeId: string): Promise<Product[]> {
  const data = await request<{ products: Product[] }>(`/api/stores/${storeId}/menu`);
  return data.products;
}

export function createProduct(storeId: string, body: ProductWrite): Promise<Product> {
  return request(`/api/stores/${storeId}/products`, { method: "POST", body: JSON.stringify(body) });
}

export function updateProduct(storeId: string, productId: string, body: ProductWrite): Promise<Product> {
  return request(`/api/stores/${storeId}/products/${productId}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteProduct(storeId: string, productId: string): Promise<void> {
  const response = await fetch(`/api/stores/${storeId}/products/${productId}`, { method: "DELETE" });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
}

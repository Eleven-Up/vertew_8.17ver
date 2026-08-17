export type OrderStatus = "PENDING" | "ACCEPTED" | "PREPARING" | "READY" | "COMPLETED" | "REJECTED";

export interface OrderItem {
  product_id: string;
  quantity: number;
  product_name: Record<string, string>;
  unit_price_minor: number;
}

export interface Order {
  id: string;
  store_id: string;
  session_id: string;
  order_number: number;
  status: OrderStatus;
  customer_language: string;
  total_minor: number;
  currency: string;
  items: OrderItem[];
  created_at: string;
  updated_at: string;
}

export interface StoreEvent {
  type: string;
  store_id: string;
  session_id: string | null;
  timestamp: string;
  payload: unknown;
}

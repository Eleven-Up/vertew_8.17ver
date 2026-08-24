export type OrderStatus = "PENDING" | "ACCEPTED" | "PREPARING" | "READY" | "COMPLETED" | "REJECTED";

export interface OrderItem {
  product_id: string;
  quantity: number;
  product_name: Record<string, string>;
  unit_price_minor: number;
  // Free-text special request for this item, e.g. "no cilantro please".
  note: string;
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

// Payload of a "call_vendor" event: the assistant escalated a customer question
// it could not answer (or a safety-sensitive one) to the human vendor.
export interface CallVendorPayload {
  question: string;
  language: string;
}

// A pending "please come help" call shown on the dashboard until dismissed.
export interface VendorCall {
  id: string;
  question: string;
  language: string;
  at: string;
  sessionId: string | null;
}

// A menu item, as managed on the Menu tab (name/description/price/image/stock).
// Mirrors backend/models.py Product.
export interface Product {
  id: string;
  store_id: string;
  name: Record<string, string>;
  description: Record<string, string>;
  price_minor: number;
  currency: string;
  available: boolean;
  image: string;
  spice_level: number;
  ingredients: Record<string, string>;
  allergens: string[];
  stock_count: number;
  origin: Record<string, string>;
}

// The vendor menu-editor form submission (create or full-replace one listing).
export interface ProductWrite {
  name_en: string;
  name_ko: string;
  name_ms: string;
  description_en: string;
  description_ko: string;
  description_ms: string;
  price_minor: number;
  image: string;
  stock_count: number;
  origin_en: string;
  origin_ko: string;
  origin_ms: string;
  available: boolean;
}

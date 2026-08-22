export type Language = "en" | "ko" | "ms";

export interface Product {
  id: string;
  store_id: string;
  name: Record<Language, string>;
  description: Record<Language, string>;
  price_minor: number;
  currency: string;
  available: boolean;
  image: string;
  // Structured menu knowledge used for product Q&A (and available to the UI):
  // spice_level is a 0..3 heat scale (0 = not spicy), ingredients mirrors
  // description per language, allergens is a list of canonical english tags.
  spice_level?: number;
  ingredients?: Partial<Record<Language, string>>;
  allergens?: string[];
  stock_count?: number;
}

export interface Session {
  id: string;
  store_id: string;
  language: Language;
  language_source: "default" | "auto_detected" | "user_selected";
  order_id: string | null;
}

export interface Order {
  id: string;
  order_number: number;
  status: string;
  total_minor: number;
  currency: string;
}

// The customer's in-progress order as recognized so far from the voice
// conversation with Vertew (or edited on this page) -- not yet paid, so not a
// real vendor-visible Order until POST .../draft/checkout.
export interface DraftItem {
  product_id: string;
  quantity: number;
  // Free-text special request for this item, e.g. "no cilantro please".
  note: string;
  name: Record<Language, string>;
  unit_price_minor: number;
}

export interface Draft {
  session_id: string;
  items: DraftItem[];
  total_minor: number;
  currency: string;
}

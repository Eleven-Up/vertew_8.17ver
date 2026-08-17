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

export type Cart = Record<string, number>;

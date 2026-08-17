import type { Language } from "./types";

const messages = {
  en: {
    fresh: "Fresh Fruits", add: "Add to Cart", orderNow: "Order Now", cart: "Your Cart",
    empty: "Your cart is empty.", total: "Total", place: "Place Order", confirm: "Confirm Order",
    back: "Back", complete: "Order Complete!", number: "Your Order Number",
    wait: "Please wait near the store.", notify: "Vertew will let you know when your order is ready.",
    loading: "Preparing your menu…", retry: "Try Again", language: "Language", each: "each",
    readyTitle: "Your order is ready!", readyBody: "Please collect your order at the store.", acknowledge: "OK",
  },
  ko: {
    fresh: "신선한 과일", add: "장바구니 담기", orderNow: "바로 주문", cart: "장바구니",
    empty: "장바구니가 비어 있습니다.", total: "합계", place: "주문하기", confirm: "주문 확인",
    back: "뒤로", complete: "주문이 완료되었습니다!", number: "주문 번호",
    wait: "매장 근처에서 기다려 주세요.", notify: "준비가 완료되면 Vertew가 알려드릴게요.",
    loading: "메뉴를 준비하고 있어요…", retry: "다시 시도", language: "언어", each: "개당",
    readyTitle: "주문이 준비되었습니다!", readyBody: "매장에서 주문하신 상품을 받아주세요.", acknowledge: "확인",
  },
  ms: {
    fresh: "Buah-buahan Segar", add: "Tambah ke Troli", orderNow: "Pesan Sekarang", cart: "Troli Anda",
    empty: "Troli anda kosong.", total: "Jumlah", place: "Buat Pesanan", confirm: "Sahkan Pesanan",
    back: "Kembali", complete: "Pesanan Selesai!", number: "Nombor Pesanan Anda",
    wait: "Sila tunggu berhampiran kedai.", notify: "Vertew akan memberitahu apabila pesanan anda siap.",
    loading: "Menyediakan menu anda…", retry: "Cuba Lagi", language: "Bahasa", each: "setiap satu",
    readyTitle: "Pesanan anda sudah siap!", readyBody: "Sila ambil pesanan anda di kedai.", acknowledge: "OK",
  },
} as const;

export function copy(language: Language) {
  return messages[language];
}

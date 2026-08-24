import type { Language } from "./types";

const messages = {
  en: {
    fresh: "Fresh Fruits", yourOrder: "Your Order",
    noDraftYet: "You haven't ordered anything yet — just tell Vertew what you'd like!",
    total: "Total", complete: "Order Complete!", number: "Your Order Number",
    wait: "Please wait near the store.", notify: "Vertew will let you know when your order is ready.",
    loading: "Loading your order…", retry: "Try Again", language: "Language", each: "each",
    readyTitle: "Your order is ready!", readyBody: "Please collect your order at the store.", acknowledge: "OK",
    pay: "Pay Now", paying: "Processing payment…", paid: "Payment received",
    notePlaceholder: "Special request? e.g. no cilantro",
  },
  ko: {
    fresh: "신선한 과일", yourOrder: "주문 내역",
    noDraftYet: "아직 주문하신 내역이 없어요 — Vertew에게 원하시는 걸 말씀해 주세요!",
    total: "합계", complete: "주문이 완료되었습니다!", number: "주문 번호",
    wait: "매장 근처에서 기다려 주세요.", notify: "준비가 완료되면 Vertew가 알려드릴게요.",
    loading: "주문 내역을 불러오고 있어요…", retry: "다시 시도", language: "언어", each: "개당",
    readyTitle: "주문이 준비되었습니다!", readyBody: "매장에서 주문하신 상품을 받아주세요.", acknowledge: "확인",
    pay: "결제하기", paying: "결제 처리 중…", paid: "결제가 완료되었습니다",
    notePlaceholder: "요청 사항이 있나요? 예: 고수 빼주세요",
  },
  ms: {
    fresh: "Buah-buahan Segar", yourOrder: "Pesanan Anda",
    noDraftYet: "Anda belum memesan apa-apa — beritahu Vertew apa yang anda mahu!",
    total: "Jumlah", complete: "Pesanan Selesai!", number: "Nombor Pesanan Anda",
    wait: "Sila tunggu berhampiran kedai.", notify: "Vertew akan memberitahu apabila pesanan anda siap.",
    loading: "Memuatkan pesanan anda…", retry: "Cuba Lagi", language: "Bahasa", each: "setiap satu",
    readyTitle: "Pesanan anda sudah siap!", readyBody: "Sila ambil pesanan anda di kedai.", acknowledge: "OK",
    pay: "Bayar Sekarang", paying: "Memproses pembayaran…", paid: "Pembayaran berjaya",
    notePlaceholder: "Ada permintaan khusus? cth. tanpa ketumbar",
  },
} as const;

export function copy(language: Language) {
  return messages[language];
}

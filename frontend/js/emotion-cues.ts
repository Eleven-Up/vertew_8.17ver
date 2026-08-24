// Emotion_Cues: splits one server reply into per-sentence "beats" so the
// character's emotion/gesture can change as different parts of the answer are
// delivered, instead of holding one emotion for the whole reply.
//
// This is a client-side heuristic (punctuation + a few keyword cues across
// en/ko/ms), not an LLM-side feature -- it costs no extra tokens/latency and
// works identically for the local rule-based fallback's canned replies. Any
// sentence that doesn't match a cue keeps the server's own emotion/gesture for
// that turn, so this only ever adds beats, never contradicts the server for
// unmarked sentences.

import type { Emotion, Gesture } from "./types.js";

export interface Beat {
  text: string;
  emotion: Emotion;
  gesture: Gesture;
}

// Split after sentence-ending punctuation (ASCII and full-width CJK), keeping
// the punctuation on the preceding sentence so cueFor can see it.
const SENTENCE_SPLIT = /(?<=[.!?。！？])\s+/;

const EXCITED_WORDS = ["free", "special", "discount", "sale", "무료", "할인", "특가", "percuma", "diskaun"];
const GREETING_WORDS = ["hi", "hello", "welcome", "안녕", "환영", "helo", "selamat datang"];
const PRICE_WORDS = ["rm ", "price", "가격", "harga"];

/**
 * Split `text` into beats, one per sentence, each carrying the emotion/
 * gesture that best fits it. `fallback` (the server's own emotion/gesture for
 * this whole reply) is used for any sentence that matches no specific cue, so
 * an unmarked sentence never looks less intentional than the server's choice.
 */
export function splitIntoBeats(text: string, fallback: { emotion: Emotion; gesture: Gesture }): Beat[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  const sentences = trimmed.split(SENTENCE_SPLIT).map((s) => s.trim()).filter(Boolean);
  const effective = sentences.length > 0 ? sentences : [trimmed];
  return effective.map((sentence) => ({ text: sentence, ...cueFor(sentence, fallback) }));
}

function cueFor(
  sentence: string,
  fallback: { emotion: Emotion; gesture: Gesture },
): { emotion: Emotion; gesture: Gesture } {
  const lower = sentence.toLowerCase();

  // A question -- the curious head-tilt "?" beat.
  if (sentence.endsWith("?") || sentence.endsWith("？")) {
    return { emotion: "neutral", gesture: "think" };
  }
  // An exclamation, or an exciting offer -- the "!" wings-out bounce beat.
  if (sentence.endsWith("!") || sentence.endsWith("！") || EXCITED_WORDS.some((w) => lower.includes(w))) {
    return { emotion: "surprised", gesture: "jump" };
  }
  // A greeting -- wave hello.
  if (GREETING_WORDS.some((w) => lower.includes(w))) {
    return { emotion: "happy", gesture: "wave" };
  }
  // A price/menu mention -- point at the (implied) menu/QR.
  if (PRICE_WORDS.some((w) => lower.includes(w))) {
    return { emotion: "happy", gesture: "point" };
  }
  return fallback;
}

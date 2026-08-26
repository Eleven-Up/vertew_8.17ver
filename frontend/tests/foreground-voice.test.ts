import { describe, expect, it } from "vitest";

import {
  END_OF_SPEECH_SILENCE_MS,
  FOREGROUND_HOLD_MS,
  FOREGROUND_MAX_RMS,
  FOREGROUND_MIN_RMS,
  ForegroundVoiceGate,
  MARKET_AUDIO_CONSTRAINTS,
  MAX_CAPTURE_MS,
  NO_SPEECH_TIMEOUT_MS,
  foregroundVoiceThreshold,
} from "../js/speech";

function calibratedGate(noise = 0.006): ForegroundVoiceGate {
  const gate = new ForegroundVoiceGate(0);
  for (const now of [0, 100, 200, 300, 400, 499]) gate.observe(noise, now);
  return gate;
}

describe("market foreground voice gate", () => {
  it("uses short capture windows for quick market orders", () => {
    expect(END_OF_SPEECH_SILENCE_MS).toBe(900);
    expect(NO_SPEECH_TIMEOUT_MS).toBe(4000);
    expect(MAX_CAPTURE_MS).toBe(10000);
  });

  it("uses a bounded adaptive threshold above the ambient floor", () => {
    expect(foregroundVoiceThreshold(0)).toBe(FOREGROUND_MIN_RMS);
    expect(foregroundVoiceThreshold(0.02)).toBeCloseTo(0.036);
    expect(foregroundVoiceThreshold(10)).toBe(FOREGROUND_MAX_RMS);
  });

  it("accepts a nearby voice only after it remains above the gate", () => {
    const gate = calibratedGate();

    expect(gate.observe(0.05, 500)).toBe(true);
    expect(gate.detected).toBe(false);
    expect(gate.observe(0.05, 500 + FOREGROUND_HOLD_MS - 1)).toBe(true);
    expect(gate.detected).toBe(false);
    expect(gate.observe(0.05, 500 + FOREGROUND_HOLD_MS)).toBe(true);
    expect(gate.detected).toBe(true);
  });

  it("rejects a short loud market-noise spike", () => {
    const gate = calibratedGate();

    gate.observe(0.12, 500);
    gate.observe(0.006, 560);
    gate.observe(0.12, 700);
    gate.observe(0.12, 700 + FOREGROUND_HOLD_MS - 1);
    expect(gate.detected).toBe(false);
  });

  it("does not stay sustained through a single loud market-noise blip", () => {
    // A customer has already been talking (gate.detected latched true), then goes
    // quiet, then one loud frame (a plate clatter) fires. sustainedNow must stay
    // false until that streak itself has held for FOREGROUND_HOLD_MS -- otherwise
    // a single frame would look identical to someone still actively talking and
    // block the end-of-speech silence timer from ever starting.
    const gate = calibratedGate();
    gate.observe(0.05, 500);
    gate.observe(0.05, 500 + FOREGROUND_HOLD_MS);
    expect(gate.detected).toBe(true);

    gate.observe(0.006, 700); // customer goes quiet
    expect(gate.sustainedNow(700)).toBe(false);

    gate.observe(0.12, 900); // single loud blip
    expect(gate.sustainedNow(900)).toBe(false);
    expect(gate.sustainedNow(900 + FOREGROUND_HOLD_MS - 1)).toBe(false);
  });

  it("does treat a genuinely sustained loud sound as still-active", () => {
    const gate = calibratedGate();
    gate.observe(0.05, 500);
    gate.observe(0.05, 500 + FOREGROUND_HOLD_MS);

    gate.observe(0.006, 700);
    gate.observe(0.12, 900);
    gate.observe(0.12, 900 + FOREGROUND_HOLD_MS);
    expect(gate.sustainedNow(900 + FOREGROUND_HOLD_MS)).toBe(true);
  });

  it("uses the quiet calibration percentile instead of learning intermittent clatter", () => {
    const gate = new ForegroundVoiceGate(0);
    for (const [level, now] of [[0.006, 0], [0.007, 100], [0.1, 200], [0.006, 300], [0.08, 499]] as const) {
      gate.observe(level, now);
    }
    gate.observe(0.03, 500);

    expect(gate.threshold).toBeCloseTo(0.0136);
  });

  it("requests browser noise suppression without amplifying distant voices", () => {
    expect(MARKET_AUDIO_CONSTRAINTS).toMatchObject({
      channelCount: { ideal: 1 },
      echoCancellation: { ideal: true },
      noiseSuppression: { ideal: true },
      autoGainControl: { ideal: false },
    });
  });
});

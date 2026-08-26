import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KioskController, READY_TO_SPEAK_MS, type KioskView } from "../js/kiosk";
import type { CharacterRenderer } from "../js/character";
import type { CharacterResponse, SttProvider, SttResult, TtsEngine, UIState } from "../js/types";

class FakeStt implements SttProvider {
  private resultCb: ((r: SttResult) => void) | null = null;
  private transcribingCb: (() => void) | null = null;
  started = false;

  start(): void {
    this.started = true;
  }
  stop(): void {
    this.started = false;
  }
  onResult(cb: (r: SttResult) => void): void {
    this.resultCb = cb;
  }
  onPartial(): void {}
  onTranscribing(cb: () => void): void {
    this.transcribingCb = cb;
  }
  setLanguage(): void {}

  emitTranscribing(): void {
    this.transcribingCb?.();
  }
  emitResult(r: SttResult): void {
    this.resultCb?.(r);
  }
}

class FakeTts implements TtsEngine {
  speak(): Promise<void> {
    return Promise.resolve();
  }
  isAvailable(): boolean {
    return true;
  }
}

class FakeRenderer implements CharacterRenderer {
  render(): void {}
  startLipSync(): void {}
  stopLipSync(): void {}
  playIdle(): void {}
  playListening(): void {}
}

class FakeView implements KioskView {
  showListeningIndicator(): void {}
  hideListeningIndicator(): void {}
  showErrorBanner(): void {}
  clearErrorBanner(): void {}
}

function makeController(): { controller: KioskController; stt: FakeStt; states: UIState[] } {
  const stt = new FakeStt();
  const states: UIState[] = [];
  const controller = new KioskController({
    stt,
    tts: new FakeTts(),
    renderer: new FakeRenderer(),
    view: new FakeView(),
    onStateChange: (state) => states.push(state),
  });
  states.length = 0; // drop the constructor's initial "idle" announcement
  return { controller, stt, states };
}

describe("KioskController turn-taking states", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("moves listening -> understanding -> thinking on transcribing then a transcript", () => {
    const { controller, stt, states } = makeController();
    controller.onTap();
    expect(controller.state).toBe("listening");

    stt.emitTranscribing();
    expect(controller.state).toBe("understanding");

    stt.emitResult({ kind: "transcript", text: "nasi goreng please" });
    expect(controller.state).toBe("thinking");

    expect(states).toEqual(["listening", "understanding", "thinking"]);
  });

  it("holds a perceptible ready beat before speaking once the reply arrives", () => {
    const { controller, stt } = makeController();
    controller.onTap();
    stt.emitResult({ kind: "transcript", text: "nasi goreng please" });
    expect(controller.state).toBe("thinking");

    const response: CharacterResponse = { text: "Sure!", emotion: "happy", gesture: "nod" };
    controller.onServerResponse(response);
    expect(controller.state).toBe("ready");

    vi.advanceTimersByTime(READY_TO_SPEAK_MS - 1);
    expect(controller.state).toBe("ready");

    vi.advanceTimersByTime(1);
    expect(controller.state).toBe("speaking");
  });

  it("still accepts a transcript that skips straight from listening (no transcribing event)", () => {
    // Browser speech recognition can resolve fast enough that a provider
    // without onTranscribing wiring never fires it -- the state machine must
    // not require the understanding step.
    const { controller, stt } = makeController();
    controller.onTap();
    stt.emitResult({ kind: "transcript", text: "nasi goreng please" });
    expect(controller.state).toBe("thinking");
  });

  it("an error during the ready beat cancels the pending speak", () => {
    const { controller, stt } = makeController();
    controller.onTap();
    stt.emitResult({ kind: "transcript", text: "nasi goreng please" });
    controller.onServerResponse({ text: "Sure!", emotion: "happy", gesture: "nod" });
    expect(controller.state).toBe("ready");

    controller.showError("network");
    expect(controller.state).toBe("error");

    vi.advanceTimersByTime(READY_TO_SPEAK_MS + 10);
    expect(controller.state).toBe("error"); // did not flip to speaking after all
  });
});

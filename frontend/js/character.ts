// Character_Renderer: 2D character expression / gesture / lip-sync rendering.
//
// Renders the single 2D merchant character (PNG sprite / Live2D for the MVP) by
// driving CSS classes on a DOM root element. Each named Emotion maps to exactly
// one facial expression and each named Gesture maps to exactly one body motion
// (Req 5.6). Unknown, missing, or empty values normalize to neutral/idle and
// never interrupt rendering (Req 4.6, 5.2). An ambient idle animation loops while
// no conversation is active (Req 5.3), and the mouth lip-syncs to voice output,
// returning to a closed resting position within 150 ms of stop (Req 5.4, 5.5).
//
// DOM contract: the renderer drives a root element (default id `character`,
// expected inside `<main id="stage">` in index.html) and a child mouth element
// (`.character-mouth`), which it creates on demand if absent. Rendering is pure
// synchronous DOM class manipulation so a recognized response begins displaying
// well within the 500 ms budget (Req 5.1).

import {
  DEFAULT_EMOTION,
  DEFAULT_GESTURE,
  EMOTIONS,
  GESTURES,
  type Emotion,
  type Gesture,
} from "./types.js";

/** DOM id of the character root element expected by {@link createCharacterRenderer}. */
export const CHARACTER_ROOT_ID = "character";

/** Class name applied to the root while the ambient idle loop is active (Req 5.3). */
export const IDLE_LOOP_CLASS = "character-idle-loop";

/** Class name applied to the mouth element while it is "open" during lip-sync. */
export const MOUTH_OPEN_CLASS = "mouth-open";

/**
 * Lip-sync frame period in milliseconds. Each mouth open/close movement begins
 * and ends within this interval, kept below the 150 ms responsiveness budget so
 * mouth movement tracks the corresponding voice segment (Req 5.4).
 */
export const LIPSYNC_FRAME_MS = 120;

/**
 * Maps each supported Emotion to exactly one facial-expression CSS class.
 * The total mapping over {@link EMOTIONS} guarantees one expression per value
 * (Req 5.6).
 */
export const EXPRESSION_CLASS: Record<Emotion, string> = {
  happy: "expr-happy",
  neutral: "expr-neutral",
  surprised: "expr-surprised",
  sad: "expr-sad",
  angry: "expr-angry",
};

/**
 * Maps each supported Gesture to exactly one body-motion CSS class.
 * The total mapping over {@link GESTURES} guarantees one motion per value
 * (Req 5.6).
 */
export const MOTION_CLASS: Record<Gesture, string> = {
  wave: "motion-wave",
  idle: "motion-idle",
  point: "motion-point",
  nod: "motion-nod",
  think: "motion-think",
};

const ALL_EXPRESSION_CLASSES: readonly string[] = Object.values(EXPRESSION_CLASS);
const ALL_MOTION_CLASSES: readonly string[] = Object.values(MOTION_CLASS);

/**
 * Return `emotion` when it is a supported Emotion, otherwise the neutral default.
 * Total over all inputs: `null`, `undefined`, empty/whitespace, or any
 * out-of-set string normalizes to {@link DEFAULT_EMOTION} (Req 4.6, 5.2, 5.6).
 */
export function normalizeEmotion(emotion: string | null | undefined): Emotion {
  if (emotion != null && (EMOTIONS as readonly string[]).includes(emotion)) {
    return emotion as Emotion;
  }
  return DEFAULT_EMOTION;
}

/**
 * Return `gesture` when it is a supported Gesture, otherwise the idle default.
 * Total over all inputs: `null`, `undefined`, empty/whitespace, or any
 * out-of-set string normalizes to {@link DEFAULT_GESTURE} (Req 4.6, 5.2, 5.6).
 */
export function normalizeGesture(gesture: string | null | undefined): Gesture {
  if (gesture != null && (GESTURES as readonly string[]).includes(gesture)) {
    return gesture as Gesture;
  }
  return DEFAULT_GESTURE;
}

/**
 * Renders the single 2D character: facial expression, body motion, ambient idle
 * loop, and mouth lip-sync. See the design Character_Renderer section.
 */
export interface CharacterRenderer {
  /** Display the expression+motion for the (normalized) emotion/gesture. */
  render(emotion: string, gesture: string): void;
  /** Begin animating the mouth in time with voice output (Req 5.4). */
  startLipSync(): void;
  /** Stop the mouth animation and close the mouth within 150 ms (Req 5.5). */
  stopLipSync(): void;
  /** Loop the ambient idle animation while no conversation is active (Req 5.3). */
  playIdle(): void;
}

/**
 * DOM-backed {@link CharacterRenderer}. Drives CSS classes on a root element and
 * a child mouth element. Construct with the character root element; the mouth
 * element is created on demand when missing.
 */
export class DomCharacterRenderer implements CharacterRenderer {
  private readonly root: HTMLElement;
  private readonly mouth: HTMLElement;
  private lipSyncTimer: ReturnType<typeof setInterval> | null = null;
  private idleActive = false;

  constructor(root: HTMLElement) {
    this.root = root;
    this.mouth = this.ensureMouth(root);
    // Start from a clean, closed-mouth resting pose.
    this.mouth.classList.remove(MOUTH_OPEN_CLASS);
  }

  /**
   * Display the facial expression and body motion for the given values,
   * normalizing unknown/empty input to neutral/idle (Req 5.2). A response marks
   * the conversation active, so the ambient idle loop is cleared. Synchronous so
   * display begins within the 500 ms budget (Req 5.1).
   */
  render(emotion: string, gesture: string): void {
    this.idleActive = false;
    this.root.classList.remove(IDLE_LOOP_CLASS);
    this.applyExpression(normalizeEmotion(emotion));
    this.applyMotion(normalizeGesture(gesture));
  }

  /**
   * Loop the ambient idle animation with the neutral expression and idle motion
   * while no conversation is active (Req 5.3).
   */
  playIdle(): void {
    this.idleActive = true;
    this.applyExpression(DEFAULT_EMOTION);
    this.applyMotion(DEFAULT_GESTURE);
    this.root.classList.add(IDLE_LOOP_CLASS);
  }

  /**
   * Start the mouth animation, toggling the open/closed state every
   * {@link LIPSYNC_FRAME_MS} so each movement tracks the voice segment (Req 5.4).
   * Idempotent: a second call while running is a no-op.
   */
  startLipSync(): void {
    if (this.lipSyncTimer !== null) {
      return;
    }
    this.mouth.classList.add(MOUTH_OPEN_CLASS);
    this.lipSyncTimer = setInterval(() => {
      this.mouth.classList.toggle(MOUTH_OPEN_CLASS);
    }, LIPSYNC_FRAME_MS);
  }

  /**
   * Stop the mouth animation and return the mouth to a closed resting position.
   * Both the timer clear and the class removal are synchronous, so the mouth is
   * closed immediately — well within the 150 ms budget (Req 5.5).
   */
  stopLipSync(): void {
    if (this.lipSyncTimer !== null) {
      clearInterval(this.lipSyncTimer);
      this.lipSyncTimer = null;
    }
    this.mouth.classList.remove(MOUTH_OPEN_CLASS);
  }

  /** True while the ambient idle loop is active (no conversation in progress). */
  get isIdleLooping(): boolean {
    return this.idleActive;
  }

  /** True while the mouth lip-sync animation is running. */
  get isLipSyncing(): boolean {
    return this.lipSyncTimer !== null;
  }

  private applyExpression(emotion: Emotion): void {
    this.root.classList.remove(...ALL_EXPRESSION_CLASSES);
    this.root.classList.add(EXPRESSION_CLASS[emotion]);
  }

  private applyMotion(gesture: Gesture): void {
    this.root.classList.remove(...ALL_MOTION_CLASSES);
    this.root.classList.add(MOTION_CLASS[gesture]);
  }

  private ensureMouth(root: HTMLElement): HTMLElement {
    const existing = root.querySelector<HTMLElement>(".character-mouth");
    if (existing) {
      return existing;
    }
    const mouth = root.ownerDocument.createElement("div");
    mouth.className = "character-mouth";
    root.appendChild(mouth);
    return mouth;
  }
}

/**
 * Create a {@link DomCharacterRenderer} bound to the given root element, or to
 * the element with id {@link CHARACTER_ROOT_ID} when no element is provided.
 * Throws if no suitable root element can be found.
 */
export function createCharacterRenderer(root?: HTMLElement): CharacterRenderer {
  const element = root ?? document.getElementById(CHARACTER_ROOT_ID);
  if (!element) {
    throw new Error(
      `Character_Renderer: no root element provided and no element with id "${CHARACTER_ROOT_ID}" found.`,
    );
  }
  return new DomCharacterRenderer(element);
}

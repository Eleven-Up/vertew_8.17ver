// Character_Renderer: 2D character expression / gesture / lip-sync rendering.
//
// Renders the single 2D merchant character (PNG sprite for the MVP) by driving
// CSS classes on a DOM root element AND swapping the underlying pose artwork.
// Each named Emotion maps to exactly one facial expression and each named
// Gesture maps to exactly one body motion (Req 5.6). Unknown, missing, or empty
// values normalize to neutral/idle and never interrupt rendering (Req 4.6, 5.2).
// An ambient idle animation loops while no conversation is active (Req 5.3), and
// the mouth lip-syncs to voice output, returning to a closed resting position
// within 150 ms of stop (Req 5.4, 5.5).
//
// DOM contract: the renderer drives a root element (default id `character`,
// expected inside `<main id="stage">` in index.html) and looks for two optional
// `<img>` children -- `.vertew-base` (the full-body pose art, also doubling as
// the lip-sync mouth by swapping to its beak-open variant) and
// `.vertew-emotion-accent` (a small floating emotion icon) -- animating them
// with anime.js when present; their absence never breaks rendering. Class
// toggling is otherwise pure synchronous DOM manipulation so a recognized
// response begins displaying well within the 500 ms budget (Req 5.1).

import { animate } from "animejs";
import {
  DEFAULT_EMOTION,
  DEFAULT_GESTURE,
  EMOTIONS,
  GESTURES,
  type Emotion,
  type Gesture,
} from "./types.js";

/** Base path for the character's pose/accent art (see assets/character/GENERATION_BRIEF.md). */
const ART_BASE = "./assets/character/processed";

/**
 * Maps each Gesture to its full-body pose image. Only `idle`/`wave`/`point`/
 * `nod`/`think` have distinct art; the map is total over {@link GESTURES}.
 */
const POSE_IMAGE_SRC: Record<Gesture, string> = {
  idle: `${ART_BASE}/pose_idle.png`,
  wave: `${ART_BASE}/pose_wave.png`,
  point: `${ART_BASE}/pose_point.png`,
  nod: `${ART_BASE}/pose_nod.png`,
  think: `${ART_BASE}/pose_think.png`,
};

/** Beak-open variant of the idle pose, swapped in for lip-sync frames. */
const POSE_IDLE_TALK_SRC = `${ART_BASE}/pose_idle_talk.png`;

/**
 * Maps each Emotion to a small floating accent icon, or `null` for `neutral`
 * (no accent shown). Total over {@link EMOTIONS}.
 */
const EMOTION_ACCENT_SRC: Record<Emotion, string | null> = {
  happy: `${ART_BASE}/fruit_halo.png`,
  neutral: null,
  surprised: `${ART_BASE}/accent_surprised.png`,
  sad: `${ART_BASE}/sweat.png`,
  angry: `${ART_BASE}/accent_angry.png`,
};

/**
 * How long a one-shot gesture pose (wave/point/nod/think) is held before the
 * base art settles back to idle. Chosen to cover the longest gesture's CSS
 * "acting" animation in hologram.css (point: 0.8s x 2 = 1.6s) so the pose
 * reads as a deliberate beat rather than lingering through the whole reply --
 * and, just as importantly, so the `.character-mouth`/`.vertew-eyelid` overlay
 * coordinates (tuned for the idle pose) are correct again for the rest of the
 * turn's lip-sync, since the other pose art shifts the head/beak slightly.
 */
const GESTURE_HOLD_MS = 1600;

/** DOM id of the character root element expected by {@link createCharacterRenderer}. */
export const CHARACTER_ROOT_ID = "character";

/** Class name applied to the root while the ambient idle loop is active (Req 5.3). */
export const IDLE_LOOP_CLASS = "character-idle-loop";

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
 * swaps the pose/accent `<img>` art via anime.js when present (`.vertew-base`,
 * `.vertew-emotion-accent`) -- both are optional so the renderer still works
 * against a DOM that only has the class-driven rig.
 */
export class DomCharacterRenderer implements CharacterRenderer {
  private readonly root: HTMLElement;
  private readonly base: HTMLImageElement | null;
  private readonly emotionAccent: HTMLImageElement | null;
  private lipSyncTimer: ReturnType<typeof setInterval> | null = null;
  private poseRevertTimer: ReturnType<typeof setTimeout> | null = null;
  private idleActive = false;
  private mouthOpen = false;
  private currentPoseSrc: string | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.base = root.querySelector<HTMLImageElement>(".vertew-base");
    this.emotionAccent = root.querySelector<HTMLImageElement>(".vertew-emotion-accent");
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
    const normEmotion = normalizeEmotion(emotion);
    const normGesture = normalizeGesture(gesture);
    this.applyExpression(normEmotion);
    this.applyMotion(normGesture);
    this.setEmotionAccent(normEmotion);
    this.setGesturePose(normGesture);
  }

  /**
   * Loop the ambient idle animation with the neutral expression and idle motion
   * while no conversation is active (Req 5.3).
   */
  playIdle(): void {
    this.idleActive = true;
    this.clearPoseRevert();
    this.applyExpression(DEFAULT_EMOTION);
    this.applyMotion(DEFAULT_GESTURE);
    this.setEmotionAccent(DEFAULT_EMOTION);
    this.setBaseImage(POSE_IMAGE_SRC[DEFAULT_GESTURE], true);
    this.root.classList.add(IDLE_LOOP_CLASS);
  }

  /**
   * Start the mouth animation. While the base art is resting on the idle pose,
   * each tick swaps the real beak-open/closed art ({@link POSE_IDLE_TALK_SRC} /
   * idle) so lip-sync reads as genuine artwork, not a CSS overlay. While a
   * one-shot gesture pose is being held (see {@link GESTURE_HOLD_MS}), ticks are
   * skipped so the gesture art is not clobbered mid-beat; lip-sync resumes as
   * soon as the pose settles back to idle. Idempotent while running (Req 5.4).
   */
  startLipSync(): void {
    if (this.lipSyncTimer !== null) {
      return;
    }
    this.lipSyncTimer = setInterval(() => this.tickLipSync(), LIPSYNC_FRAME_MS);
    this.tickLipSync();
  }

  /**
   * Stop the mouth animation and return to the closed resting pose. Both the
   * timer clear and the image swap are synchronous, so the mouth is closed
   * immediately — well within the 150 ms budget (Req 5.5).
   */
  stopLipSync(): void {
    if (this.lipSyncTimer !== null) {
      clearInterval(this.lipSyncTimer);
      this.lipSyncTimer = null;
    }
    if (this.mouthOpen) {
      this.mouthOpen = false;
      if (this.poseRevertTimer === null) {
        this.setBaseImage(POSE_IMAGE_SRC.idle, false);
      }
    }
  }

  /** True while the ambient idle loop is active (no conversation in progress). */
  get isIdleLooping(): boolean {
    return this.idleActive;
  }

  /** True while the mouth lip-sync animation is running. */
  get isLipSyncing(): boolean {
    return this.lipSyncTimer !== null;
  }

  private tickLipSync(): void {
    if (this.poseRevertTimer !== null) {
      return; // mid gesture-beat: hold still, resume once the pose settles
    }
    this.mouthOpen = !this.mouthOpen;
    this.setBaseImage(this.mouthOpen ? POSE_IDLE_TALK_SRC : POSE_IMAGE_SRC.idle, false);
  }

  /**
   * Show the gesture's pose art immediately (animated crossfade), then -- for
   * one-shot gestures other than idle -- settle back to the idle pose after
   * {@link GESTURE_HOLD_MS} so lip-sync alignment is restored for the rest of
   * the turn.
   */
  private setGesturePose(gesture: Gesture): void {
    this.clearPoseRevert();
    this.setBaseImage(POSE_IMAGE_SRC[gesture], true);
    if (gesture === "idle") {
      return;
    }
    this.poseRevertTimer = setTimeout(() => {
      this.poseRevertTimer = null;
      this.applyMotion("idle");
      this.setBaseImage(POSE_IMAGE_SRC.idle, true);
    }, GESTURE_HOLD_MS);
  }

  private clearPoseRevert(): void {
    if (this.poseRevertTimer !== null) {
      clearTimeout(this.poseRevertTimer);
      this.poseRevertTimer = null;
    }
  }

  /**
   * Set the base pose image. `animated` crossfades with a small pop (used for
   * gesture/idle changes); non-animated swaps are instant (used for the rapid
   * lip-sync ticks, where a fade would never resolve cleanly).
   */
  private setBaseImage(src: string, animated: boolean): void {
    if (!this.base || this.currentPoseSrc === src) {
      return;
    }
    this.currentPoseSrc = src;
    if (!animated) {
      this.base.src = src;
      return;
    }
    animate(this.base, {
      opacity: [1, 0],
      duration: 110,
      ease: "inQuad",
      onComplete: () => {
        if (this.base) this.base.src = src;
        animate(this.base as HTMLImageElement, {
          opacity: [0, 1],
          scale: [0.95, 1],
          duration: 220,
          ease: "outBack",
        });
      },
    });
  }

  private setEmotionAccent(emotion: Emotion): void {
    if (!this.emotionAccent) {
      return;
    }
    const src = EMOTION_ACCENT_SRC[emotion];
    if (!src) {
      animate(this.emotionAccent, { opacity: 0, scale: 0.7, duration: 160, ease: "inQuad" });
      return;
    }
    this.emotionAccent.src = src;
    animate(this.emotionAccent, {
      opacity: [0, 1],
      scale: [0.5, 1],
      translateY: [-10, 0],
      duration: 320,
      ease: "outBack",
    });
  }

  private applyExpression(emotion: Emotion): void {
    this.root.classList.remove(...ALL_EXPRESSION_CLASSES);
    this.root.classList.add(EXPRESSION_CLASS[emotion]);
  }

  private applyMotion(gesture: Gesture): void {
    this.root.classList.remove(...ALL_MOTION_CLASSES);
    this.root.classList.add(MOTION_CLASS[gesture]);
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

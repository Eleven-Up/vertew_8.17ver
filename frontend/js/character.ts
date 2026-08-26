// Character_Renderer: real 3D character rendering via three.js.
//
// Renders the single Vertew mascot as an actual 3D model (a free, CC-BY
// licensed toco toucan mesh -- see assets/character/3d/CREDITS.md) inside a
// WebGL `<canvas>`, rather than swapping 2D pose sprites. Each named
// Emotion/Gesture maps to a distinct procedural 3D motion (rotation/position/
// scale animated every frame), so "gesture" now means real rotation and
// movement in 3D space, not a different flat image. Unknown, missing, or
// empty emotion/gesture values normalize to neutral/idle and never interrupt
// rendering.
//
// The mesh has no skeleton or baked animation, so there is no per-part
// (wing/head/mouth) animation -- all motion here is a whole-model transform
// (position/rotation/scale on the loaded model's wrapping THREE.Group). It is
// however a properly volumetric model (unlike an earlier single-image-to-3D
// generation attempt, which reconstructed as a thin "pillow"), so a full
// continuous turn looks good from every angle -- see idleMotion.
// Lip-sync is therefore a rapid subtle "talking" wiggle rather than real mouth
// movement -- an honest substitute given a single static mesh, not an attempt
// to fake real mouth articulation.
//
// DOM contract: the renderer drives a root element (default id `character`,
// expected inside `<main id="stage">` in index.html) and expects a
// `<canvas class="vertew-canvas">` child to render into (created if absent) plus
// an optional `<img class="vertew-emotion-accent">` for the small floating
// emotion icon overlay -- kept as a 2D overlay on top of the 3D scene since a
// static mesh can't change facial expression on its own.

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import {
  DEFAULT_EMOTION,
  DEFAULT_GESTURE,
  EMOTIONS,
  GESTURES,
  type Emotion,
  type Gesture,
} from "./types.js";

/**
 * Punchier, more overshoot-y easing curves for the gesture motion below --
 * shaping progress (`p`/`t` in [0,1]) so a lean/tilt/spin snaps past its rest
 * point and springs back, instead of gliding straight in via a plain
 * `1 - Math.cos(...)` ease-out.
 *
 * These formulas are ported directly from anime.js's outBack/outElastic/
 * outBounce (rather than depending on the `animejs` package itself) so this
 * renderer has no dependency on an external ESM package's own JS-engine
 * requirements -- this runs on the kiosk's actual device, including older
 * ARM boards (e.g. Raspberry Pi 4) with an older bundled Chromium than a dev
 * machine, where a newer npm package can quietly fail to evaluate at all.
 * The math itself is plain arithmetic, so it needs nothing beyond what the
 * rest of this already-running bundle needs.
 */
function easeOutBack(overshoot: number): (t: number) => number {
  return (t) => {
    const u = 1 - t;
    return 1 - ((overshoot + 1) * u * u * u - overshoot * u * u);
  };
}

function easeOutElastic(amplitude: number, period: number): (t: number) => number {
  const a = Math.min(10, Math.max(1, amplitude));
  const p = Math.min(2, Math.max(0.000001, period));
  const s = (p / (2 * Math.PI)) * Math.asin(1 / a);
  const e = (2 * Math.PI) / p;
  return (t) => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    return 1 + a * Math.pow(2, -10 * t) * Math.sin((t - s) * e);
  };
}

function easeInBounce(t: number): number {
  let b = 4;
  let pow2 = 0;
  do {
    b -= 1;
    pow2 = Math.pow(2, b);
  } while (t < (pow2 - 1) / 11);
  return 1 / Math.pow(4, 3 - b) - 7.5625 * Math.pow((pow2 * 3 - 2) / 22 - t, 2);
}

function easeOutBounce(t: number): number {
  return 1 - easeInBounce(1 - t);
}

const EASE_OUT_BACK = easeOutBack(2.4);
const EASE_OUT_ELASTIC = easeOutElastic(1.15, 0.45);
const EASE_OUT_BOUNCE = easeOutBounce;

/** Base path for the character's 2D accent art (emotion icon overlay only). */
const ART_BASE = "./assets/character/processed";

/** The 3D model (see assets/character/3d/CREDITS.md for source/license). */
const MODEL_SRC = "./assets/character/3d/vertew.glb";

/**
 * Maps each Emotion to a small floating accent icon, or `null` for `neutral`
 * (no accent shown). Total over {@link EMOTIONS}. Rendered as a flat 2D overlay
 * above the 3D canvas -- the mesh itself has no expression to change.
 */
const EMOTION_ACCENT_SRC: Record<Emotion, string | null> = {
  happy: `${ART_BASE}/fruit_halo.png`,
  neutral: null,
  surprised: `${ART_BASE}/accent_surprised.png`,
  sad: `${ART_BASE}/sweat.png`,
  angry: `${ART_BASE}/accent_angry.png`,
};

/**
 * How long a one-shot gesture's 3D motion (wave/point/nod/think/fly/jump/
 * approach) plays before the model settles back into the ambient idle turn.
 * Long enough to read as a deliberate beat, short enough not to linger through
 * the rest of the reply.
 */
export const GESTURE_HOLD_MS = 2100;

/** DOM id of the character root element expected by {@link createCharacterRenderer}. */
export const CHARACTER_ROOT_ID = "character";

/**
 * Lip-sync wiggle period in milliseconds -- the rhythm of the subtle talking
 * pulse applied while {@link CharacterRenderer.startLipSync} is active.
 */
export const LIPSYNC_FRAME_MS = 120;

/**
 * Return `emotion` when it is a supported Emotion, otherwise the neutral default.
 * Total over all inputs: `null`, `undefined`, empty/whitespace, or any
 * out-of-set string normalizes to {@link DEFAULT_EMOTION}.
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
 * out-of-set string normalizes to {@link DEFAULT_GESTURE}.
 */
export function normalizeGesture(gesture: string | null | undefined): Gesture {
  if (gesture != null && (GESTURES as readonly string[]).includes(gesture)) {
    return gesture as Gesture;
  }
  return DEFAULT_GESTURE;
}

/**
 * Renders the single 3D character: gesture/emotion motion, an ambient idle
 * turn, an attentive listening lean, and a talking wiggle in place of lip-sync.
 */
export interface CharacterRenderer {
  /** Play the 3D motion for the (normalized) emotion/gesture. */
  render(emotion: string, gesture: string): void;
  /** Begin the subtle talking wiggle in time with voice output. */
  startLipSync(): void;
  /** Stop the talking wiggle. */
  stopLipSync(): void;
  /** Resume the ambient idle turn while no conversation is active. */
  playIdle(): void;
  /** Show the attentive "listening" lean while the mic is actively capturing. */
  playListening(): void;
}

/** Camera-space units the model is normalized to fill (its largest dimension).
 * Left with headroom below the frame edge for gestures that move it up
 * (jump/fly) -- fitting it edge-to-edge at rest left no margin for those. */
const MODEL_FIT_SIZE = 1.5;

/** Fixed yaw (radians) applied to every state so the resting pose is a
 * flattering 3/4 profile (beak + eye + chest all visible) rather than the
 * model's raw near-front-on orientation. Found by eyeballing a few angles. */
const CHARACTER_BASE_YAW = 0.8;

/**
 * Idle doesn't hold a single nonstop spin (that read as "spinning product
 * shot", not a character) -- but it should still clearly show off real 3D
 * movement, so on top of a moderate ambient sway + "breathing" bob it: (a)
 * continuously wanders side to side / forward-back in a slow organic path
 * (see the position math in idleMotion), and (b) every
 * {@link IDLE_FLOURISH_PERIOD_S} seconds plays a short, varied flourish (see
 * {@link IDLE_FLOURISHES}) -- including a full look-around spin -- so it
 * reads as an active, roaming character rather than a static display piece.
 */
const IDLE_SWAY_AMPLITUDE = 0.5; // radians (~29 degrees each way)
const IDLE_SWAY_SPEED = (2 * Math.PI) / 4.5;

/** Radius/speed of the continuous idle wandering path (position, not rotation). */
const IDLE_WANDER_X = 0.4;
const IDLE_WANDER_Z = 0.2;
const IDLE_WANDER_SPEED_X = (2 * Math.PI) / 8;
const IDLE_WANDER_SPEED_Z = (2 * Math.PI) / 5.6; // different period than X -> an organic, non-repeating path

/** Vertical bob amplitude/speed for the idle "breathing" motion. */
const IDLE_BOB_AMPLITUDE = 0.09;
const IDLE_BOB_SPEED = 1.4;

/** How often an idle flourish plays, and how long each one lasts. */
const IDLE_FLOURISH_PERIOD_S = 4;
const IDLE_FLOURISH_DURATION_S = 1.5;

/**
 * A pool of short, distinctive idle flourishes cycled through over time (by
 * elapsed-time index, not randomness, so behavior is deterministic and
 * testable) -- this is what gives idle variety/personality instead of a
 * single repeating loop. `p` is progress through the flourish in [0,1].
 * Each returns an additive {rotation, position, scale} delta on top of the
 * base sway/bob/wander.
 */
const IDLE_FLOURISHES: ((p: number) => { rx: number; ry: number; rz: number; y: number; scale: number })[] = [
  // Curious head-tilt -- ramps in and back out through anime.js's outElastic
  // curve (mirrored around the midpoint) instead of a plain sine glide, so it
  // springs/wobbles into the tilt rather than gliding smoothly.
  (p) => ({ rx: 0, ry: 0, rz: EASE_OUT_ELASTIC(p < 0.5 ? p * 2 : (1 - p) * 2) * 0.34, y: 0, scale: 1 }),
  // A little perked-up hop.
  (p) => ({ rx: -0.1 * Math.sin(p * Math.PI), ry: 0, rz: 0, y: 0.18 * Math.sin(p * Math.PI), scale: 1 + 0.07 * Math.sin(p * Math.PI) }),
  // A full look-around spin (now 1.5 turns for a wider rotation radius) --
  // the clearest "this is real 3D" beat, and now safe to use freely since
  // the current model reads well from every angle.
  (p) => ({ rx: 0, ry: p * Math.PI * 3, rz: 0, y: 0.08 * Math.sin(p * Math.PI), scale: 1 }),
  // A quick alert perk (ears-up read on a bird with no ears: a brief upward stretch).
  (p) => ({ rx: 0.08 * Math.sin(p * Math.PI), ry: 0, rz: 0, y: 0.08 * Math.sin(p * Math.PI), scale: 1 + 0.08 * Math.sin(p * Math.PI) }),
];

type MotionState = { emotion: Emotion; gesture: Gesture; changedAt: number };

/**
 * three.js-backed {@link CharacterRenderer}. Owns a WebGL canvas, loads the
 * generated GLB once, and re-poses the model's wrapping group every animation
 * frame from the current emotion/gesture/listening/lip-sync state -- there is
 * no per-gesture timer bookkeeping like a sprite-swap renderer would need; the
 * render loop simply re-evaluates "what should this look like right now".
 */
export class ThreeCharacterRenderer implements CharacterRenderer {
  private readonly root: HTMLElement;
  private readonly emotionAccent: HTMLImageElement | null;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene: THREE.Scene;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly clock: THREE.Clock;
  private modelGroup: THREE.Group | null = null;

  private state: MotionState = { emotion: DEFAULT_EMOTION, gesture: DEFAULT_GESTURE, changedAt: 0 };
  private listening = false;
  private lipSyncing = false;

  constructor(root: HTMLElement) {
    this.root = root;
    this.emotionAccent = root.querySelector<HTMLImageElement>(".vertew-emotion-accent");

    const canvas = this.resolveCanvas(root);
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
    this.camera.position.set(0, 0.1, 4.2);

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.4));
    const key = new THREE.DirectionalLight(0xfff3e6, 1.1);
    key.position.set(2, 3, 4);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x67efff, 0.6);
    rim.position.set(-3, 1, -2);
    this.scene.add(rim);

    this.clock = new THREE.Clock();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(root);

    void this.loadModel();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  /** Find the canvas the caller's markup provides, or create one. */
  private resolveCanvas(root: HTMLElement): HTMLCanvasElement {
    const existing = root.querySelector<HTMLCanvasElement>(".vertew-canvas");
    if (existing) return existing;
    const created = document.createElement("canvas");
    created.className = "vertew-canvas";
    root.prepend(created);
    return created;
  }

  private async loadModel(): Promise<void> {
    const loader = new GLTFLoader();
    let gltf: Awaited<ReturnType<GLTFLoader["loadAsync"]>>;
    try {
      gltf = await loader.loadAsync(MODEL_SRC);
    } catch {
      // No model available (e.g. offline dev checkout without the generated
      // asset) -- render an empty stage rather than breaking the Kiosk_UI.
      return;
    }

    const model = gltf.scene;
    model.traverse((node) => {
      if (node instanceof THREE.Mesh && node.material instanceof THREE.MeshStandardMaterial) {
        // The generated PBR material can read near-black under simple kiosk
        // lighting at high metalness; this is a decorative mascot, not a
        // physically accurate render, so cap it for a reliably lit look.
        node.material.metalness = Math.min(node.material.metalness, 0.2);
      }
    });

    // Center at the origin and normalize scale so it fits the frame the same
    // way regardless of the exported mesh's raw size/pivot.
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const scale = MODEL_FIT_SIZE / Math.max(size.x, size.y, size.z, 0.001);
    model.position.sub(center);
    model.scale.setScalar(scale);

    const group = new THREE.Group();
    group.add(model);
    this.scene.add(group);
    this.modelGroup = group;
  }

  private resize(): void {
    const rect = this.root.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  render(emotion: string, gesture: string): void {
    this.listening = false;
    this.state = {
      emotion: normalizeEmotion(emotion),
      gesture: normalizeGesture(gesture),
      changedAt: performance.now(),
    };
    this.setEmotionAccent(this.state.emotion);
  }

  playIdle(): void {
    this.listening = false;
    this.state = { emotion: DEFAULT_EMOTION, gesture: DEFAULT_GESTURE, changedAt: performance.now() };
    this.setEmotionAccent(DEFAULT_EMOTION);
  }

  playListening(): void {
    this.listening = true;
    this.state = { ...this.state, gesture: DEFAULT_GESTURE };
  }

  startLipSync(): void {
    this.lipSyncing = true;
  }

  stopLipSync(): void {
    this.lipSyncing = false;
  }

  /** True while the ambient idle turn is the active motion (no gesture/listening). */
  get isIdleLooping(): boolean {
    return !this.listening && this.state.gesture === "idle";
  }

  /** True while the talking wiggle is running. */
  get isLipSyncing(): boolean {
    return this.lipSyncing;
  }

  private tick(): void {
    const totalElapsed = this.clock.getElapsedTime();
    const group = this.modelGroup;
    if (!group) {
      this.renderer.render(this.scene, this.camera);
      return;
    }

    // A gesture's motion holds only for GESTURE_HOLD_MS, then the state falls
    // back to idle -- evaluated here each frame rather than via a timer.
    const sinceChangeMs = performance.now() - this.state.changedAt;
    const activeGesture: Gesture =
      this.state.gesture !== "idle" && sinceChangeMs < GESTURE_HOLD_MS ? this.state.gesture : "idle";

    let position = new THREE.Vector3(0, 0, 0);
    let rotation = new THREE.Euler(0, 0, 0);
    let scale = new THREE.Vector3(1, 1, 1);

    if (this.listening) {
      rotation.x = -0.12;
      rotation.y = Math.sin(totalElapsed * 0.8) * 0.15;
      position.y = IDLE_BOB_AMPLITUDE * 0.6 * Math.sin(totalElapsed * IDLE_BOB_SPEED);
    } else if (activeGesture === "idle") {
      [position, rotation, scale] = this.idleMotion(totalElapsed, this.state.emotion);
    } else {
      const t = sinceChangeMs / 1000;
      const p = Math.min(1, sinceChangeMs / GESTURE_HOLD_MS);
      [position, rotation, scale] = this.gestureMotion(activeGesture, this.state.emotion, t, p, totalElapsed);
    }

    // The model's raw (unrotated) pose faces almost straight at the camera,
    // which reads as an odd "staring up" angle rather than a classic 3/4
    // toucan profile. Every state above computes its rotation.y relative to
    // 0, so this fixed offset is applied uniformly here to land the resting
    // pose (and every gesture) on the flattering angle instead.
    rotation.y += CHARACTER_BASE_YAW;

    if (this.lipSyncing) {
      // Rapid, subtle talking pulse -- an honest stand-in for lip-sync on a
      // mesh with no separate mouth/jaw to animate.
      const wigglePeriod = totalElapsed * ((2 * Math.PI * 1000) / LIPSYNC_FRAME_MS / 2);
      scale.multiplyScalar(1 + 0.035 * Math.sin(wigglePeriod));
      rotation.x += 0.015 * Math.sin(totalElapsed * 50);
    }

    group.position.copy(position);
    group.rotation.copy(rotation);
    group.scale.copy(scale);

    this.renderer.render(this.scene, this.camera);
  }

  /** Ambient idle motion: a small bounded sway plus a "breathing" bob (no
   * continuous spin -- see {@link IDLE_SWAY_AMPLITUDE}), periodically layered
   * with a short varied flourish, all modulated by the current emotion so
   * each mood has a distinct, recognizable idle personality. */
  private idleMotion(t: number, emotion: Emotion): [THREE.Vector3, THREE.Euler, THREE.Vector3] {
    const mood = {
      happy: { bob: 1.5, bobSpeed: 1.2, sway: 1, tiltX: 0.02, flourish: 1.4 },
      neutral: { bob: 1, bobSpeed: 1, sway: 1, tiltX: 0, flourish: 1 },
      surprised: { bob: 1, bobSpeed: 1, sway: 0.6, tiltX: 0, flourish: 1 },
      sad: { bob: 0.4, bobSpeed: 0.55, sway: 0.4, tiltX: -0.16, flourish: 0.3 },
      angry: { bob: 0.7, bobSpeed: 1.6, sway: 0.5, tiltX: -0.02, flourish: 0.8 },
    }[emotion];

    // Continuous wandering (not just rotation-in-place): two different-period
    // sine waves on x/z trace a slow, organic, non-repeating path around the
    // resting spot, so the character visibly moves around rather than just
    // standing still swaying -- this is the "roams around" motion.
    const position = new THREE.Vector3(
      IDLE_WANDER_X * mood.sway * Math.sin(t * IDLE_WANDER_SPEED_X)
        + (emotion === "angry" ? 0.02 * Math.sin(t * 45) : 0),
      IDLE_BOB_AMPLITUDE * mood.bob * Math.sin(t * IDLE_BOB_SPEED * mood.bobSpeed),
      IDLE_WANDER_Z * mood.sway * Math.sin(t * IDLE_WANDER_SPEED_Z),
    );
    const rotation = new THREE.Euler(
      mood.tiltX,
      IDLE_SWAY_AMPLITUDE * mood.sway * Math.sin(t * IDLE_SWAY_SPEED),
      0.03 * Math.sin(t * 0.7),
    );
    let scalePop = 1;
    // A quick scale pop right after a "surprised" reaction starts, decaying out.
    if (emotion === "surprised") {
      scalePop = 1 + 0.08 * Math.exp(-t * 3) * Math.sin(t * 12);
    }

    // Layer in a short, varied flourish every IDLE_FLOURISH_PERIOD_S seconds --
    // this is what keeps idle feeling alive/characterful without ever holding
    // a repeating loop or spinning around.
    const cycleT = t % IDLE_FLOURISH_PERIOD_S;
    if (cycleT < IDLE_FLOURISH_DURATION_S) {
      const progress = cycleT / IDLE_FLOURISH_DURATION_S;
      const index = Math.floor(t / IDLE_FLOURISH_PERIOD_S) % IDLE_FLOURISHES.length;
      const f = IDLE_FLOURISHES[index](progress);
      rotation.x += f.rx * mood.flourish;
      rotation.y += f.ry * mood.flourish;
      rotation.z += f.rz * mood.flourish;
      position.y += f.y * mood.flourish;
      scalePop *= 1 + (f.scale - 1) * mood.flourish;
    }

    return [position, rotation, new THREE.Vector3(scalePop, scalePop, scalePop)];
  }

  /** One-shot gesture motion. `t` is seconds since the gesture started, `p` is
   * that progress clamped to [0,1] over {@link GESTURE_HOLD_MS}. `emotion` lets
   * a gesture read differently depending on mood -- e.g. "surprised" turns
   * jump's bounce into a wings-out puff, since the mesh has no separate wings
   * to actually spread. */
  private gestureMotion(
    gesture: Gesture,
    emotion: Emotion,
    t: number,
    p: number,
    totalElapsed: number,
  ): [THREE.Vector3, THREE.Euler, THREE.Vector3] {
    const position = new THREE.Vector3();
    const rotation = new THREE.Euler();
    let scale = new THREE.Vector3(1, 1, 1);

    switch (gesture) {
      case "wave": {
        // An energetic little dance: a full-and-a-half spin plus a wide
        // side-to-side shimmy and bouncing hops, decaying out at the end via
        // anime.js's outElastic so the settle wobbles rather than just
        // linearly decays. This is the "look, real 3D!" showcase move -- a
        // small rocking wobble alone read as boring.
        const settle = EASE_OUT_ELASTIC(1 - p);
        rotation.y = p * Math.PI * 3;
        rotation.z = Math.sin(t * 11) * 0.22 * settle;
        position.x = Math.sin(t * 11) * 0.24 * settle;
        position.y = 0.15 * settle * Math.abs(Math.sin(t * 8.5));
        break;
      }
      case "point": {
        // A lean toward what's being pointed at, snapped in with anime.js's
        // outBack for a livelier little overshoot instead of a plain ease.
        const ease = EASE_OUT_BACK(p);
        rotation.y = 0.5 * ease;
        rotation.x = 0.16 * ease;
        position.z = 0.14 * ease;
        break;
      }
      case "nod": {
        const settle = 1 - p;
        rotation.x = Math.sin(t * 13) * 0.32 * settle;
        break;
      }
      case "think": {
        // A curious head-cock: springs into a held tilt (the "?" pose) via
        // anime.js's outElastic rather than a quick wobble, with a small
        // ongoing sway so it doesn't freeze.
        const ease = EASE_OUT_ELASTIC(Math.min(p, 0.4) / 0.4);
        rotation.z = -0.46 * ease + 0.05 * Math.sin(t * 1.9);
        rotation.x = 0.1 * ease;
        rotation.y = 0.16 * Math.sin(t * 1.4);
        position.y = IDLE_BOB_AMPLITUDE * Math.sin(totalElapsed * IDLE_BOB_SPEED * 0.6);
        break;
      }
      case "fly": {
        // A wide sweeping loop across the frame -- lift, swing out to one
        // side and back, bank, and turn a full 2+ rotations while airborne,
        // landing back near center. Real travel, not just a rise-and-settle
        // in place.
        const arc = Math.sin(p * Math.PI); // 0 -> 1 -> 0
        position.y = 0.4 * arc;
        position.x = 0.46 * Math.sin(p * Math.PI * 2);
        position.z = 0.24 * arc;
        rotation.x = -0.18 * arc;
        rotation.y = p * Math.PI * 2.2;
        rotation.z = Math.sin(t * 8) * 0.2;
        break;
      }
      case "jump": {
        // A small anticipation squat right before the hop, then a bigger arc
        // -- hopping further to one side rather than straight up in place,
        // and landing with an anime.js outBounce settle instead of a plain
        // cosine wobble.
        const squat = p < 0.12 ? -0.1 * Math.sin((p / 0.12) * Math.PI) : 0;
        position.x = 0.22 * Math.sin(p * Math.PI);
        position.y = 0.42 * (4 * p * (1 - p)) + squat * 0.3;
        const bounce = 1 - 0.14 * (1 - EASE_OUT_BOUNCE(Math.min(p * 2, 1))) * Math.sin(p * Math.PI) + squat;
        if (emotion === "surprised") {
          // "!" reads as an excited puff-up mid-air -- no separate wings to
          // spread on this mesh, so a wider (not just taller) bounce stands
          // in for "wings out", peaking at the top of the hop.
          const puff = 0.4 * Math.sin(p * Math.PI);
          scale.set(bounce + puff, bounce, bounce + puff * 0.6);
        } else {
          scale.set(bounce, bounce, bounce);
        }
        break;
      }
      case "approach": {
        const ease = EASE_OUT_BACK(p);
        position.z = 0.4 * ease;
        scale.setScalar(1 + 0.1 * ease);
        break;
      }
    }
    return [position, rotation, scale];
  }

  private setEmotionAccent(emotion: Emotion): void {
    if (!this.emotionAccent) return;
    const src = EMOTION_ACCENT_SRC[emotion];
    if (!src) {
      this.emotionAccent.style.opacity = "0";
      return;
    }
    this.emotionAccent.src = src;
    this.emotionAccent.style.opacity = "1";
  }
}

/**
 * Create a {@link ThreeCharacterRenderer} bound to the given root element, or
 * to the element with id {@link CHARACTER_ROOT_ID} when no element is provided.
 * Throws if no suitable root element can be found.
 */
export function createCharacterRenderer(root?: HTMLElement): CharacterRenderer {
  const element = root ?? document.getElementById(CHARACTER_ROOT_ID);
  if (!element) {
    throw new Error(
      `Character_Renderer: no root element provided and no element with id "${CHARACTER_ROOT_ID}" found.`,
    );
  }
  return new ThreeCharacterRenderer(element);
}

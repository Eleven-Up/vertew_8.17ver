// main.ts: Kiosk_UI browser entry point.
//
// Referenced by index.html as `./js/main.js`. Wires the three browser components
// together over the localhost WebSocket to the Conversation_Server:
//
//   Speech_Module (WebSpeechSttProvider + WebSpeechTtsEngine)
//     -> KioskController (conversation state machine)
//     -> Character_Renderer (DomCharacterRenderer bound to #character)
//     -> ChatClient (localhost WebSocket) -> Conversation_Server
//
// Everything here runs locally in the browser; the only connection opened is the
// localhost conversation socket, so the Kiosk_UI issues no internet requests
// (Req 9.1). A document/#stage tap drives the push-to-talk start (Req 1.x), and
// taps during TTS are ignored by the controller (Req 6.4).

import { createCharacterRenderer } from "./character.js";
import { ChatClient, buildWsUrl } from "./chat.js";
import { createChatLog } from "./chatlog.js";
import { KioskController, createDomKioskView } from "./kiosk.js";
import { WebSpeechSttProvider, WebSpeechTtsEngine } from "./speech.js";

/**
 * Construct and wire the Kiosk_UI. Returns the controller and chat client so a
 * harness/test can drive them; called automatically on DOM ready in the browser.
 */
export function startKiosk(): { controller: KioskController; chat: ChatClient } {
  // Character_Renderer bound to #character (index.html). Throws if absent.
  const renderer = createCharacterRenderer();

  // Speech_Module: replaceable STT provider + local English TTS (Req 2.6, 6.2/6.3).
  // Korean demo: capture and synthesize in Korean.
  const stt = new WebSpeechSttProvider("ko-KR");
  const tts = new WebSpeechTtsEngine("ko-KR");

  // Listening indicator + error banner, created inside #stage.
  const view = createDomKioskView();

  // On-screen conversation transcript + live speech caption.
  const chatLog = createChatLog();

  // Localhost conversation socket. URL derived from the page origin so it never
  // targets the internet (Req 9.1).
  const chat = new ChatClient({ url: buildWsUrl(window.location) });

  // Conversation state machine. Transcripts are pushed to the server via the chat
  // client when the UI moves listening -> processing (Req 4.3). The final
  // transcript is also logged as a customer bubble and clears the live caption.
  const controller = new KioskController({
    stt,
    tts,
    renderer,
    view,
    onSendTranscript: (text) => {
      chatLog.addUser(text);
      chatLog.clearLiveCaption();
      chat.send(text);
    },
    // Reflect the conversation state on <body data-state> so the page can show
    // the idle greeting, the listening animation, the speaking cue, etc.
    onStateChange: (state) => {
      document.body.dataset.state = state;
    },
  });

  // Stream live speech-to-text into the caption as the customer speaks.
  stt.onPartial?.((text) => chatLog.setLiveCaption(text));

  // Route server responses back into the state machine (Req 5.1) and log the
  // character's reply as a bubble; a socket failure during a turn surfaces as a
  // network error and recovers to idle (Req 2.5/9.5).
  chat.onResponse((response) => {
    controller.onServerResponse(response);
    chatLog.addCharacter(response.text);
  });
  chat.onNetworkError(() => controller.showError("network"));

  // Open the socket up front so the first turn does not pay the connect latency.
  chat.connect();

  // Push-to-talk: a screen tap anywhere on the stage starts a turn. The controller
  // honors the tap only in idle and ignores it during listening/speaking
  // (Req 1.5, 6.4). Clear any stale live caption when a new capture begins.
  const stage = document.getElementById("stage") ?? document.body;
  stage.addEventListener("pointerdown", () => {
    chatLog.clearLiveCaption();
    controller.onTap();
  });

  // Begin the ambient idle animation while awaiting the first tap (Req 5.3). The
  // controller also starts idle in its constructor; this is an explicit safeguard.
  renderer.playIdle();

  return { controller, chat };
}

// Auto-start in the browser once the DOM is ready. Guarded so importing this
// module in a non-DOM test context does not trigger side effects.
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => startKiosk());
  } else {
    startKiosk();
  }
}

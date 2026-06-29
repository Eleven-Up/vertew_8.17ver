# Requirements Document

## Introduction

The Vertew service is a voice conversation system that lets a friendly 2D merchant character converse with customers on behalf of a street market vendor. The character is rendered on a webpage displayed in Chromium fullscreen (kiosk mode) on a Raspberry Pi 5, with the HDMI output driving a hologram display. A customer taps the screen to start a push-to-talk interaction, speaks, and the character responds with synthesized voice, facial expression, and gesture.

The MVP targets a single concurrent user, short market-haggling-length conversations, one pre-made 2D merchant character with a prompt-configurable persona, and an English demo. The design minimizes internet usage: the user interface, character rendering, and text-to-speech run locally on the Pi, while only speech-to-text and the language model text call use the internet. Vendors enter store and product information through an admin page, which is incorporated into the conversation prompt.

This document defines the requirements for the MVP scope. Multi-user support, custom character generation, multilingual support, and a fully offline variant are noted as out of scope for this release.

## Glossary

- **Kiosk_UI**: The customer-facing webpage rendered in Chromium fullscreen on the Raspberry Pi that displays the character and handles screen taps.
- **Speech_Module**: The browser-based component responsible for speech-to-text (STT) capture and text-to-speech (TTS) playback, implemented with the Web Speech API and isolated as a replaceable module.
- **Character_Renderer**: The browser component that displays the 2D merchant character and animates facial expression and gesture.
- **Conversation_Server**: The FastAPI server running on the Raspberry Pi that manages conversation state, builds prompts, calls the language model, and serves the admin interface.
- **Prompt_Builder**: The Conversation_Server subcomponent that assembles the language model prompt from persona, store information, and recent conversation turns.
- **LLM_Client**: The Conversation_Server subcomponent that calls the Google Gemini Flash API and parses the returned response.
- **Admin_Interface**: The vendor-facing web page served by the Conversation_Server for entering and editing store and product information.
- **Data_Store**: The SQLite database storing store/product information and conversation logs.
- **Push_To_Talk**: The interaction mode where the microphone is activated only after the customer taps the screen.
- **Conversation_Turn**: A single exchange consisting of one customer utterance and one character response.
- **Emotion**: A named facial expression value returned by the language model (for example, "happy", "neutral", "surprised").
- **Gesture**: A named body motion value returned by the language model (for example, "wave", "idle", "point").
- **Persona**: The configurable personality description of the merchant character supplied as part of the system prompt.

## Requirements

### Requirement 1: Push-to-Talk Interaction Start

**User Story:** As a customer, I want to tap the screen to start speaking, so that the character listens only when I intend to talk and avoids reacting to background market noise.

#### Acceptance Criteria

1. WHILE the Kiosk_UI is in the idle state and no screen tap has been registered, THE Speech_Module SHALL keep the microphone inactive.
2. WHEN a customer taps the screen while the microphone is inactive, THE Speech_Module SHALL activate the microphone within 500 ms and begin capturing audio.
3. WHILE the microphone is active, THE Kiosk_UI SHALL display a visual listening indicator within 200 ms of microphone activation and SHALL keep it continuously visible for the entire duration the microphone remains active.
4. WHEN no speech is detected for a continuous silence period of 2 seconds after speech has begun, THE Speech_Module SHALL treat the current utterance as complete and deactivate the microphone.
5. IF a customer taps the screen while the microphone is already active, THEN THE Speech_Module SHALL ignore the additional tap and continue the current capture without resetting the silence period.
6. IF the microphone has been active for a maximum capture duration of 30 seconds without an end-of-utterance silence being detected, THEN THE Speech_Module SHALL deactivate the microphone and treat the captured audio as a completed utterance.
7. IF no speech is detected within 10 seconds of microphone activation, THEN THE Speech_Module SHALL deactivate the microphone and THE Kiosk_UI SHALL return to the idle state.
8. IF microphone access is denied or the microphone cannot be activated after a tap, THEN THE Speech_Module SHALL keep the microphone inactive and THE Kiosk_UI SHALL display an error message indicating that the microphone is unavailable, while preserving the idle state.

### Requirement 2: Speech-to-Text Conversion

**User Story:** As a customer, I want my spoken words converted to text, so that the character can understand what I say.

#### Acceptance Criteria

1. WHEN the microphone captures a customer utterance, THE Speech_Module SHALL convert the captured audio into a text transcript and return the transcript within 5 seconds of detecting end-of-speech.
2. WHILE capturing a customer utterance, IF no speech is detected for a continuous 3 seconds, THEN THE Speech_Module SHALL treat the utterance as complete and begin conversion.
3. WHEN a text transcript containing at least one recognized word is produced, THE Speech_Module SHALL send the transcript to the Conversation_Server.
4. IF speech-to-text conversion produces an empty transcript or returns a no-match result, THEN THE Kiosk_UI SHALL display a prompt asking the customer to tap and speak again, retain the idle-ready state, and allow up to 3 consecutive retry attempts.
5. IF the speech-to-text service does not return a result within 5 seconds or the network request fails, THEN THE Kiosk_UI SHALL display a connectivity error message indicating the service is unavailable and return to the idle state without sending a transcript to the Conversation_Server.
6. THE Speech_Module SHALL expose speech-to-text through an interface that allows the speech-to-text provider to be replaced without changing the Kiosk_UI or Conversation_Server.

### Requirement 3: Prompt Construction

**User Story:** As a vendor, I want the character's responses grounded in my store information and the recent conversation, so that the character answers relevantly about my products.

#### Acceptance Criteria

1. WHEN the Conversation_Server receives a customer transcript, THE Prompt_Builder SHALL construct a single prompt containing the Persona, the current store and product information, and the recent Conversation_Turn history.
2. THE Prompt_Builder SHALL include at most the 5 most recent Conversation_Turns in the prompt, and WHEN fewer than 5 Conversation_Turns exist, THE Prompt_Builder SHALL include all available Conversation_Turns.
3. IF no store or product information is available when constructing the prompt, THEN THE Prompt_Builder SHALL include the Persona and recent Conversation_Turn history and SHALL instruct the language model to state that no product information is currently available rather than omitting a response.
4. THE Prompt_Builder SHALL instruct the language model to return exactly one JSON object containing a non-empty text field of at most 500 characters, an emotion field whose value is one of the Emotion values defined in the Glossary, and a gesture field whose value is one of the Gesture values defined in the Glossary.
5. THE Prompt_Builder SHALL instruct the language model to produce text that contains no profanity and uses plain conversational vocabulary at a first-language reading level of grade 8 or below.

### Requirement 4: Language Model Response

**User Story:** As a customer, I want the character to reply meaningfully to my question, so that I get helpful answers about the products.

#### Acceptance Criteria

1. WHEN a prompt is constructed, THE LLM_Client SHALL send the prompt to the Google Gemini Flash API over the internet and SHALL wait for a response for a maximum of 10 seconds before treating the request as failed.
2. WHEN the Gemini Flash API returns a response within the 10-second timeout, THE LLM_Client SHALL parse the response into a text value of at most 1000 characters, an emotion value, and a gesture value.
3. WHEN the LLM_Client parses a valid response, THE Conversation_Server SHALL send the text, emotion, and gesture values to the Kiosk_UI.
4. IF the Gemini Flash API response is not valid JSON or is missing the text, emotion, or gesture field, THEN THE LLM_Client SHALL substitute a default neutral response containing a predefined fallback text value indicating the request could not be understood, an emotion value of "neutral", and a gesture value of "idle".
5. IF the Gemini Flash API is unreachable, returns an error status, or does not respond within the 10-second timeout, THEN THE Conversation_Server SHALL send a fallback response to the Kiosk_UI containing a predefined text value indicating the character is temporarily unavailable, an emotion value of "neutral", and a gesture value of "idle".
6. WHEN the emotion or gesture value returned by the language model is not in the set of supported Emotion and Gesture values, THE Character_Renderer SHALL render the "neutral" Emotion or "idle" Gesture respectively.

### Requirement 5: Character Rendering

**User Story:** As a customer, I want to see the merchant character express emotion and gesture as it talks, so that the interaction feels natural and engaging.

#### Acceptance Criteria

1. WHEN the Kiosk_UI sends a response containing a recognized Emotion value and a recognized Gesture value, THE Character_Renderer SHALL begin displaying the corresponding facial expression and body motion within 500 milliseconds of receipt.
2. IF a received response contains an Emotion value or Gesture value that is not in the defined set, or contains a missing or empty Emotion or Gesture value, THEN THE Character_Renderer SHALL display the neutral facial expression and idle body motion and continue rendering without interruption.
3. WHILE no conversation is active, THE Character_Renderer SHALL display a continuously looping idle animation.
4. WHILE the character voice output is playing, THE Character_Renderer SHALL animate the character mouth such that each mouth movement begins and ends within 150 milliseconds of the corresponding segment of the voice output.
5. WHEN the character voice output stops, THE Character_Renderer SHALL stop the mouth animation within 150 milliseconds and return the mouth to a closed resting position.
6. THE Character_Renderer SHALL support a defined set of at least one named Emotion value and at least one named Gesture value, where each named value maps to exactly one facial expression or body motion of the single 2D character.

### Requirement 6: Text-to-Speech Voice Output

**User Story:** As a customer, I want to hear the character speak its response, so that I can interact by voice without reading text.

#### Acceptance Criteria

1. WHEN the Kiosk_UI receives a non-empty response text value of up to 1000 characters, THE Speech_Module SHALL begin audible synthesis of the text into speech using the browser local text-to-speech engine within 1 second of receiving the value.
2. THE Speech_Module SHALL perform all text-to-speech synthesis locally in the browser without sending any data over the internet.
3. THE Speech_Module SHALL synthesize speech in English for the MVP demo.
4. WHILE the Speech_Module is producing voice output, THE Kiosk_UI SHALL ignore screen taps.
5. WHEN voice output completes, THE Kiosk_UI SHALL return to the idle state within 1 second and await the next screen tap.
6. IF the browser local text-to-speech engine is unavailable or fails to start synthesis within 1 second, THEN THE Kiosk_UI SHALL display a visible message indicating that voice output could not be produced and SHALL return to the idle state within 1 second.
7. IF the received response text value is empty or contains only whitespace, THEN THE Speech_Module SHALL skip synthesis and THE Kiosk_UI SHALL return to the idle state within 1 second.

### Requirement 7: Vendor Knowledge Administration

**User Story:** As a vendor, I want to enter my store and product information through an admin page, so that the character can answer questions about my offerings.

#### Acceptance Criteria

1. WHEN a vendor opens the Admin_Interface, THE Conversation_Server SHALL display the current store and product information within 3 seconds.
2. IF no store or product information exists when a vendor opens the Admin_Interface, THEN THE Conversation_Server SHALL display empty input fields for store and product information.
3. WHEN a vendor submits store or product information with all required fields populated and each text field containing 1 to 2000 characters, THE Conversation_Server SHALL store the submitted information in the Data_Store and display a confirmation indication within 3 seconds.
4. IF a vendor submits store or product information with an empty required field or a text field exceeding 2000 characters, THEN THE Conversation_Server SHALL reject the submission, retain the values previously entered in the Admin_Interface, and display an error indication identifying the invalid field.
5. IF storing submitted information in the Data_Store fails, THEN THE Conversation_Server SHALL retain the values entered in the Admin_Interface and display an error indication that the save did not complete.
6. WHEN store or product information is saved, THE Prompt_Builder SHALL include the updated information in every prompt generated after the save completes.
7. WHERE a vendor sets a Persona value of 1 to 500 characters in the Admin_Interface, THE Prompt_Builder SHALL include the Persona value in the system prompt.

### Requirement 8: Conversation Data Storage

**User Story:** As a vendor, I want conversations and store data persisted, so that information survives restarts and conversations can be reviewed.

#### Acceptance Criteria

1. WHEN a Conversation_Turn completes, THE Conversation_Server SHALL record the customer transcript, the character response text, and a completion timestamp in the Data_Store within 2 seconds.
2. WHEN the Conversation_Server starts, THE Conversation_Server SHALL load the stored store and product information from the Data_Store.
3. IF recording a completed Conversation_Turn in the Data_Store fails, THEN THE Conversation_Server SHALL retry the write at most 1 time and, if it still fails, present an error indication while retaining any previously committed records unchanged.
4. IF loading store and product information from the Data_Store fails at startup, THEN THE Conversation_Server SHALL present an error indication and block new Conversation_Turns until the information can be loaded.
5. THE Data_Store SHALL persist store information, product information, and conversation logs across Conversation_Server restarts such that committed records are neither lost nor modified.

### Requirement 9: Network-Saving Operation

**User Story:** As a vendor operating on a weak market wifi connection, I want the system to minimize internet usage, so that the service remains responsive and low-cost.

#### Acceptance Criteria

1. THE Kiosk_UI SHALL communicate with the Conversation_Server exclusively over localhost without issuing any internet network requests.
2. THE Conversation_Server SHALL initiate internet network requests only for the speech-to-text request and the Gemini Flash API request, and SHALL perform all UI rendering, character animation, and text-to-speech operations locally with zero internet usage.
3. THE LLM_Client SHALL request the answer text, emotion, and gesture from the Gemini Flash API in a single combined call per Conversation_Turn, and SHALL NOT issue more than one Gemini Flash API call per Conversation_Turn.
4. WHEN the Conversation_Server issues a speech-to-text request or a Gemini Flash API request, THE Conversation_Server SHALL apply a network timeout of 10 seconds per request.
5. IF a speech-to-text request or a Gemini Flash API request fails or exceeds its 10-second timeout, THEN THE Conversation_Server SHALL abort the request, present a user-facing error indication that the network operation could not be completed, and preserve the current Conversation_Turn state without partial output.
6. IF a speech-to-text request or a Gemini Flash API request fails or times out, THEN THE Conversation_Server SHALL retry the failed request at most 1 time before reporting failure.

### Requirement 10: Content Suitability

**User Story:** As a vendor, I want the character to respond appropriately, so that customers receive courteous, profanity-free service.

#### Acceptance Criteria

1. IF a parsed character response text contains one or more terms matching the configured profanity term list, THEN THE Conversation_Server SHALL replace the entire response with a predefined courteous fallback response that itself contains no terms from the profanity term list, and SHALL transmit only the fallback response to the Kiosk_UI.
2. WHEN a parsed character response text contains no terms matching the configured profanity term list, THE Conversation_Server SHALL transmit the response unchanged to the Kiosk_UI.
3. THE Prompt_Builder SHALL include in every constructed prompt an instruction that directs the language model to exclude all profanity from its response.
4. THE Prompt_Builder SHALL include in every constructed prompt an instruction that directs the language model to respond using polite, service-oriented language consistent with a market vendor addressing a customer.

### Requirement 11: Kiosk Display Operation

**User Story:** As a vendor, I want the service to start automatically in fullscreen on the Pi, so that the hologram display shows the character without manual setup.

#### Acceptance Criteria

1. WHEN the Raspberry Pi completes startup and the kiosk autostart script executes, THE Kiosk_UI SHALL launch in Chromium fullscreen mode within 60 seconds.
2. THE Kiosk_UI SHALL render output at the resolution reported by the connected HDMI display, filling 100% of the screen area with no visible scrollbars, window borders, or title bar.
3. WHILE running in kiosk mode, THE Kiosk_UI SHALL hide browser navigation controls and the address bar.
4. WHILE running in kiosk mode, THE Kiosk_UI SHALL hide the mouse cursor after 5 seconds of no input device activity.
5. IF Chromium fails to launch in fullscreen mode within 60 seconds of the autostart script executing, THEN THE Kiosk_UI SHALL retry the launch up to 3 times and, after the final failed attempt, present a visible on-screen indication that the display failed to start.

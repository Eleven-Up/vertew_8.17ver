# Raspberry Pi deployment

Target hardware: Raspberry Pi 5 (4GB), 64-bit Raspberry Pi OS with Desktop. This
branch (`feat/raspberry-pi`) carries the Pi-tuned config; the app code itself is
identical to the PC/browser branch.

## Quick start: one command

```bash
git clone <this repo> vertew && cd vertew
git checkout feat/raspberry-pi
./scripts/install.sh
```

That sets up the backend venv + dependencies, builds the frontend (or tells you
what to copy over if Node isn't on the Pi), creates `backend/.env` from the
example if it's missing, and installs + enables the systemd services and the
kiosk autostart entry. **After this, Vertew starts automatically on every
boot -- no manual step, no script to run by hand.** (There's no ".exe"
equivalent on Linux; this script *is* the one-file "just run it" version.)

Two things it can't do for you:

- **Add your LLM API key.** Open `backend/.env` afterward and fill in
  `GROQ_API_KEY` (or whichever `LLM_PROVIDER` you're using).
- **64-bit OS.** `faster-whisper`'s `ctranslate2` dependency only ships
  prebuilt wheels for 64-bit ARM; on 32-bit Raspberry Pi OS the install would
  need to compile from source (slow/unreliable on a Pi). Re-flash with the
  64-bit image first if unsure.

Re-running `install.sh` later (e.g. after `git pull`) is safe -- every step
checks before it acts.

## What it does, if you want to do any of it by hand

<details>
<summary>Manual steps (click to expand)</summary>

### 1. Backend venv + dependencies

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Build the frontend once

`frontend/js/main.js`/`mock.js` are the esbuild output and are gitignored, so
they don't ship in the repo. Either:

- Build directly on the Pi (needs Node.js): `cd frontend && npm install && npm run build`, or
- Build on your dev machine and `scp` `frontend/js/main.js` and `frontend/js/mock.js`
  over, so the Pi never needs Node.js installed at all.

### 3. Configure `backend/.env`

Copy `backend/.env.example` to `backend/.env` and fill in your LLM key
(`LLM_PROVIDER`/`GROQ_API_KEY` etc.). This branch's example already defaults
`STT_PROVIDER=local` and `STT_MODEL_SIZE=base`, tuned for the Pi 5's CPU rather
than a desktop dev machine -- drop to `tiny` if a reply feels slow to arrive,
or try `small` if the Pi 5 keeps up and you want the accuracy back.

### 4. Autostart on boot

```bash
sudo cp scripts/vertew-backend.service.example /etc/systemd/system/vertew-backend.service
sudo cp scripts/vertew-sensor.service.example /etc/systemd/system/vertew-sensor.service   # optional
# Edit both: replace /opt/vertew with wherever you actually cloned the repo,
# and User=pi with your actual username if different.
sudo systemctl daemon-reload
sudo systemctl enable --now vertew-backend.service
sudo systemctl enable --now vertew-sensor.service   # optional

mkdir -p ~/.config/autostart
cp scripts/vertew-kiosk.desktop.example ~/.config/autostart/vertew-kiosk.desktop
# same path/username edit here
```

</details>

## Pre-seed the Whisper model (optional, avoids relying on Pi Wi-Fi at demo time)

faster-whisper downloads its model from Hugging Face Hub on first use (a few
hundred MB). To avoid needing internet at the actual demo:

```bash
# On your dev machine, once (downloads into ~/.cache/huggingface):
python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
# Then copy the cache to the Pi:
scp -r ~/.cache/huggingface pi@<pi-host>:~/.cache/
```

## Microphone

The Pi's onboard 3.5mm jack is output-only on most models -- plug in a USB
microphone. Confirm it's picked up before wiring anything else:

```bash
arecord -l   # should list your USB mic as a capture device
```

## One thing that will bite you if skipped

`getUserMedia` (microphone access) only works in a "secure context": `localhost`
is exempt, but a plain `http://<pi-lan-ip>:8000` is not. This only matters if a
*separate* device (e.g. a tablet) loads the Kiosk_UI over the LAN instead of the
Pi's own screen running Chromium against `http://localhost:8000` -- the on-Pi
kiosk display is unaffected either way.

## Sanity check before the demo

```bash
curl -s http://localhost:8000/api/stt/config   # should print {"provider":"local"}
```

Then open the kiosk URL, tap the screen, and talk -- a reply should land a
second or two after you stop speaking (batch transcription, not streaming, so
some delay after you finish is expected and normal).

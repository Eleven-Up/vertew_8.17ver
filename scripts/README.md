# Raspberry Pi deployment

Target hardware: Raspberry Pi 5 (4GB), 64-bit Raspberry Pi OS with Desktop. This
branch (`feat/raspberry-pi`) carries the Pi-tuned config; the app code itself is
identical to the PC/browser branch.

## 1. Get the code and dependencies onto the Pi

```bash
git clone <this repo> /opt/vertew
cd /opt/vertew/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`faster-whisper`'s `ctranslate2` dependency needs a **64-bit** OS to install from
a prebuilt wheel; on 32-bit Raspberry Pi OS it has no wheel and would need to
compile from source (slow/unreliable on a Pi). If you're on 32-bit, re-flash with
the 64-bit image first.

## 2. Build the frontend once

`frontend/js/main.js`/`mock.js` are the esbuild output and are gitignored, so
they don't ship in the repo. Either:

- Build directly on the Pi (needs Node.js): `cd frontend && npm install && npm run build`, or
- Build on your dev machine and `scp` `frontend/js/main.js` and `frontend/js/mock.js`
  over, so the Pi never needs Node.js installed at all.

## 3. Configure `backend/.env`

Copy `backend/.env.example` to `backend/.env` and fill in your LLM key
(`LLM_PROVIDER`/`GROQ_API_KEY` etc.). This branch's example already defaults
`STT_PROVIDER=local` and `STT_MODEL_SIZE=base`, tuned for the Pi 5's CPU rather
than a desktop dev machine -- drop to `tiny` if a reply feels slow to arrive,
or try `small` if the Pi 5 keeps up and you want the accuracy back.

### Pre-seed the Whisper model (optional, avoids relying on Pi Wi-Fi at demo time)

faster-whisper downloads its model from Hugging Face Hub on first use (a few
hundred MB). To avoid needing internet at the actual demo:

```bash
# On your dev machine, once (downloads into ~/.cache/huggingface):
python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
# Then copy the cache to the Pi:
scp -r ~/.cache/huggingface pi@<pi-host>:~/.cache/
```

## 4. Microphone

The Pi's onboard 3.5mm jack is output-only on most models -- plug in a USB
microphone. Confirm it's picked up before wiring anything else:

```bash
arecord -l   # should list your USB mic as a capture device
```

## 5. Autostart on boot

Two systemd services (`vertew-backend.service.example`, and
`vertew-sensor.service.example` if the physical distance sensor is wired up) plus
one XDG autostart entry for the kiosk browser (`vertew-kiosk.desktop.example`).

```bash
# Backend (and sensor bridge, if used) as systemd services:
sudo cp scripts/vertew-backend.service.example /etc/systemd/system/vertew-backend.service
sudo cp scripts/vertew-sensor.service.example /etc/systemd/system/vertew-sensor.service   # optional
sudo systemctl daemon-reload
sudo systemctl enable --now vertew-backend.service
sudo systemctl enable --now vertew-sensor.service   # optional

# Kiosk browser: autostart within the desktop session (works under both LXDE
# and Wayfire/labwc, since both honor the XDG autostart spec).
mkdir -p ~/.config/autostart
cp scripts/vertew-kiosk.desktop.example ~/.config/autostart/vertew-kiosk.desktop
```

Both `.example` systemd units assume the repo lives at `/opt/vertew` and the
venv at `/opt/vertew/.venv` -- edit the paths if yours differs.

## 6. One thing that will bite you if skipped

`getUserMedia` (microphone access) only works in a "secure context": `localhost`
is exempt, but a plain `http://<pi-lan-ip>:8000` is not. This only matters if a
*separate* device (e.g. a tablet) loads the Kiosk_UI over the LAN instead of the
Pi's own screen running Chromium against `http://localhost:8000` -- the on-Pi
kiosk display is unaffected either way.

## 7. Sanity check before the demo

```bash
curl -s http://localhost:8000/api/stt/config   # should print {"provider":"local"}
```

Then open the kiosk URL, tap the screen, and talk -- a reply should land a
second or two after you stop speaking (batch transcription, not streaming, so
some delay after you finish is expected and normal).

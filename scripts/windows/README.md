# Windows dev launcher

Double-click **`start-vertew.bat`** to start the backend (serving the
hologram/customer/vendor frontends) and a Cloudflare quick tunnel, then print
the local and public URLs. Double-click **`stop-vertew.bat`** to shut both
back down.

Requirements (one-time, see the repo README's "Local setup"): `.venv` created
at the repo root with `backend/requirements.txt` installed, and the
frontend/apps builds already in place. `cloudflared` is optional -- without it
on PATH, you still get the local URL, just no public one.

## What it does

- `start-vertew.ps1`: stops any run it previously started (so re-running never
  piles up stray processes), starts `uvicorn` (no `--reload` -- this is for
  quickly getting a demo up, not active development; keep using the manual
  `--reload` workflow while coding), waits for it to answer, starts
  `cloudflared tunnel --url http://localhost:8000` if available, waits for its
  `https://*.trycloudflare.com` URL to appear in its log, and writes both PIDs
  plus the URL to `vertew-run.json` (repo root, gitignored) so the stop script
  can find them again. The public URL is also saved to `vertew-url.txt`.
- `stop-vertew.ps1`: reads `vertew-run.json` and stops exactly those two
  processes. Safe to run even if nothing is running.

## Notes

- The Cloudflare quick tunnel is account-less and public: anyone with the
  printed link can reach your local server for as long as it's running, with
  no login. Only share it when you mean to.
- Logs land in `backend/server_run.log` / `.err.log` and
  `backend/cloudflared.log` / `.err.log` if you need to debug a failed start.

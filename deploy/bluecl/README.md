# BlueCL path deployment

Target: `https://bluecl.cloud/aicap/`.

Recovery 2026-11-03: the API stayed healthy, but the nginx global method map regenerated on Oct 29 omitted the two AICAP POST exceptions, causing HTTP 405 on start/step. Applied `repair-api-methods.py` to both the live map and installed `/usr/local/sbin/bluecl-security` generator. Backup: `/var/backups/aicap-method-repair-6il66mb6`. Nginx syntax checks and reload passed. Public `/api/control` and `/api/step` returned 200, and browser automatic playback and completion counters advanced. No backend restart, static redeployment, broad method allowance or Cloudflare change. Preserve these exceptions when replacing the generator in future maintenance.

## Actual GPU router

The live dashboard relays requests and status through the existing `/aicap/api/control` path. The API expects the router SSH forward at `127.0.0.1:18823`; the router listens on EC2 loopback port 8008.

For a new installation, create `~/.config/aicap/router-tunnel.env` with `AICAP_ROUTER_TARGET=ubuntu@<router-host>`. Keep the SSH key locally at `~/.ssh/aicap-router-tunnel` and register the verified SSH host key before starting the tunnel. Install `aicap-router-tunnel.service` under `~/.config/systemd/user/`, then run `systemctl --user daemon-reload` and `systemctl --user enable --now aicap-router-tunnel.service`. Check the connection with `curl --fail http://127.0.0.1:18823/status` on the BlueCL host.

The demo allows at most three live requests in flight and defaults to 120 live requests per hour across visitors (`AICAP_REAL_HOURLY_LIMIT`).

## Historical releases

October release: frontend `20261023T160042Z-bc79a5fc`, backend `20261024-queue`. Restored the original 650ms fact / 1s cost count-ups and fast balancer phases. Removed the numeric speed-difference badge. Stage explanations unfold in a separate side message box without gating the balancer, with reduced-motion support and mobile stacking. Top-right completion status, round dots, exact-tie rotation, FIFO queues and visitor isolation remain intact. See `docs/request-story.md`. No API restart or security-rule changes. Previous notes below are historical.

- Frontend: build with `npm run build -- --base=/aicap/ --outDir=dist/aicap`.
- Upload only `aicap/index.html`, hashed `aicap/assets/`, font and font license using the `bluecl-deploy` static release helper. Never upload backend source, dataset, `.env`, credentials or server scripts to the public root.
- Private backend: `/home/solty/.local/share/aicap/current`, Python virtual environment at `/home/solty/.local/share/aicap/venv`.
- User service: `aicap.service`, listening only on `127.0.0.1:18822`. Existing services and port 8000 are not reused.
- Install backend dependencies with `pip install -r backend/requirements.txt -c deploy/bluecl/constraints.txt`. The deployment constraints preserve compatibility with this host's older x86 CPU; newer NumPy wheels can require unsupported X86_V2 instructions.
- Nginx maps `/aicap/api/` to the backend's `/api/`. Its configuration requires one reviewed, user-invoked sudo installation using `install-nginx-once.sh`.
- Existing root nginx config is backed up before editing. No DNS, certificate, firewall or sudo policy changes.

The API now isolates mutable simulation state per visitor using a random HttpOnly, SameSite=Lax cookie (Secure over HTTPS). Tabs in the same browser share the session; different browsers/visitors do not share queues, KV caches, controls, speed or policy. A trained read-only predictor and immutable trace remain shared. Sessions expire after 30 minutes without an API request, are capped at 48, and reset on service restart. This is an anonymous demo, not authenticated durable storage. One Uvicorn worker is required; use a shared session store before adding workers. State responses are private/no-store and must never be cached at the proxy/CDN.

Playback advances against a monotonic clock when that visitor polls or sends a command. Inactive sessions consume no background simulation ticks; a returning visitor catches up until expiry. Frontend polling ignores stale responses during controls. The four-step narrated flow uses a clearly labeled fictional question, real routing/completion values, and explicit separation between offline supervised prediction and future reinforcement learning. No live retraining or tunable learned routing weights are claimed; the current completion cost uses equal 1:1:1 terms.

Check the service with `systemctl --user status aicap.service` and inspect logs with `journalctl --user -u aicap.service`.

After proxy setup, deploy and verify static files using the skill's manifest and HTTPS hash verification, then check `/aicap/api/state`, controls and mobile layout in a browser. A private API being ready does not mean the public deployment is complete.

## Deployment verification — 2026-10-23

Static release `20261023T102446Z-c67abea8` was installed (5 files, backup at `/var/lib/bluecl-deploy/main/20261023T102446Z-c67abea8`). Public verification is incomplete: four asset hashes passed, but Cloudflare returned a challenge/block for HTML verification and POST controls. The origin serves the page with HTTP 200; its global method map currently rejects AICAP POST with HTTP 405. `allow-api-post-once.py` prepares a backed-up, validated change adding only the two exact API endpoints. Run it with sudo only after review. The existing `bluecl-security` policy generator can overwrite this map on a future hardening refresh; preserve the exact AICAP allowances in that maintenance workflow. Do not disable global protections to work around Cloudflare blocks.

Follow-up: the two exact POST allowances have now been applied. A valid JSON POST to the origin `/aicap/api/step` succeeds, while the public browser control still returns Cloudflare HTTP 403. Diagnostic public POST Ray ID: `a4f00caf99955cbc-HKG`. Remaining blocker is the Cloudflare rule, not another nginx installation. Review the matching Security Event before making any narrowly scoped rule exception.

Resolved: after explicit user confirmation, the existing Cloudflare custom rule `BlueCL - restrict methods and private paths` was saved with only `/aicap/api/control` and `/aicap/api/step` added to its POST exception set. Block action, Active status, private-path clauses and all other rules were preserved. Public browser verification passed for one-second stepping, continuous replay and pause: 58 arrivals, 7 completions, changing A/B/C queues, contexts and KV values were observed. Mobile layout checked at 390×844; viewport override reset afterward. All four CSS/JS/font/license SHA256 hashes match over HTTPS. HTML returns 200 but includes an injected Cloudflare Insights script, so its raw SHA256 differs from the 478-byte source; do not report raw HTML hash verification as passed. Some automated clients still encounter Cloudflare 403; normal tested browser controls work. Simulation remains a shared in-memory public demo, not actual GPU inference.

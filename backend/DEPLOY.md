# Backend Deployment (Contabo VPS + Cloudflare Tunnel)

The API is served at `https://api.bostonsalsa.org` (the default in
`lib/constants.ts` and `scripts/fetch_submissions.py`; override with
`NEXT_PUBLIC_API_URL` / `BLD_API_URL`).

## Server Setup

```bash
# Create user
sudo useradd -r -m -s /bin/bash bld

# Copy files
sudo mkdir -p /opt/bld-api
sudo cp server.py requirements.txt /opt/bld-api/
sudo chown -R bld:bld /opt/bld-api

# Create venv and install deps (requirements.txt is pinned to the versions
# the test suite runs against; bump deliberately, not by re-resolving)
sudo -u bld bash -c 'cd /opt/bld-api && python3 -m venv venv && venv/bin/pip install -r requirements.txt'

# Create .env — it holds secrets, so it must be 0600 and owned by bld.
# Generate the admin token with:  openssl rand -hex 32
sudo install -m 600 -o bld -g bld /dev/null /opt/bld-api/.env
sudo tee /opt/bld-api/.env >/dev/null <<'EOF'
BLD_FRONTEND_ORIGIN=https://bostonsalsa.org,https://www.bostonsalsa.org
BLD_SUBMISSIONS_PATH=/opt/bld-api/submissions.json
BLD_ADMIN_TOKEN=<openssl rand -hex 32>
TURNSTILE_SECRET=<Cloudflare Turnstile secret key>
TURNSTILE_HOSTNAMES=bostonsalsa.org,www.bostonsalsa.org
# We sit behind cloudflared, so the client IP comes from CF-Connecting-IP.
# Set to false only if the port is ever exposed without a proxy.
TRUST_PROXY_HEADERS=true
EOF
sudo chmod 600 /opt/bld-api/.env

# Install and start systemd service
sudo cp bld-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bld-api

# Verify
curl http://127.0.0.1:8001/health
```

`BLD_ADMIN_TOKEN` is required: without it `/api/submissions` refuses every
request and `scripts/fetch_submissions.py` (the `submissions` scraper in the
pipeline) exits non-zero. The same value goes in the repo's `.env` on the
machine that runs the pipeline:

```
BLD_API_URL=https://api.bostonsalsa.org
BLD_ADMIN_TOKEN=<same value as on the VPS>
```

The unit file runs with `ProtectSystem=strict`, so the service can only write
under `/opt/bld-api` (`ReadWritePaths`). Keep `BLD_SUBMISSIONS_PATH` inside
that directory or extend `ReadWritePaths` to match.

## Cloudflare Tunnel

Assuming `cloudflared` is already installed and authenticated on the VPS:

```bash
# Add DNS route (if tunnel already exists, e.g. twic-api)
cloudflared tunnel route dns <TUNNEL_NAME> api.bostonsalsa.org

# Or create a new tunnel
cloudflared tunnel create bld-api
cloudflared tunnel route dns bld-api api.bostonsalsa.org
```

Add ingress rule to `/etc/cloudflared/config.yml`:

```yaml
ingress:
  # ... existing rules (e.g. api.chessautoprep.com) ...
  - hostname: api.bostonsalsa.org
    service: http://127.0.0.1:8001
  - service: http_status:404
```

Restart cloudflared:

```bash
sudo systemctl restart cloudflared
```

## Verify End-to-End

Every submission must carry a Turnstile token in `cf_turnstile_token`, and
the server verifies it against Cloudflare (expected action
`turnstile-spin-v2`, hostname in `TURNSTILE_HOSTNAMES`). A request without
one is rejected with `403 CAPTCHA verification failed` — that is the
expected negative result, not a deployment problem:

```bash
# Expect 403: no token
curl -sS -X POST https://api.bostonsalsa.org/api/submit-event \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@test.com","event_name":"Test","event_url":"https://example.com","styles":["salsa"]}'
```

For a real end-to-end check, take a fresh token from the live form: open
`https://bostonsalsa.org/submit`, let the Turnstile widget complete, and copy
`cf_turnstile_token` from the POST body in the browser's Network tab (tokens
are single-use and expire in ~5 minutes), then:

```bash
curl -sS -X POST https://api.bostonsalsa.org/api/submit-event \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@test.com","event_name":"Test","event_url":"https://example.com","styles":["salsa"],"cf_turnstile_token":"<token from the form>"}'
# → {"status":"ok","message":"Event submitted for review"}
```

Then confirm it was stored (this is the call `fetch_submissions.py` makes):

```bash
curl -sS https://api.bostonsalsa.org/api/submissions \
  -H "Authorization: Bearer $BLD_ADMIN_TOKEN" | python3 -m json.tool
```

Rate limit: 5 submissions per minute per client IP (`CF-Connecting-IP`).
Bodies over 64 KB are refused with 413; field lengths and the allowed style
set are enforced with 422.

## Reviewing Submissions

Submissions are appended to `/opt/bld-api/submissions.json`. The pipeline
pulls them with `scripts/fetch_submissions.py`; to look on the box:

```bash
sudo -u bld python3 -m json.tool /opt/bld-api/submissions.json
```

Writes are atomic and serialized on `submissions.json.lock`. If the file is
ever found unparseable, the server moves it to
`submissions.corrupt-<timestamp>.json` and starts a fresh list rather than
overwriting it — recover the entries from that file by hand.

After the pending queue has been reviewed, clear the store; the cleared
batch is written to `submissions-archive-<timestamp>.json` next to it, not
discarded:

```bash
curl -sS -X POST https://api.bostonsalsa.org/api/submissions/clear \
  -H "Authorization: Bearer $BLD_ADMIN_TOKEN"
```

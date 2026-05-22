# Backend Deployment (Contabo VPS + Cloudflare Tunnel)

## Server Setup

```bash
# Create user
sudo useradd -r -m -s /bin/bash bld

# Copy files
sudo mkdir -p /opt/bld-api
sudo cp server.py requirements.txt /opt/bld-api/
sudo chown -R bld:bld /opt/bld-api

# Create venv and install deps
sudo -u bld bash -c 'cd /opt/bld-api && python3 -m venv venv && venv/bin/pip install -r requirements.txt'

# Create .env
sudo tee /opt/bld-api/.env <<'EOF'
BLD_FRONTEND_ORIGIN=https://bostonlatindance.com
BLD_SUBMISSIONS_PATH=/opt/bld-api/submissions.json
EOF
sudo chown bld:bld /opt/bld-api/.env

# Install and start systemd service
sudo cp bld-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bld-api

# Verify
curl http://127.0.0.1:8001/health
```

## Cloudflare Tunnel

Assuming `cloudflared` is already installed and authenticated on the VPS:

```bash
# Add DNS route (if tunnel already exists, e.g. twic-api)
cloudflared tunnel route dns <TUNNEL_NAME> api.bostonlatindance.com

# Or create a new tunnel
cloudflared tunnel create bld-api
cloudflared tunnel route dns bld-api api.bostonlatindance.com
```

Add ingress rule to `/etc/cloudflared/config.yml`:

```yaml
ingress:
  # ... existing rules (e.g. api.chessautoprep.com) ...
  - hostname: api.bostonlatindance.com
    service: http://127.0.0.1:8001
  - service: http_status:404
```

Restart cloudflared:

```bash
sudo systemctl restart cloudflared
```

## Verify End-to-End

```bash
curl -X POST https://api.bostonlatindance.com/api/submit-event \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@test.com","event_name":"Test","event_url":"https://example.com"}'
```

## Reviewing Submissions

Submissions are appended to `/opt/bld-api/submissions.json`. View them with:

```bash
cat /opt/bld-api/submissions.json | python3 -m json.tool
```

# Hermes v0.17 "Reach Release" — Feature Audit Template

Run when a new Hermes release drops and Jim asks "what should we enable?" The
audit compares the release changelog against the live system to produce a
prioritized enable/skip table.

## Method

1. **Check current version:** `hermes --version` (or `/opt/hermes/bin/hermes --version`)
2. **Fetch the release notes:** `web_extract` the GitHub release tag page
   (e.g. `https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.19`)
3. **Check each feature against live config:**
   - `grep` config.yaml for platform/feature names
   - `hermes tools list` for toolset status
   - `hermes photon status`, `hermes webhook list`, etc. for platform-specific status
   - `cronjob action=list` for cron-based features
4. **Classify each feature:**
   - ✅ Already active (no action)
   - 🔴 Not enabled — recommend enabling (low effort, high value)
   - 🟡 Available but skip (with reason)
   - N/A — not applicable to this deployment

## v0.17 audit results (Jun 22, 2026)

### Already active
- Background subagents (`delegate_task`)
- Image editing (image-to-image via `image_gen` toolset)
- Atomic batch memory operations
- Rich text on Telegram
- Curator zero-token routine runs
- Checkpoints (filesystem snapshots, 20 max, auto-prune)
- Dashboard profile builder
- Skills Hub browser rehaul

### Enabled during this audit
- **Webhook platform** — added `webhook: enabled: true` to config.yaml with
  `host: 127.0.0.1` (localhost only, privacy-first). Port 8644. No secret set
  yet. Requires gateway restart to activate.

### Skipped (with reasons)
- **iMessage / Photon** — Jim doesn't trust the third-party service. Photon
  sits between Hermes and Apple's iMessage infrastructure. Parked until trust
  concerns resolved or a self-hosted alternative (BlueBubbles, HA companion)
  is evaluated.
- **Raft agent network** — Jim is satisfied with WhatsApp + Slack + Telegram.
  No need for a shared human+agent workspace.
- **SimpleX Chat** — No need; existing channels cover all communication.
- **WhatsApp Business Cloud API** — Requires Meta Business account + public
  webhook URL. Baileys bridge works fine; Cloud API is more stable but needs
  exposed endpoint.
- **Grok Composer model** — Coding-focused; GLM 5.2 working well.
- **Desktop app upgrades** — Docker/container deployment, no desktop app.

## Privacy decision: webhook host binding

When enabling the webhook platform, bind to `127.0.0.1` (localhost only) by
default, NOT `0.0.0.0` (all interfaces). Jim is privacy-first — external
services can't reach the webhook endpoint. If external webhooks are needed
later (GitHub, CI), add a secret token and open it up then.

Config:
```yaml
gateway:
  platforms:
    webhook:
      enabled: true
      extra:
        host: "127.0.0.1"
        port: 8644
        secret: ""
```
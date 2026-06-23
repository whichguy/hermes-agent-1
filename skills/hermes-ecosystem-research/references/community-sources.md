# Hermes Community Source Map

Vetted sources for the Layer-2 (community signal) half of ecosystem research.
Access notes reflect what worked / failed in practice.

## Primary: Hermes Atlas — `hermesatlas.com`

The community map of Hermes Agent. ~160-170 open-source repos across ~12 categories,
live GitHub star data, quality-filtered + security-reviewed, curated weekly.

- **Reliable to read in full via the `browser` toolset** (navigate + snapshot). No bot-wall.
- Navigation: MAP, LISTS, HANDBOOK, DEV, REPORTS, NEWSLETTER, SOURCE.
- **Curated lists** (the most useful cut for recommendations):
  - Best memory providers — semantic/graph/cross-session memory (the fastest-growing category)
  - Top skills — popular skills + registries on the agentskills.io standard
  - Deployment options — Docker, Nix, systemd, managed cloud
  - Multi-agent frameworks — fleet mgmt, swarm coordinators, delegation
  - Developer tools — CLIs, linters, migration helpers, token trackers
  - Workspaces & GUIs — web/desktop chat UIs, memory browsers, config dashboards
- **Categories on the map** include: Core & official, Workspaces & GUIs, Skills & skill
  registries, Memory & context, plus more below the fold.

### Notable repos seen (snapshot — star counts are time-sensitive, re-check live)

Workspaces / GUIs (the category to recommend for a chat-only user who wants to *see* state):
- `nesquina/hermes-webui` — web + phone UI (very high stars)
- `fathah/hermes-desktop` — desktop companion (install/configure/chat)
- `EKKOLearnAI/hermes-web-ui` — explicitly multi-platform: Telegram, Discord, Slack, WhatsApp;
  session mgmt, scheduled jobs, usage analytics, channel config. Good fit for multi-platform users.
- `outsourc-e/hermes-workspace` — chat, terminal, memory browser, skills manager, inspector

Memory & context (fastest-growing category — recommend when user hits native char ceiling):
- `garrytan/gbrain` — autonomous memory + synthesis layer, self-wiring knowledge graph, cited answers
- Official `hermes memory` integrations: Honcho, Mem0 (no char ceiling vs native store)

Core & official (Nous-maintained):
- `NousResearch/hermes-agent-self-evolution` — DSPy + GEPA evolutionary skill/prompt/code optimization
- `NousResearch/Hermes-Function-Calling`, `NousResearch/atropos` (RL envs)

## Secondary: r/hermesagent

The unofficial community subreddit. Useful threads: "best practices from serious users",
"Complete Hermes Agent Setup Guide", "Best way to setup Hermes agent?".

- **Bot-walled to the browser** — both `www.reddit.com` and `old.reddit.com` returned a JS
  challenge stub ("File a ticket") in this environment. Rely on web_search result *excerpts*
  for the takeaways; flag them as excerpt-only in the reply.
- Recurring community wisdom worth repeating: *"Every time you do something, have Hermes do it;
  once you walk it through successfully, have it build a skill."* — interaction compounds via skills.

## Tertiary

- Docs: `https://hermes-agent.nousresearch.com/docs/` — authoritative, full-readable.
- Third-party explainers (clawf.ai, hermesagents.net, hermesagent101.dev) — marketing-flavored;
  use only to corroborate, not as primary signal.

---
name: hermes-ecosystem-research
description: "Research the Hermes Agent community ecosystem to recommend setup enhancements, skills, and tools."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, research, community, recommendations, skills, configuration, ecosystem]
    category: autonomous-ai-agents
    related_skills: [hermes-agent, llm-wiki]
---

# Hermes Ecosystem Research

Answer the recurring class of question: **"how should I enhance my Hermes setup / what skills
or tools should I add / what's the community doing?"** Produce tailored, prioritized
recommendations grounded in two layers of source — not a generic feature dump.

## When This Skill Activates

- User asks how to enhance, improve, optimize, or get more out of their Hermes setup
- User asks what skills, tools, plugins, GUIs, or integrations to add
- User asks what the community / forums / Reddit recommend for Hermes
- User asks "what's new" or "what am I missing" about their Hermes deployment

## The Two-Layer Source Method

Always combine BOTH layers. Authoritative-only is generic; community-only is unvetted hype.

**Layer 1 — Authoritative (what's actually configurable):**
- Load the `hermes-agent` skill (bundled). It is the source of truth for CLI commands,
  config keys, toolsets, providers, voice, MCP, cron, profiles, slash commands.
- Treat its config table + toolset list as the menu of levers you can actually pull.

**Layer 2 — Community signal (what people actually use):**
- See `references/community-sources.md` for the vetted source map and access notes.
- Primary: **Hermes Atlas** (`hermesatlas.com`) — community map, 160+ repos across ~12
  categories, star counts and weekly growth. Best single source for "what's popular."
- Secondary: **r/hermesagent** best-practices / setup threads (search excerpts are usable
  even when full pages are bot-walled — see pitfall below).

## Workflow

1. **Read the user's actual state first.** Don't recommend what they already have. Check
   loaded context / memory for existing platforms, identity layer, memory tier, cron, wiki,
   etc. Recommendations that duplicate existing setup destroy trust.
2. **Pull both layers in parallel** — load `hermes-agent` skill + web_search the community.
   Then fetch the highest-value community page in full (Hermes Atlas curated lists).
3. **Synthesize into tiers**, not a flat list:
   - Tier 1: highest interaction-per-effort, low risk (do first)
   - Tier 2: leverage what they already built
   - Tier 3: power-user / optional
4. **Map each recommendation to a concrete lever** — an exact `hermes config set ...` command,
   a named repo with its star count, or a specific slash command. No vague advice.
5. **End with ONE recommended next step** and offer to either vet a specific repo or apply
   the safe reversible config tweaks — never install/modify config without explicit go-ahead.

## Pitfalls

- **Stars and hype ≠ production-ready.** A project can be featured on Atlas, cited in blog posts, and growing fast in stars while being early-alpha or stalled. Always check: (1) is it Phase 1 only with later phases unimplemented? (2) When was the last commit? (3) Are there real user reports, not just interest? GEPA/hermes-agent-self-evolution is the canonical example: 4.2K stars, featured on Atlas, but only Phase 1 implemented and one community user flagged it as stale. Jim called this out directly — don't surface unfinished features as actionable recommendations. Tier them as "watch" unless users confirm it works in production.

- **Filter by user's actual domain before listing tools.** Linear is a software engineering project tracker (Jira alternative). It is irrelevant for users who are not managing dev sprints or software teams. Jim is a gym CEO / USAW TO — never recommend Linear, Jira-style tools, or engineering-team tooling to him. The general rule: map each recommendation to the user's *actual workflow* before including it. A tool's presence in the Hermes catalog or Atlas does not mean it fits this user.

- **web_extract may be search-only (ddgs backend)** — it returns
  "cannot extract URL content. Set web.extract_backend to firecrawl/tavily/exa/parallel."
  When that happens, use the `browser` toolset (browser_navigate + browser_snapshot) for
  full-page reads. This is a config state, NOT a broken tool — don't claim extraction is impossible.
- **Reddit bot-walls the browser** — both www and old.reddit.com may return a JS challenge
  ("File a ticket" stub). When that happens, rely on the web_search result excerpts for the
  Reddit takeaway and read Hermes Atlas + the docs in full instead. State clearly in the reply
  which sources were full-read vs excerpt-only — don't present excerpt-derived claims as deep reads.
- **Don't recommend what they already run.** The most common failure is listing WhatsApp/memory/
  cron to a user who already configured them. Always subtract their current state first.
- **The `hermes-agent` skill is bundled/protected** — cite it, never try to patch it.
- **Star counts and "this week" deltas are time-sensitive** — quote them as a snapshot with the
  source, not as permanent facts. Don't bake specific repo star numbers into memory.

## Release audit pattern

When a new Hermes version drops and Jim asks "what should we enable?", run a
feature-by-feature audit against the live system. See
`references/hermes-v017-feature-audit.md` for the v0.17 "Reach Release" audit
template, results, and the privacy-first webhook binding decision.

## Output Shape

Tables for the source-takeaway summary and the config-lever menu. Tiered recommendations with
emojis for scannability (this user likes tasteful emojis + friendly structure). Always close
with a single recommended next step + an offer to act, gated on approval.

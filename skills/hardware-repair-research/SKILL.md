---
name: hardware-repair-research
description: Research consumer-hardware complaints, normal-vs-defect behavior, failure
  modes, replacement parts, compatibility, repair options, and next-step guidance
  for electronics/peripherals.
version: 1.0.0
created_by: agent
platforms:
- linux
- macos
- windows
author: Fortified Strength
license: MIT
metadata:
  hermes:
    config:
    - key: hardware-repair-research.enabled
      description: Enable hardware-repair-research skill behavior
      default: true
      prompt: Enable hardware-repair-research skill?
    tags:
    - hardware
    category: productivity
---
---

# Hardware Repair Research

Use this skill when the user reports a consumer hardware problem, asks whether a device behavior is common/normal or defective, wants replacement parts, or provides a device model/part number. This includes keyboards, mice, headphones, laptops, batteries, chargers, docks, smart-home devices, and similar peripherals.

This is the umbrella for **consumer-product troubleshooting research** and **hardware repair/parts research**. Keep one class-level workflow here rather than creating separate one-session skills for “is this common?”, “is this normal by design?”, “find a replacement battery”, or “is this worth repairing?” cases.

## Core workflow

1. **Clarify only when necessary.** If the user provides a model number, URL, error symptom, photo, or vague pointer, investigate it directly rather than asking what they want done.
2. **Establish the authoritative baseline.** Find manufacturer product pages, support articles, manuals, technical specifications, warning thresholds, environmental disclaimers, and feature-dependent limits.
3. **Separate normal behavior from failure.** Compare the symptom pattern to the baseline and label it as normal, borderline, likely defective, or likely consumable aging.
4. **Triangulate prevalence without overstating it.** Search support communities, Reddit/forums, retailer reviews, repair notes, and issue trackers for repeated patterns; treat isolated anecdotes as weak evidence.
5. **Identify exact compatibility anchors.** Capture model numbers, part numbers, voltage, capacity, dimensions, connector type, polarity, revision-specific differences, and whether the item is a kit or bare part.
6. **Research failure mode and repair path.** Look for official docs, community reports, teardown/repair notes, and reputable repair sources.
7. **Find replacement options.** Prefer listings or suppliers that explicitly match the model/part number. Note price, rating/review caveats, included tools, warranty, and compatibility caveats.
8. **Assess repair difficulty and safety.** Mention opening difficulty, adhesives/clips/screws, battery swelling/fire risk, and whether the repair is worth it versus replacement.
9. **Be proactive.** After answering the direct request, infer the next useful low-risk step: find an install guide, tool list, compatibility warning, diagnostic test, or post-repair test plan. Ask before purchases or irreversible actions.

## Normal-vs-defect research pattern

Use this subsection for “is this common?”, “is this normal?”, “should I RMA?”, or “why did this suddenly start?” questions.

- Lead with a one-sentence verdict.
- Convert marketing claims into practical thresholds such as “normal with feature X enabled”, “borderline”, or “likely abnormal”.
- Include the manufacturer baseline and the user-facing threshold.
- Name evidence quality: official spec, support doc, repeated community reports, repair-source pattern, or weak anecdote.
- If behavior changed after years of normal use, explicitly consider component aging instead of over-anchoring on settings or generic complaints.
- Give one clean diagnostic test: disable one feature, switch connection mode, update firmware, test another host/room/charger, or run one full charge cycle.
- End with a decision rule: keep monitoring, change settings, replace a consumable part, contact support/RMA, or stop using due to safety risk.

## Battery-specific checks

- Confirm chemistry and nominal voltage before recommending anything.
- Match **part number first**, then model number, then physical dimensions/capacity.
- Capacity can vary modestly, but voltage, connector polarity, dimensions, and fit matter.
- Warn the user to stop using/charging a swollen, hot, smelly, or visibly bulging lithium battery.
- A sudden loss of runtime after years is often more consistent with lithium battery degradation than settings/software, especially if it works while plugged in or drains with power-hungry features disabled.

## Response shape

Keep the response practical and purchase-safe:

- **Likely diagnosis / verdict:** one sentence; say whether it looks normal-by-design, borderline, defective, or consumable aging.
- **Evidence:** official baseline plus any community/repair/report triangulation, with evidence quality labeled.
- **Compatibility target:** model/part specs to verify before recommending parts.
- **Options:** short ranked list with links if available.
- **Caveats:** safety/fit/review/warranty concerns.
- **Recommended next step:** one concise diagnostic, purchase-safe, or repair-prep action.

## Pitfalls

- Do not assume all variants in a product family share the same battery or internal part.
- Do not recommend a purchase based only on product-family name; require a model/part-number match.
- Do not stop at “this is common”; connect it to the user’s timeline and symptom pattern.
- Do not overstate prevalence unless there is aggregate evidence or repeated independent reports.
- Search engines may return irrelevant results for short product names; add vendor/model terms and behavior terms.
- Manufacturer battery-life/runtime claims often vary by feature state, environment, firmware/app state, and connection mode.
- Low-battery warnings are not the same as imminent failure; many devices continue in reduced-feature mode for a long time.
- Do not over-index on transient tool/search failures. Retry via another source or query shape.

## References

- `references/logitech-mx-keyboard-batteries.md` — condensed notes from the Logitech MX keyboard battery replacement research session.
- `references/logitech-mx-keyboard-battery.md` — original normal-vs-defect notes for Logitech MX keyboard battery/backlight behavior.
- `references/logitech-mx-keyboard-replacement-batteries.md` — original replacement-part compatibility notes and Amazon examples for Logitech MX keyboard batteries.
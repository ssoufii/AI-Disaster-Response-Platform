# Issue #43 — Template-only alert content in simulation mode

**Epic:** Live simulation demo · **Points:** 2 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/43

## Holistic objective

A demo that dispatches every two minutes, forever, cannot call a paid API per household. The pre-written templates already exist as the Claude-failure fallback and are correct by construction, so the demo uses them on purpose.

## Technical objective

- `content_generator.generate` returns `template_for(severity, language)` first thing when `SIMULATION_MODE` is on; no Anthropic client is requested.
- Logged at info as `content_generation.template_simulation`, so it never reads as a generation failure.

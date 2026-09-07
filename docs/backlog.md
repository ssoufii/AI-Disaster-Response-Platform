# Backlog

Generated from `CLAUDE.md` build order and `docs/architecture.md`. Repo currently contains no
backend/frontend code — everything below starts from an empty `/backend` and `/frontend`.

Points are Fibonacci (1/2/3/5/8). Issue numbers below are the real GitHub issue numbers
(this repo's issue numbering happened to land 1:1 with the backlog order).

Total: 20 issues (at the ~20 cap). Epic 8 is intentionally coarsened per the size constraint —
flagged below for decomposition into 2-3 stories once the earlier epics are proven out.

Labels: `epic`, `story`, `spike`, `chore`, `area:backend`, `area:frontend`, `area:claude-api`,
`area:twilio`, `priority:high|medium|low`. Milestones: one per epic (1-8, in build order).

## Epic 1 — Foundation & data layer (milestone 1)

1. **[Dispatcher registers households within a zone](../../issues/1)** — 8pts — deps: none
2. **[Dispatcher drafts an alert for a zone](../../issues/2)** — 3pts — deps: #1
3. **[Seed script populates demo households across accessibility/channel profiles](../../issues/3)** — 3pts — deps: #1

## Epic 2 — Claude content generation (milestone 2)

4. **[Generate personalized alert content for one household](../../issues/4)** — 5pts — deps: #1, #2
5. **[Malformed Claude output falls back to a template](../../issues/5)** — 3pts — deps: #4
6. **[Generate content for an entire zone with bounded concurrency](../../issues/6)** — 5pts — deps: #4, #5

## Epic 3 — SMS delivery path (end-to-end) (milestone 3)

7. **[Dispatch an alert via SMS and track delivery status via webhook](../../issues/7)** — 5pts — deps: #4, #2
8. **[Reject Twilio webhooks with invalid signatures](../../issues/8)** — 2pts — deps: #7
9. **[Twilio status webhook is idempotent under duplicate callbacks](../../issues/9)** — 3pts — deps: #7

## Epic 4 — Real-time delivery monitoring (WebSockets + console) (milestone 4)

10. **[Dispatcher watches SMS delivery status update live on the console](../../issues/10)** — 5pts — deps: #7
11. **[Console shows a reconnecting banner and recovers after a dropped WebSocket](../../issues/11)** — 3pts — deps: #10

## Epic 5 — Automatic fallback rerouting (milestone 5)

12. **[SMS failure automatically reroutes to the household's fallback channel](../../issues/12)** — 5pts — deps: #7, #9
13. **[Household marked unreached after exhausting all fallback channels](../../issues/13)** — 3pts — deps: #12
14. **[Console shows rerouting explained inline, not just a status flip](../../issues/14)** — 2pts — deps: #12, #10

## Epic 6 — Voice channel & confirmation (milestone 6)

15. **[SPIKE: Decide two-way IVR confirmation approach for v1](../../issues/15)** — 2pts — deps: none
16. **[Dispatch an alert via voice call with TwiML playback](../../issues/16)** — 5pts — deps: #4, #12, #15
17. **[Voice call captures DTMF confirmation as `confirmed_received`](../../issues/17)** — 3pts — deps: #16, #15

## Epic 7 — ASL / video channel (milestone 7)

18. **[SPIKE: Decide ASL delivery mechanism for v1](../../issues/18)** — 2pts — deps: none
19. **[Dispatch an ASL alert as a captioned video clip via WhatsApp/MMS](../../issues/19)** — 5pts — deps: #18, #4, #12

## Epic 8 — Hardening (auth, rate limiting, observability) (milestone 8)

20. **[Harden the platform: console auth, Twilio/Claude rate limiting & backoff, phone-number log redaction audit](../../issues/20)** — 8pts — deps: #10, #16, #19
    - ⚠️ Coarsened to hit the 20-issue cap. Before starting, split into: (a) console auth, (b)
      Twilio/Claude retry & rate-limit polish, (c) structured logging + redaction audit.

---

## Notes on choices made

- **Epic 1** treats "models + migrations" as infrastructure folded into the first two stories
  that need them (households/zones in #1, alerts in #2) rather than a standalone
  non-demoable story, per the vertical-slice rule.
- **Spikes** (#15, #18) map to the two Open Decisions in `CLAUDE.md`. Their deliverable is a
  written decision appended to `docs/architecture.md`, not code. Downstream stories (#16, #17,
  #19) depend on them because the decision changes scope (e.g., whether #17 exists at all if
  the spike says delivery-only for v1 — keep it in the backlog as the "if yes" path and close
  it without implementing if the spike says no).
- **Failure paths are first-class stories**, not checklist items: #5 (malformed Claude JSON),
  #8 (bad webhook signature), #9 (duplicate webhook), #12/#13 (fallback chain + exhaustion),
  #11 (dropped WebSocket) — covering every failure mode called out in CLAUDE.md's Domain Rules
  and Testing sections.
- Each story's Definition of Done will include the CLAUDE.md-mandated bar: mocked
  Twilio/Anthropic clients in tests, Alembic migration in the same commit as any model change,
  `docs/architecture.md` updated when the delivery path or WS contract changes, lint/typecheck
  clean.

---

**Please review before I create anything on GitHub.** Once approved I will: create labels and
milestones (idempotent), create all 20 issues via `gh issue create --body-file`, then edit each
issue to swap title-based dependency notes for real `#<number>` links, and finish with a summary
table.

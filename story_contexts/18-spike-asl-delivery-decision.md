# Issue #18 — SPIKE: Decide ASL delivery mechanism for v1

**Epic:** ASL / video channel · **Points:** 2 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/18

## Holistic objective

The second of CLAUDE.md's two open decisions, and the one with the larger scope swing: a
pre-recorded interpreter clip library versus generating avatar video are barely the same project
in terms of effort. CLAUDE.md already states a default (clip library) precisely because generated
video is "dramatically" more scope — this spike exists to confirm that default deliberately, with
a written rationale, rather than let it be assumed and then discovered wrong mid-build of #19.

## Technical objective

- No code. A time-boxed (1 day) research spike.
- Deliverable: a `docs/architecture.md` **"Decision: ASL delivery"** note confirming clip-library
  vs. avatar-generation for v1, with rationale.
- Must also specify, at implementation-ready detail, how clips get *selected* — e.g. matched by
  severity plus a keyword/tag set derived from `asl_video_caption` — so #19 has a concrete
  mechanism to build against, not just a philosophical choice.
- Nothing merges as production code under this issue — only the decision document.

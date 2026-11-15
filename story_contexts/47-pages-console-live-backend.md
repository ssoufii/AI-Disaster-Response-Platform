# Issue #47 — Pages console connects to the live simulation backend

**Epic:** Live simulation demo · **Points:** 3 · **Depends on:** #46
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/47

## Holistic objective

The scripted playback proved the components worked; it did not prove the system did. The published page should be the actual console watching the actual backend.

## Technical objective

- `components/LiveConsole.tsx` asks `GET /alerts/latest`, loads that alert's snapshot, then mounts the ordinary `AlertConsole` (snapshot first, socket second). It keeps checking and moves to each new alert.
- A sleeping backend is shown as "Waking up the demo server", never a blank page.
- The scripted demo (`/demo`, `DemoConsole`, `demoScript`) is deleted. `pages.yml` builds with `NEXT_PUBLIC_LIVE_DEMO=1` and the `DEMO_API_URL` repository variable, and is skipped until that variable is set.

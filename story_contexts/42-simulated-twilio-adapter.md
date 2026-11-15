# Issue #42 — Simulated Twilio adapter behind delivery_service

**Epic:** Live simulation demo · **Points:** 5 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/42

## Holistic objective

The public demo has to show the thing this project is named for — a failed delivery rerouting to the next channel — without a phone bill and without any chance of contacting a real person. The only honest way to do that is to keep the whole delivery path real and replace just the outside world: Twilio.

## Technical objective

- `services/twilio_simulator.py` mirrors the slice of the Twilio client `delivery_service` uses (`messages.create_async`, `calls.create_async`).
- `delivery_service.get_client()` returns it whenever `SIMULATION_MODE` is on, checked on every call; the real `Client` is never built.
- Outcomes come back as signed POSTs to the real `/webhooks/twilio/status` and `/voice-confirmation`, in process (httpx `ASGITransport`). The signing token is a random per-process secret set in `config.py`.
- Numbers starting `+1500555` always fail; other failures are random at `SIM_*_RATE`. A keypress is the only source of `confirmed_received`.

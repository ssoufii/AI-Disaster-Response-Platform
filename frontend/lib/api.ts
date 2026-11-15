/**
 * Reads from the FastAPI backend.
 *
 * The snapshot is fetched before the socket is opened, never alongside it: the
 * grid has to be on screen by the time the console renders, or a dispatcher
 * watching a live incident is looking at an empty page while a socket connects
 * (CLAUDE.md, WebSocket Contract).
 *
 * Every read carries the dispatcher token, since #20 — the same token the socket
 * presents on its handshake. A read without one is refused by the API, so the
 * refusal is surfaced as its own error rather than as "cannot reach the service":
 * a dispatcher who needs to sign in and a backend that is down are two different
 * problems with two different next steps.
 */

import { authHeaders } from "@/lib/auth";
import { API_BASE_URL } from "@/lib/env";
import type { AlertDetails, AlertStatusSnapshot, LatestAlert } from "@/lib/types";

export class AlertNotFoundError extends Error {}

/** The token is missing, wrong, or no longer accepted. */
export class UnauthorizedError extends Error {}

/**
 * Every household's current delivery state for one alert.
 *
 * Never cached: a snapshot of a dispatch in progress is stale the moment it is
 * stored, and a cached one would put the console a refresh behind the incident.
 */
export async function fetchAlertStatus(
  alertId: string,
  token: string | null,
): Promise<AlertStatusSnapshot> {
  const response = await fetch(`${API_BASE_URL}/alerts/${alertId}/status`, {
    cache: "no-store",
    headers: authHeaders(token),
  });

  if (response.status === 401 || response.status === 403) {
    throw new UnauthorizedError("The dispatcher token was not accepted");
  }
  if (response.status === 404) {
    throw new AlertNotFoundError(`No alert ${alertId}`);
  }
  if (!response.ok) {
    throw new Error(`GET /alerts/${alertId}/status responded ${response.status}`);
  }

  return (await response.json()) as AlertStatusSnapshot;
}

/** The alert as the dispatcher wrote it. Console-only: needs the token. */
export async function fetchAlert(alertId: string, token: string | null): Promise<AlertDetails> {
  const response = await fetch(`${API_BASE_URL}/alerts/${alertId}`, {
    cache: "no-store",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(`GET /alerts/${alertId} responded ${response.status}`);
  }
  return (await response.json()) as AlertDetails;
}

/**
 * The most recently dispatched alert, or null when nothing has been dispatched.
 *
 * What the public demo console opens with: it has no alert id of its own, so it
 * asks which one is live. Throws when the backend cannot be reached, which the
 * demo shows as "waking up" rather than as an empty console.
 */
export async function fetchLatestAlert(): Promise<LatestAlert | null> {
  const response = await fetch(`${API_BASE_URL}/alerts/latest`, { cache: "no-store" });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`GET /alerts/latest responded ${response.status}`);
  }
  return (await response.json()) as LatestAlert;
}

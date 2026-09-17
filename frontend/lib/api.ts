/**
 * Reads from the FastAPI backend.
 *
 * The snapshot is fetched before the socket is opened, never alongside it: the
 * grid has to be on screen by the time the console renders, or a dispatcher
 * watching a live incident is looking at an empty page while a socket connects
 * (CLAUDE.md, WebSocket Contract).
 */

import { API_BASE_URL } from "@/lib/env";
import type { AlertStatusSnapshot } from "@/lib/types";

export class AlertNotFoundError extends Error {}

/**
 * Every household's current delivery state for one alert.
 *
 * Never cached: a snapshot of a dispatch in progress is stale the moment it is
 * stored, and a cached one would put the console a refresh behind the incident.
 */
export async function fetchAlertStatus(alertId: string): Promise<AlertStatusSnapshot> {
  const response = await fetch(`${API_BASE_URL}/alerts/${alertId}/status`, {
    cache: "no-store",
  });

  if (response.status === 404) {
    throw new AlertNotFoundError(`No alert ${alertId}`);
  }
  if (!response.ok) {
    throw new Error(`GET /alerts/${alertId}/status responded ${response.status}`);
  }

  return (await response.json()) as AlertStatusSnapshot;
}

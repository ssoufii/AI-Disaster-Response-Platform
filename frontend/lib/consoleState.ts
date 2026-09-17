/**
 * The console's live state: one row per household, keyed by `household_id`.
 *
 * Keyed rather than a list because of how updates arrive. A `delivery_update`
 * concerns exactly one household, and rebuilding the array on every event would
 * hand every row a new object and re-render the whole grid — on a zone dispatch
 * that is hundreds of rows re-rendering per Twilio callback, on a screen someone
 * is watching continuously. Patching `rows[household_id]` leaves every other row
 * object identical, so the memoised rows around it do not re-render at all.
 *
 * `order` holds the snapshot's household order separately, so a patch never
 * reshuffles the grid under the dispatcher's eyes.
 */

import type {
  AlertStatusSnapshot,
  Channel,
  DeliveryStatus,
  DeliveryUpdateEvent,
  HouseholdDeliveryStatus,
  HouseholdStatus,
} from "@/lib/types";

export interface HouseholdRow {
  household_id: string;
  name: string;
  preferred_channel: Channel;
  last_known_status: HouseholdStatus;
  /** The channel of the current attempt — the preferred one, or a fallback. */
  channel: Channel | null;
  /** Null when nothing has been attempted for this household yet. */
  status: DeliveryStatus | null;
  attempt_number: number | null;
  fallback_triggered: boolean;
  fallback_channel: Channel | null;
  /** When this row last changed, as reported by the event that changed it. */
  updated_at: string | null;
}

export interface ConsoleState {
  alertId: string;
  order: string[];
  rows: Record<string, HouseholdRow>;
}

export type ConsoleAction = { type: "delivery_update"; event: DeliveryUpdateEvent };

function rowFromSnapshot(household: HouseholdDeliveryStatus): HouseholdRow {
  const attempt = household.current_attempt;
  return {
    household_id: household.household_id,
    name: household.name,
    preferred_channel: household.preferred_channel,
    last_known_status: household.last_known_status,
    channel: attempt?.channel ?? null,
    status: attempt?.status ?? null,
    attempt_number: attempt?.attempt_number ?? null,
    // The snapshot reports where a household stands, not how it got there;
    // whether a fallback fired is something only the events carry.
    fallback_triggered: false,
    fallback_channel: null,
    updated_at: attempt?.completed_at ?? attempt?.started_at ?? null,
  };
}

export function initialConsoleState(snapshot: AlertStatusSnapshot): ConsoleState {
  const rows: Record<string, HouseholdRow> = {};
  for (const household of snapshot.households) {
    rows[household.household_id] = rowFromSnapshot(household);
  }
  return {
    alertId: snapshot.alert_id,
    order: snapshot.households.map((household) => household.household_id),
    rows,
  };
}

export function consoleReducer(state: ConsoleState, action: ConsoleAction): ConsoleState {
  const { event } = action;
  const current = state.rows[event.household_id];

  // A household the snapshot did not contain: it joined the zone after this
  // page loaded. Dropping the event keeps the grid consistent with the snapshot
  // it was built from; a reconnect resync (#11) is what picks the household up.
  if (current === undefined) {
    return state;
  }

  // Applied from the event alone, never merged with what the row happened to
  // hold — a console that connected mid-dispatch has seen none of the earlier
  // events and must still land on the same row as one that saw them all.
  const patched: HouseholdRow = {
    ...current,
    channel: event.channel,
    status: event.status,
    attempt_number: event.attempt_number,
    fallback_triggered: event.fallback_triggered,
    fallback_channel: event.fallback_channel,
    updated_at: event.timestamp,
  };

  return {
    ...state,
    rows: { ...state.rows, [event.household_id]: patched },
  };
}

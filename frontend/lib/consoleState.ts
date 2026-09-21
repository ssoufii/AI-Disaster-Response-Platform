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
  HouseholdUnreachedEvent,
} from "@/lib/types";

/**
 * One attempt in a household's history, as the console observed it.
 *
 * Kept as a list rather than a single current attempt because a fallback makes
 * a *new* DeliveryAttempt row and never mutates the failed one (Domain Rule 2):
 * an audit trail that flipped one entry in place would be a different claim
 * about what happened. Entries are keyed by `attempt_number` for the same
 * reason — that is what distinguishes the rows on the backend.
 */
export interface AttemptHistoryEntry {
  attempt_number: number;
  channel: Channel;
  status: DeliveryStatus;
  /** The channel this attempt was rerouted to, when it failed and one existed. */
  fallback_channel: Channel | null;
  updated_at: string | null;
}

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
  /** Every attempt this console has seen for the household, oldest first. */
  attempts: AttemptHistoryEntry[];
}

export interface ConsoleState {
  alertId: string;
  order: string[];
  rows: Record<string, HouseholdRow>;
}

export type ConsoleAction =
  | { type: "delivery_update"; event: DeliveryUpdateEvent }
  | { type: "household_unreached"; event: HouseholdUnreachedEvent }
  /** A reconnect re-read `GET /alerts/{id}/status`; see `consoleReducer`. */
  | { type: "snapshot_resync"; snapshot: AlertStatusSnapshot };

/**
 * Add an attempt to a history, or update the one it supersedes.
 *
 * Matched on `attempt_number`: an event about attempt 1 going from `queued` to
 * `failed` is the same DeliveryAttempt reporting a later state, while attempt 2
 * is a row that did not exist before. Sorted rather than appended because a
 * console that connected mid-dispatch may learn of attempt 2 before the
 * resync tells it about attempt 1.
 */
function withAttempt(
  attempts: AttemptHistoryEntry[],
  entry: AttemptHistoryEntry,
): AttemptHistoryEntry[] {
  const others = attempts.filter((a) => a.attempt_number !== entry.attempt_number);
  return [...others, entry].sort((a, b) => a.attempt_number - b.attempt_number);
}

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
    // The snapshot reports one attempt per household, so on a cold load the
    // timeline starts there and fills in as events arrive. A resync keeps the
    // attempts this console already saw (see `consoleReducer`) rather than
    // throwing the audit trail away.
    attempts:
      attempt === null || attempt === undefined
        ? []
        : [
            {
              attempt_number: attempt.attempt_number,
              channel: attempt.channel,
              status: attempt.status,
              fallback_channel: null,
              updated_at: attempt.completed_at ?? attempt.started_at,
            },
          ],
  };
}

export function initialConsoleState(
  snapshot: AlertStatusSnapshot,
  previous?: ConsoleState,
): ConsoleState {
  const rows: Record<string, HouseholdRow> = {};
  for (const household of snapshot.households) {
    const row = rowFromSnapshot(household);
    const seen = previous?.rows[household.household_id]?.attempts ?? [];
    // An attempt already observed is a fact about the past, not stale state:
    // it is a committed DeliveryAttempt row that nothing will rewrite. So the
    // snapshot decides where the household stands *now* and the attempts the
    // console saw before an outage stay in its timeline.
    rows[household.household_id] = {
      ...row,
      attempts: row.attempts.reduce(withAttempt, seen),
    };
  }
  return {
    alertId: snapshot.alert_id,
    order: snapshot.households.map((household) => household.household_id),
    rows,
  };
}

/** One row replaced, every other row object left identical — see the header. */
function withRow(state: ConsoleState, row: HouseholdRow): ConsoleState {
  return {
    ...state,
    rows: { ...state.rows, [row.household_id]: row },
  };
}

export function consoleReducer(state: ConsoleState, action: ConsoleAction): ConsoleState {
  // After an outage the server's snapshot is the only honest account of where
  // every household stands: the events that arrived while the socket was down
  // are gone and nothing replays them. So it replaces the rows wholesale rather
  // than merging into them — including households that joined the zone while
  // this page was loaded, which is how a dropped `delivery_update` below is
  // eventually made good.
  if (action.type === "snapshot_resync") {
    return initialConsoleState(action.snapshot, state);
  }

  const current = state.rows[action.event.household_id];

  // A household the snapshot did not contain: it joined the zone after this
  // page loaded. Dropping the event keeps the grid consistent with the snapshot
  // it was built from; a reconnect resync is what picks the household up.
  if (current === undefined) {
    return state;
  }

  // The end of the chain: nothing else will be tried, so the row stops being
  // about an attempt and becomes about the household — red, and waiting on a
  // person (Domain Rule 4). The attempt that failed keeps its own state and its
  // place in the timeline; this event says only that nothing follows it.
  if (action.type === "household_unreached") {
    const { event } = action;
    return withRow(state, {
      ...current,
      last_known_status: event.last_known_status,
      updated_at: event.timestamp,
    });
  }

  // Applied from the event alone, never merged with what the row happened to
  // hold — a console that connected mid-dispatch has seen none of the earlier
  // events and must still land on the same row as one that saw them all. The
  // timeline is the one thing that accumulates, because an earlier attempt is
  // a separate row the event does not claim to replace.
  const { event } = action;
  return withRow(state, {
    ...current,
    channel: event.channel,
    status: event.status,
    attempt_number: event.attempt_number,
    fallback_triggered: event.fallback_triggered,
    fallback_channel: event.fallback_channel,
    updated_at: event.timestamp,
    attempts: withAttempt(current.attempts, {
      attempt_number: event.attempt_number,
      channel: event.channel,
      status: event.status,
      fallback_channel: event.fallback_channel,
      updated_at: event.timestamp,
    }),
  });
}

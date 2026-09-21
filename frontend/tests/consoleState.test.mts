/**
 * What the console makes of a reroute.
 *
 * The reducer and the narrative are where "SMS failed → retrying via Voice"
 * actually comes from — the components only print what these return — so they
 * are what is worth testing without a DOM. Run with the Node test runner
 * (`npm test` in `frontend/`); no framework is installed and none is needed.
 * Nothing here is anywhere near Twilio or Anthropic: these are pure functions
 * over event payloads.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { consoleReducer, initialConsoleState, type ConsoleState } from "../lib/consoleState.ts";
import { rerouteNarrative } from "../lib/rerouteNarrative.ts";
import { statusTone } from "../lib/statusTone.ts";
import { isDeliveryUpdateEvent, isHouseholdUnreachedEvent } from "../lib/types.ts";
import type {
  AlertStatusSnapshot,
  Channel,
  DeliveryStatus,
  DeliveryUpdateEvent,
  HouseholdUnreachedEvent,
} from "../lib/types.ts";

const HOUSEHOLD = "household-1";

function snapshot(): AlertStatusSnapshot {
  return {
    alert_id: "alert-1",
    status: "dispatching",
    households: [
      {
        household_id: HOUSEHOLD,
        name: "Rivera household",
        preferred_channel: "sms",
        last_known_status: "unknown",
        current_attempt: null,
      },
    ],
  };
}

function deliveryUpdate(overrides: Partial<DeliveryUpdateEvent> = {}): DeliveryUpdateEvent {
  return {
    type: "delivery_update",
    alert_id: "alert-1",
    household_id: HOUSEHOLD,
    channel: "sms",
    status: "queued",
    attempt_number: 1,
    fallback_triggered: false,
    fallback_channel: null,
    timestamp: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function unreached(overrides: Partial<HouseholdUnreachedEvent> = {}): HouseholdUnreachedEvent {
  return {
    type: "household_unreached",
    alert_id: "alert-1",
    household_id: HOUSEHOLD,
    last_channel: "voice",
    attempts_made: 2,
    last_known_status: "unreached",
    timestamp: "2026-01-01T00:02:00Z",
    ...overrides,
  };
}

function apply(state: ConsoleState, event: DeliveryUpdateEvent | HouseholdUnreachedEvent) {
  return event.type === "household_unreached"
    ? consoleReducer(state, { type: "household_unreached", event })
    : consoleReducer(state, { type: "delivery_update", event });
}

function rowAfter(events: (DeliveryUpdateEvent | HouseholdUnreachedEvent)[]) {
  const state = events.reduce(apply, initialConsoleState(snapshot()));
  return state.rows[HOUSEHOLD];
}

test("a failed attempt annotated with a fallback reads as a reroute, in amber", () => {
  const row = rowAfter([
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
  ]);

  assert.equal(rerouteNarrative(row), "SMS failed → retrying via Voice");
  assert.equal(statusTone(row), "amber");
});

test("the reroute names the channels the event carried, whatever they are", () => {
  const row = rowAfter([
    deliveryUpdate({
      channel: "voice" as Channel,
      status: "no_answer" as DeliveryStatus,
      fallback_triggered: true,
      fallback_channel: "video" as Channel,
    }),
  ]);

  assert.equal(rerouteNarrative(row), "Voice failed → retrying via Video / ASL");
});

test("a reroute event applies standalone, without the attempt that preceded it", () => {
  // The console that connected mid-dispatch, which never saw attempt 1 queued.
  const cold = rowAfter([
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
  ]);
  const warm = rowAfter([
    deliveryUpdate({ status: "queued" }),
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
  ]);

  assert.equal(rerouteNarrative(cold), rerouteNarrative(warm));
  assert.equal(statusTone(cold), statusTone(warm));
});

test("a delivered household says nothing extra and stays green", () => {
  const row = rowAfter([deliveryUpdate({ status: "delivered" })]);

  assert.equal(rerouteNarrative(row), null);
  assert.equal(statusTone(row), "green");
});

test("an unreached household turns red and asks for a person", () => {
  const row = rowAfter([
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
    deliveryUpdate({ channel: "voice", status: "failed", attempt_number: 2 }),
    unreached(),
  ]);

  assert.equal(statusTone(row), "red");
  assert.equal(rerouteNarrative(row), "All channels tried after 2 attempts — needs human follow-up");
  assert.equal(row.last_known_status, "unreached");
});

test("the household_unreached event leaves the failed attempt's own state alone", () => {
  const row = rowAfter([
    deliveryUpdate({ channel: "voice", status: "failed", attempt_number: 2 }),
    unreached(),
  ]);

  // Domain Rule 2: the attempt is a committed row, not something an event about
  // the household rewrites.
  assert.equal(row.status, "failed");
  assert.equal(row.channel, "voice");
  assert.equal(row.attempts.length, 1);
  assert.equal(row.attempts[0].status, "failed");
});

test("every attempt keeps its own timeline entry, in order", () => {
  const row = rowAfter([
    deliveryUpdate({ status: "queued" }),
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
    deliveryUpdate({ channel: "voice", status: "queued", attempt_number: 2 }),
    deliveryUpdate({ channel: "voice", status: "delivered", attempt_number: 2 }),
  ]);

  assert.deepEqual(
    row.attempts.map((attempt) => [attempt.attempt_number, attempt.channel, attempt.status]),
    [
      [1, "sms", "failed"],
      [2, "voice", "delivered"],
    ],
  );
  assert.equal(row.attempts[0].fallback_channel, "voice");
});

test("a later attempt seen first still sorts behind the earlier one", () => {
  const row = rowAfter([
    deliveryUpdate({ channel: "voice", status: "queued", attempt_number: 2 }),
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
  ]);

  assert.deepEqual(
    row.attempts.map((attempt) => attempt.attempt_number),
    [1, 2],
  );
});

test("a resync re-reads the household's state without losing its history", () => {
  const live = [
    deliveryUpdate({ status: "failed", fallback_triggered: true, fallback_channel: "voice" }),
    deliveryUpdate({ channel: "voice", status: "queued", attempt_number: 2 }),
  ].reduce(apply, initialConsoleState(snapshot()));

  const resynced = consoleReducer(live, {
    type: "snapshot_resync",
    snapshot: {
      ...snapshot(),
      households: [
        {
          household_id: HOUSEHOLD,
          name: "Rivera household",
          preferred_channel: "sms",
          last_known_status: "unreached",
          current_attempt: {
            id: "attempt-2",
            channel: "voice",
            attempt_number: 2,
            status: "failed",
            twilio_sid: "SM2",
            started_at: "2026-01-01T00:01:00Z",
            completed_at: "2026-01-01T00:01:30Z",
            error_reason: "no-answer",
          },
        },
      ],
    },
  });

  const row = resynced.rows[HOUSEHOLD];
  // The snapshot is what the household *is* now; the attempts the console
  // already watched happen are facts it does not have to forget.
  assert.equal(statusTone(row), "red");
  assert.deepEqual(
    row.attempts.map((attempt) => [attempt.attempt_number, attempt.status]),
    [
      [1, "failed"],
      [2, "failed"],
    ],
  );
});

test("the socket recognises both event types, and nothing else", () => {
  // What `useAlertSocket` forwards to the reducer. An unknown type is ignored
  // rather than mishandled, so the contract can grow without this build
  // breaking on a frame it predates.
  assert.equal(isDeliveryUpdateEvent(deliveryUpdate()), true);
  assert.equal(isHouseholdUnreachedEvent(unreached()), true);
  assert.equal(isHouseholdUnreachedEvent(deliveryUpdate()), false);
  assert.equal(isDeliveryUpdateEvent(unreached()), false);
  assert.equal(isDeliveryUpdateEvent({ type: "dispatch_complete", alert_id: "alert-1" }), false);
  assert.equal(isHouseholdUnreachedEvent({ type: "dispatch_complete" }), false);
  assert.equal(isHouseholdUnreachedEvent(null), false);
});

test("an event for a household the snapshot never had is still dropped", () => {
  const state = apply(
    initialConsoleState(snapshot()),
    unreached({ household_id: "household-unknown" }),
  );

  assert.deepEqual(Object.keys(state.rows), [HOUSEHOLD]);
  assert.equal(state.rows[HOUSEHOLD].last_known_status, "unknown");
});

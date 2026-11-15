/**
 * The figures at the top of the console and each household's channel path.
 *
 * Both are derived from the reducer's rows, so they are tested as pure
 * functions: the totals must add up to the zone, and the path must never claim
 * a channel the console did not see.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { consoleReducer, initialConsoleState, type ConsoleState } from "../lib/consoleState.ts";
import { rerouteNarrative } from "../lib/rerouteNarrative.ts";
import { statusTone } from "../lib/statusTone.ts";
import { chainSteps, finding, tally } from "../lib/tally.ts";
import type {
  AlertStatusSnapshot,
  Channel,
  DeliveryStatus,
  HouseholdDeliveryStatus,
} from "../lib/types.ts";

function household(
  id: string,
  attempt: { channel: Channel; status: DeliveryStatus; attempt_number?: number } | null,
  last_known_status: "unknown" | "unreached" = "unknown",
): HouseholdDeliveryStatus {
  return {
    household_id: id,
    name: `${id} household`,
    preferred_channel: "sms",
    last_known_status,
    current_attempt:
      attempt === null
        ? null
        : {
            id: `${id}-attempt`,
            channel: attempt.channel,
            attempt_number: attempt.attempt_number ?? 1,
            status: attempt.status,
            twilio_sid: null,
            started_at: "2026-01-01T00:00:00Z",
            completed_at: null,
            error_reason: null,
          },
  };
}

function stateOf(households: HouseholdDeliveryStatus[]): ConsoleState {
  const snapshot: AlertStatusSnapshot = { alert_id: "alert-1", status: "dispatching", households };
  return initialConsoleState(snapshot);
}

test("the four figures add up to the zone", () => {
  const totals = tally(
    stateOf([
      household("a", { channel: "sms", status: "delivered" }),
      household("b", { channel: "voice", status: "confirmed_received" }),
      household("c", { channel: "sms", status: "failed" }),
      household("d", { channel: "whatsapp", status: "failed", attempt_number: 3 }, "unreached"),
      household("e", { channel: "sms", status: "sending" }),
      household("f", null),
    ]),
  );

  assert.equal(totals.total, 6);
  assert.equal(totals.reached, 2);
  assert.equal(totals.confirmed, 1);
  assert.equal(totals.rerouting, 1);
  assert.equal(totals.unreached, 1);
  assert.equal(totals.inFlight, 2);
  assert.equal(totals.reached + totals.rerouting + totals.unreached + totals.inFlight, totals.total);
  assert.deepEqual(totals.tones, ["green", "green", "amber", "red", "grey", "grey"]);
});

test("delivered is not counted as confirmed", () => {
  const totals = tally(stateOf([household("a", { channel: "sms", status: "delivered" })]));

  assert.equal(totals.reached, 1);
  assert.equal(totals.confirmed, 0);
});

test("the finding leads with a household that needs a person", () => {
  const unreached = tally(
    stateOf([
      household("a", { channel: "sms", status: "delivered" }),
      household("b", { channel: "voice", status: "failed", attempt_number: 2 }, "unreached"),
    ]),
  );
  const done = tally(stateOf([household("a", { channel: "sms", status: "delivered" })]));
  const rerouting = tally(
    stateOf([
      household("a", { channel: "sms", status: "delivered" }),
      household("b", { channel: "sms", status: "failed" }),
    ]),
  );

  assert.equal(
    finding(unreached),
    "b household could not be reached on any channel and needs a person to follow up.",
  );
  assert.equal(finding(done), "Every household has been reached.");
  assert.match(finding(rerouting), /1 of 2 reached so far; 1 being retried/);
});

test("the channel path lists every attempt the console watched", () => {
  let state = stateOf([household("a", { channel: "sms", status: "queued" })]);
  const event = {
    type: "delivery_update" as const,
    alert_id: "alert-1",
    household_id: "a",
    timestamp: "2026-01-01T00:00:05Z",
  };
  state = consoleReducer(state, {
    type: "delivery_update",
    event: {
      ...event,
      channel: "sms",
      status: "failed",
      attempt_number: 1,
      fallback_triggered: true,
      fallback_channel: "voice",
    },
  });
  state = consoleReducer(state, {
    type: "delivery_update",
    event: {
      ...event,
      channel: "voice",
      status: "delivered",
      attempt_number: 2,
      fallback_triggered: false,
      fallback_channel: null,
    },
  });

  assert.deepEqual(chainSteps(state.rows.a), [
    { channel: "sms", outcome: "failed" },
    { channel: "voice", outcome: "done" },
  ]);
});

test("a console that loaded mid-dispatch fills in only what is certain", () => {
  // Attempt 3 is all the snapshot reports. Attempt 1 was on the preferred
  // channel and both earlier attempts failed; which channel attempt 2 used was
  // never seen, so it is a gap, not a guess.
  const state = stateOf([household("a", { channel: "whatsapp", status: "sending", attempt_number: 3 })]);

  assert.deepEqual(chainSteps(state.rows.a), [
    { channel: "sms", outcome: "failed" },
    { channel: null, outcome: "failed" },
    { channel: "whatsapp", outcome: "active" },
  ]);
});

test("a household with no attempt has no path", () => {
  assert.deepEqual(chainSteps(stateOf([household("a", null)]).rows.a), []);
});

test("a console that opens after the fact shows the whole path from the snapshot", () => {
  const row = household("a", { channel: "voice", status: "delivered", attempt_number: 2 });
  const first = { ...row.current_attempt!, id: "a-1", channel: "sms" as const, attempt_number: 1, status: "failed" as const };
  const state = stateOf([{ ...row, attempts: [first, row.current_attempt!] }]);

  assert.deepEqual(chainSteps(state.rows.a), [
    { channel: "sms", outcome: "failed" },
    { channel: "voice", outcome: "done" },
  ]);
  assert.equal(state.rows.a.attempts[0].fallback_channel, "voice");
});

test("a retry in flight stays amber and says what it is retrying", () => {
  const state = stateOf([household("a", { channel: "voice", status: "sending", attempt_number: 2 })]);

  assert.equal(statusTone(state.rows.a), "amber");
  assert.equal(rerouteNarrative(state.rows.a), "SMS failed → retrying via Voice");
  assert.equal(tally(state).rerouting, 1);
});

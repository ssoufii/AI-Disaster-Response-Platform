/**
 * The demo script, played through the real reducer.
 *
 * The demo is only worth publishing if it lands where it claims to: every
 * household ends in the state the page describes, and the script never sends
 * an event the live backend could not have sent.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { consoleReducer, initialConsoleState, type ConsoleState } from "../lib/consoleState.ts";
import { demoSnapshot, demoTimeline } from "../lib/demoScript.ts";
import { rerouteNarrative } from "../lib/rerouteNarrative.ts";
import { statusTone } from "../lib/statusTone.ts";

function playAll(): ConsoleState {
  let state = initialConsoleState(demoSnapshot());
  for (const step of demoTimeline(new Date("2026-01-01T00:00:00Z"))) {
    if (step.kind !== "event") continue;
    state =
      step.event.type === "household_unreached"
        ? consoleReducer(state, { type: "household_unreached", event: step.event })
        : consoleReducer(state, { type: "delivery_update", event: step.event });
  }
  return state;
}

test("the timeline is in playback order", () => {
  const times = demoTimeline(new Date()).map((step) => step.atMs);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
});

test("every event is for a household in the snapshot", () => {
  const ids = new Set(demoSnapshot().households.map((h) => h.household_id));
  for (const step of demoTimeline(new Date())) {
    if (step.kind === "event") assert.ok(ids.has(step.event.household_id));
  }
});

test("the connection drops and comes back", () => {
  const states = demoTimeline(new Date())
    .filter((step) => step.kind === "connection")
    .map((step) => step.state);
  assert.deepEqual(states, ["reconnecting", "live"]);
});

test("each household ends where the demo page says it does", () => {
  const { rows } = playAll();

  assert.equal(rows["hh-rivera"].status, "delivered");
  assert.equal(rows["hh-garcia"].status, "confirmed_received");

  // Rescued by its fallback: a second attempt on voice, the first kept intact.
  assert.equal(rows["hh-patel"].status, "confirmed_received");
  assert.deepEqual(
    rows["hh-patel"].attempts.map((a) => [a.channel, a.status]),
    [
      ["sms", "failed"],
      ["voice", "confirmed_received"],
    ],
  );

  // Every channel exhausted: red, and asking for a person.
  const failing = rows["hh-test"];
  assert.equal(failing.last_known_status, "unreached");
  assert.equal(failing.attempts.length, 3);
  assert.equal(statusTone(failing), "red");
  assert.match(rerouteNarrative(failing) ?? "", /needs human follow-up/);
});

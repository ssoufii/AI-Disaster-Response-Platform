/**
 * The two things the console derives from its rows: the totals at the top, and
 * each household's channel path.
 *
 * Pure functions over the reducer's state, so they are testable without a DOM
 * and the components only print what these return.
 */

import type { ConsoleState, HouseholdRow } from "./consoleState.ts";
import { statusTone, type StatusTone } from "./statusTone.ts";
import type { Channel } from "./types.ts";

export interface Tally {
  total: number;
  /** Delivered or confirmed: the warning got there. */
  reached: number;
  /** The subset of `reached` where a person pressed 1 (Domain Rule 6). */
  confirmed: number;
  /** Failed on one channel and still being tried on the next. */
  rerouting: number;
  /** Every channel exhausted: needs a person (Domain Rule 4). */
  unreached: number;
  /** Queued, sending, or not attempted yet. */
  inFlight: number;
  /** One tone per household, in grid order — what the reach strip draws. */
  tones: StatusTone[];
  /** Who needs a person, by name, so nobody has to scroll the grid to find out. */
  unreachedNames: string[];
}

export function tally(state: ConsoleState): Tally {
  const result: Tally = {
    total: state.order.length,
    reached: 0,
    confirmed: 0,
    rerouting: 0,
    unreached: 0,
    inFlight: 0,
    tones: [],
    unreachedNames: [],
  };

  for (const id of state.order) {
    const row = state.rows[id];
    const tone = statusTone(row);
    result.tones.push(tone);
    if (tone === "green") {
      result.reached += 1;
      if (row.status === "confirmed_received") {
        result.confirmed += 1;
      }
    } else if (tone === "amber") {
      result.rerouting += 1;
    } else if (tone === "red") {
      result.unreached += 1;
      result.unreachedNames.push(row.name);
    } else {
      result.inFlight += 1;
    }
  }

  return result;
}

function listOf(names: string[]): string {
  if (names.length <= 1) {
    return names[0] ?? "";
  }
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The headline sentence under the alert's title: the one thing to know now. */
export function finding(t: Tally): string {
  if (t.total === 0) {
    return "No households are registered in this alert's zone.";
  }
  if (t.unreached > 0) {
    // Named, because this sentence is the dispatcher's to-do list. Past three
    // names it would stop being a sentence, so the rest are counted.
    const named = t.unreachedNames.slice(0, 3);
    const more = t.unreached - named.length;
    const who = more > 0 ? `${named.join(", ")} and ${more} more` : listOf(named);
    return `${who} could not be reached on any channel and ${
      t.unreached === 1 ? "needs" : "need"
    } a person to follow up.`;
  }
  if (t.reached === t.total) {
    return "Every household has been reached.";
  }
  if (t.rerouting > 0) {
    return `${t.reached} of ${t.total} reached so far; ${t.rerouting} being retried on another channel.`;
  }
  return `${t.reached} of ${t.total} reached so far; the rest are on their way.`;
}

export type ChainOutcome = "failed" | "done" | "active";

export interface ChainStep {
  /** Null for an attempt this console knows happened but never saw. */
  channel: Channel | null;
  outcome: ChainOutcome;
}

const FAILED = new Set(["failed", "no_answer"]);
const DONE = new Set(["delivered", "confirmed_received"]);

/**
 * A household's path through its channels: "SMS → Voice → WhatsApp".
 *
 * Built from the attempts the console has seen. A console that loaded
 * mid-dispatch only knows the current attempt, but two things are still certain
 * and are filled in: attempt 1 was on the preferred channel, and every attempt
 * before the current one failed — a later attempt only exists because the one
 * before it did (Domain Rule 2). An attempt in between that was never seen is a
 * step with no channel, shown as a gap rather than guessed.
 */
export function chainSteps(row: HouseholdRow): ChainStep[] {
  const last = row.attempt_number ?? 0;
  const steps: ChainStep[] = [];

  for (let number = 1; number <= last; number += 1) {
    const seen = row.attempts.find((attempt) => attempt.attempt_number === number);
    if (number < last) {
      steps.push({
        channel: seen?.channel ?? (number === 1 ? row.preferred_channel : null),
        outcome: "failed",
      });
      continue;
    }
    const status = seen?.status ?? row.status;
    steps.push({
      channel: seen?.channel ?? row.channel,
      outcome:
        status !== null && FAILED.has(status)
          ? "failed"
          : status !== null && DONE.has(status)
            ? "done"
            : "active",
    });
  }

  return steps;
}

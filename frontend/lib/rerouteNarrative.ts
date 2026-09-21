/**
 * Says what happened to a household in words, not just in colour.
 *
 * A row going from grey to amber tells a dispatcher that something changed; it
 * does not tell them what, or whether the system is still working on it. Under
 * the time pressure of a live incident nobody should have to decode a palette,
 * so the reroute is stated outright — "SMS failed → retrying via Voice" —
 * exactly as CLAUDE.md's Frontend Conventions require.
 *
 * Both sentences are built from the event payload rather than from a lookup of
 * what the console remembers: `fallback_triggered`/`fallback_channel` annotate
 * the attempt that *failed* and name where it is going next, and a
 * `household_unreached` event says the chain ran out. A console that connected
 * halfway through a dispatch renders the same words as one that watched the
 * whole thing.
 */

import type { AttemptHistoryEntry, HouseholdRow } from "@/lib/consoleState";
// Relative and extension-qualified, unlike the `@/` imports elsewhere: this is
// a value import, and `frontend/tests/` runs these modules on the Node test
// runner, which resolves neither the alias nor an extensionless path.
import { channelLabel, deliveryStatusLabel } from "./statusTone.ts";

/**
 * The plain-language line under a household's status, or null when there is
 * nothing to explain — a delivery that simply worked speaks for itself.
 */
export function rerouteNarrative(row: HouseholdRow): string | null {
  if (row.last_known_status === "unreached") {
    // Deliberately the loudest sentence on the screen: this household gets no
    // further attempt from the system, and only a person can close it out.
    const tried = row.attempts.length > 0 ? row.attempts.length : (row.attempt_number ?? 0);
    const chain = tried > 0 ? ` after ${tried} attempt${tried === 1 ? "" : "s"}` : "";
    return `All channels tried${chain} — needs human follow-up`;
  }

  if (row.fallback_triggered && row.fallback_channel !== null && row.channel !== null) {
    return `${channelLabel(row.channel)} failed → retrying via ${channelLabel(row.fallback_channel)}`;
  }

  // Failed, and no fallback named: either the chain has nowhere left to go and
  // the `household_unreached` event is still in flight, or this build saw the
  // failure without the annotation. Saying so beats an unexplained amber row.
  if (row.fallback_triggered || row.status === "failed" || row.status === "no_answer") {
    return `${channelLabel(row.channel ?? row.preferred_channel)} failed`;
  }

  return null;
}

/** One line in the timeline: what was tried, on what channel, and how it ended. */
export function attemptNarrative(entry: AttemptHistoryEntry): string {
  const outcome = deliveryStatusLabel(entry.status);
  const reroute =
    entry.fallback_channel === null ? "" : ` → retried via ${channelLabel(entry.fallback_channel)}`;
  return `Attempt ${entry.attempt_number} · ${channelLabel(entry.channel)} · ${outcome}${reroute}`;
}

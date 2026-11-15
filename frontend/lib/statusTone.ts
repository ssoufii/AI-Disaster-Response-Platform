/**
 * What colour a household's row is, and why.
 *
 * The four tones are fixed by CLAUDE.md and mean specific things, so they are
 * decided here once rather than at each call site:
 *
 * - **green** — delivered, or confirmed by a human. Nothing to do.
 * - **amber** — this channel failed and the household is being retried on the
 *   next one. Still in hand.
 * - **red** — every channel is exhausted. A person has to pick this up.
 * - **grey** — in flight, or not attempted yet. Nothing has happened.
 *
 * Red is read from the household's own status rather than from the attempt,
 * because "unreached" is a statement about the household after its whole
 * fallback chain ran out (#13), not about any single attempt.
 */

import type { HouseholdRow } from "./consoleState.ts";
import type { DeliveryStatus } from "./types.ts";

export type StatusTone = "green" | "amber" | "red" | "grey";

const FAILED_STATUSES = new Set(["failed", "no_answer"]);

export function statusTone(row: HouseholdRow): StatusTone {
  if (row.last_known_status === "unreached") {
    return "red";
  }
  if (row.status === "delivered" || row.status === "confirmed_received") {
    return "green";
  }
  // Amber is "failed but still in hand", which is what a fallback annotation
  // means whatever the failing status was called — the backend can add terminal
  // statuses to the chain without this going grey on a household being retried.
  if (row.fallback_triggered) {
    return "amber";
  }
  if (row.status !== null && FAILED_STATUSES.has(row.status)) {
    return "amber";
  }
  // On a second or later channel and not there yet: an earlier channel failed
  // and this is the retry. That is "failed but rerouting" for as long as the
  // retry is in flight, not only for the instant between the two attempts.
  if (row.attempt_number !== null && row.attempt_number > 1) {
    return "amber";
  }
  return "grey";
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  sending: "Sending",
  delivered: "Delivered",
  failed: "Failed",
  no_answer: "No answer",
  confirmed_received: "Confirmed received",
};

export function deliveryStatusLabel(status: DeliveryStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function statusLabel(row: HouseholdRow): string {
  if (row.last_known_status === "unreached") {
    return "Unreached";
  }
  if (row.status === null) {
    return "Not attempted";
  }
  return deliveryStatusLabel(row.status);
}

const CHANNEL_LABELS: Record<string, string> = {
  sms: "SMS",
  voice: "Voice",
  video: "Video / ASL",
  whatsapp: "WhatsApp",
};

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel;
}

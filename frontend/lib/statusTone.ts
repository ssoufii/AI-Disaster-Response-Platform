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

import type { HouseholdRow } from "@/lib/consoleState";
import type { DeliveryStatus } from "@/lib/types";

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
  return "grey";
}

export const TONE_CLASSES: Record<StatusTone, string> = {
  green: "border-emerald-300 bg-emerald-50 text-emerald-900",
  amber: "border-amber-300 bg-amber-50 text-amber-900",
  red: "border-red-300 bg-red-50 text-red-900",
  grey: "border-slate-300 bg-slate-100 text-slate-700",
};

export const TONE_DOT_CLASSES: Record<StatusTone, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  grey: "bg-slate-400",
};

/**
 * The tint on the row itself, for the two tones that ask for attention.
 *
 * Only amber and red carry one: a grid where every row is coloured is a grid
 * where nothing stands out, and the rows that need a dispatcher's eye are the
 * one being retried and the one waiting on a person.
 */
export const TONE_ROW_CLASSES: Record<StatusTone, string> = {
  green: "",
  amber: "bg-amber-50/60",
  red: "bg-red-50",
  grey: "",
};

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
    return "Unreached — needs follow-up";
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

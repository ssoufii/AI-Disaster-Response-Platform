/**
 * One household's attempt history, in the order it happened.
 *
 * Every entry is a distinct DeliveryAttempt row (Domain Rule 2) — a fallback
 * never rewrites the attempt it is replacing — so the timeline lists them
 * rather than showing one status flipping in place. That is the difference
 * between "this household is on voice" and "SMS failed at 14:02, voice queued
 * at 14:02", which is the account a dispatcher needs when deciding whether a
 * household has been given up on.
 *
 * Rendered inside a native `<details>` by the grid: hundreds of expanded
 * timelines would bury the live rows, and a disclosure needs no client state.
 */

import type { AttemptHistoryEntry } from "@/lib/consoleState";
import { attemptNarrative } from "@/lib/rerouteNarrative";
import { TONE_DOT_CLASSES, type StatusTone } from "@/lib/statusTone";

function attemptTone(entry: AttemptHistoryEntry): StatusTone {
  if (entry.status === "delivered" || entry.status === "confirmed_received") {
    return "green";
  }
  if (entry.status === "failed" || entry.status === "no_answer") {
    return "amber";
  }
  return "grey";
}

function formatTime(timestamp: string | null): string {
  if (timestamp === null) {
    return "—";
  }
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleTimeString();
}

export function DeliveryTimeline({ attempts }: { attempts: AttemptHistoryEntry[] }) {
  if (attempts.length === 0) {
    return <p className="text-sm text-slate-500">No attempt has been made yet.</p>;
  }

  return (
    <ol className="space-y-1 text-sm text-slate-700">
      {attempts.map((entry) => (
        <li key={entry.attempt_number} className="flex items-baseline gap-2">
          <span
            className={`h-2 w-2 shrink-0 translate-y-[-1px] rounded-full ${TONE_DOT_CLASSES[attemptTone(entry)]}`}
            aria-hidden
          />
          <span>{attemptNarrative(entry)}</span>
          <span className="text-slate-500 tabular-nums">{formatTime(entry.updated_at)}</span>
        </li>
      ))}
    </ol>
  );
}

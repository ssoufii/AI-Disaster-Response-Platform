/**
 * The live grid: one row per household, household × channel × status.
 *
 * Each row is memoised on its own row object. Because the reducer patches a
 * single entry of `rows` and leaves the others identical, an incoming update
 * re-renders exactly the household it concerns and nothing else.
 */

import { memo } from "react";

import { DeliveryTimeline } from "@/components/DeliveryTimeline";
import type { ConsoleState, HouseholdRow } from "@/lib/consoleState";
import { rerouteNarrative } from "@/lib/rerouteNarrative";
import {
  TONE_CLASSES,
  TONE_DOT_CLASSES,
  TONE_ROW_CLASSES,
  channelLabel,
  statusLabel,
  statusTone,
} from "@/lib/statusTone";

function formatTime(timestamp: string | null): string {
  if (timestamp === null) {
    return "—";
  }
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleTimeString();
}

const HouseholdStatusRow = memo(function HouseholdStatusRow({ row }: { row: HouseholdRow }) {
  const tone = statusTone(row);
  const narrative = rerouteNarrative(row);
  const attempts = row.attempts.length;

  return (
    <tr className={`border-b border-slate-200 last:border-b-0 ${TONE_ROW_CLASSES[tone]}`}>
      <td className="px-4 py-3 align-top font-medium">{row.name}</td>
      <td className="px-4 py-3 align-top text-slate-600">
        {channelLabel(row.channel ?? row.preferred_channel)}
        {row.channel === null && <span className="text-slate-400"> (preferred)</span>}
      </td>
      <td className="px-4 py-3 align-top">
        <span
          className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${TONE_CLASSES[tone]}`}
        >
          <span className={`h-2 w-2 rounded-full ${TONE_DOT_CLASSES[tone]}`} aria-hidden />
          {statusLabel(row)}
        </span>
        {/* The reroute in words, right under the badge it explains: a colour
            change alone leaves a dispatcher decoding a palette mid-incident. */}
        {narrative !== null && (
          <p
            className={`mt-1.5 text-sm ${tone === "red" ? "font-medium text-red-800" : "text-slate-600"}`}
          >
            {narrative}
          </p>
        )}
      </td>
      <td className="px-4 py-3 align-top text-slate-600 tabular-nums">
        {row.attempt_number ?? "—"}
      </td>
      <td className="px-4 py-3 align-top text-slate-600 tabular-nums">
        {formatTime(row.updated_at)}
      </td>
      <td className="px-4 py-3 align-top">
        {attempts === 0 ? (
          <span className="text-slate-400">—</span>
        ) : (
          <details>
            <summary className="cursor-pointer text-sm text-slate-600">
              {attempts} attempt{attempts === 1 ? "" : "s"}
            </summary>
            <div className="mt-2">
              <DeliveryTimeline attempts={row.attempts} />
            </div>
          </details>
        )}
      </td>
    </tr>
  );
});

export function HouseholdStatusGrid({ state }: { state: ConsoleState }) {
  if (state.order.length === 0) {
    return (
      <p className="rounded-lg border border-slate-300 bg-white px-4 py-6 text-slate-600">
        No households are registered in this alert&apos;s zone.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-slate-300 bg-white">
      <table className="w-full border-collapse text-left">
        <thead className="border-b border-slate-300 bg-slate-50 text-sm uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="px-4 py-3 font-medium">
              Household
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Channel
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Status
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Attempt
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Updated
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              History
            </th>
          </tr>
        </thead>
        <tbody>
          {state.order.map((householdId) => (
            <HouseholdStatusRow key={householdId} row={state.rows[householdId]} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

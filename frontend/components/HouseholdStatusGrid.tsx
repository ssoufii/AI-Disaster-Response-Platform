/**
 * The live grid: one row per household, household × channel × status.
 *
 * Each row is memoised on its own row object. Because the reducer patches a
 * single entry of `rows` and leaves the others identical, an incoming update
 * re-renders exactly the household it concerns and nothing else.
 */

import { memo } from "react";

import type { ConsoleState, HouseholdRow } from "@/lib/consoleState";
import {
  TONE_CLASSES,
  TONE_DOT_CLASSES,
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

  return (
    <tr className="border-b border-slate-200 last:border-b-0">
      <td className="px-4 py-3 font-medium">{row.name}</td>
      <td className="px-4 py-3 text-slate-600">
        {channelLabel(row.channel ?? row.preferred_channel)}
        {row.channel === null && <span className="text-slate-400"> (preferred)</span>}
      </td>
      <td className="px-4 py-3">
        <span
          className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${TONE_CLASSES[tone]}`}
        >
          <span className={`h-2 w-2 rounded-full ${TONE_DOT_CLASSES[tone]}`} aria-hidden />
          {statusLabel(row)}
        </span>
      </td>
      <td className="px-4 py-3 text-slate-600 tabular-nums">{row.attempt_number ?? "—"}</td>
      <td className="px-4 py-3 text-slate-600 tabular-nums">{formatTime(row.updated_at)}</td>
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

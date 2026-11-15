/**
 * The live grid: one row per household — who, by which channels, and how it
 * stands.
 *
 * Each row is memoised on its own row object. Because the reducer patches a
 * single entry of `rows` and leaves the others identical, an incoming update
 * re-renders exactly the household it concerns and nothing else.
 *
 * On a phone a row becomes two lines (who and how it stands; then the path and
 * the time) instead of a table squeezed until its columns collide.
 */

import { memo } from "react";

import type { ConsoleState, HouseholdRow } from "@/lib/consoleState";
import { rerouteNarrative } from "@/lib/rerouteNarrative";
import { channelLabel, statusLabel, statusTone } from "@/lib/statusTone";
import { chainSteps } from "@/lib/tally";

function formatTime(timestamp: string | null): string {
  if (timestamp === null) {
    return "—";
  }
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime())
    ? "—"
    : parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

/**
 * The household's path through its channels, e.g. "SMS → Voice".
 *
 * Every attempt is its own row on the backend and a fallback never rewrites the
 * one it replaces (Domain Rule 2), so the path lists them all: a failed channel
 * stays visible, struck through, with the one that took over beside it.
 */
function ChannelPath({ row }: { row: HouseholdRow }) {
  const steps = chainSteps(row);
  if (steps.length === 0) {
    return (
      <span className="text-muted">{channelLabel(row.preferred_channel)} (not attempted)</span>
    );
  }

  return (
    <span className="inline-flex items-center gap-x-1.5 whitespace-nowrap max-sm:flex-wrap">
      {steps.map((step, index) => (
        <span key={index} className="inline-flex items-center gap-x-1.5">
          {index > 0 && (
            <span className="text-muted" aria-hidden>
              →
            </span>
          )}
          {step.outcome === "failed" ? (
            <s className="text-muted">
              {step.channel === null ? "…" : channelLabel(step.channel)}
              <span className="sr-only"> failed, then</span>
            </s>
          ) : (
            <span className="font-medium">
              {step.channel === null ? "…" : channelLabel(step.channel)}
            </span>
          )}
        </span>
      ))}
    </span>
  );
}

const HouseholdStatusRow = memo(function HouseholdStatusRow({ row }: { row: HouseholdRow }) {
  const tone = statusTone(row);
  const narrative = rerouteNarrative(row);

  return (
    <tr className="border-t border-rule max-sm:grid max-sm:grid-cols-[1fr_auto] max-sm:gap-x-3 max-sm:gap-y-1 max-sm:px-4 max-sm:py-3">
      <td className="py-3 pr-4 pl-5 align-top font-medium sm:whitespace-nowrap max-sm:p-0">{row.name}</td>
      <td className="px-4 py-3 align-top max-sm:order-3 max-sm:p-0 max-sm:text-[13px]">
        <ChannelPath row={row} />
      </td>
      <td className="px-4 py-3 align-top max-sm:order-2 max-sm:p-0 max-sm:text-right">
        <span className="pill" data-tone={tone}>
          <span className="tone-dot" aria-hidden />
          {statusLabel(row)}
        </span>
        {/* The reroute in words, right under the badge it explains: a colour
            change alone leaves a dispatcher decoding a palette mid-incident. */}
        {narrative !== null && (
          <p
            key={narrative}
            className={`narrative mt-1 text-[13px] max-sm:hidden ${
              tone === "red" ? "font-medium text-tone-red" : "text-muted"
            }`}
          >
            {narrative}
          </p>
        )}
      </td>
      <td className="figures py-3 pr-5 pl-4 text-right align-top whitespace-nowrap text-muted max-sm:order-4 max-sm:p-0 max-sm:text-[13px]">
        {formatTime(row.updated_at)}
      </td>
      {narrative !== null && (
        <td
          className={`order-5 col-span-2 hidden p-0 text-[13px] max-sm:block ${
            tone === "red" ? "font-medium text-tone-red" : "text-muted"
          }`}
        >
          {narrative}
        </td>
      )}
    </tr>
  );
});

export function HouseholdStatusGrid({ state }: { state: ConsoleState }) {
  if (state.order.length === 0) {
    return (
      <p className="rounded-lg border border-line bg-surface px-5 py-6 text-muted">
        No households are registered in this alert&apos;s zone, so there is nobody to send it to.
      </p>
    );
  }

  return (
    <section
      aria-label="Households"
      className="overflow-hidden rounded-lg border border-line bg-surface"
    >
      <table className="w-full border-collapse text-left max-sm:block">
        <thead className="text-[13px] text-muted max-sm:hidden">
          <tr>
            <th scope="col" className="py-2.5 pr-4 pl-5 font-normal">
              Household
            </th>
            <th scope="col" className="px-4 py-2.5 font-normal whitespace-nowrap">
              Channel path
            </th>
            <th scope="col" className="px-4 py-2.5 font-normal">
              Status
            </th>
            <th scope="col" className="py-2.5 pr-5 pl-4 text-right font-normal">
              Updated
            </th>
          </tr>
        </thead>
        <tbody className="max-sm:block max-sm:[&>tr:first-child]:border-t-0">
          {state.order.map((householdId) => (
            <HouseholdStatusRow key={householdId} row={state.rows[householdId]} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

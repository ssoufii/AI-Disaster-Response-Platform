/**
 * The answer to the screen's one question — has this alert reached everyone,
 * and who needs a person — as four figures and one strip.
 *
 * One divided card rather than four tiles: these are parts of one total, and
 * they add up to it. Figures are ink; a zero is grey; the only coloured figure
 * is the one that asks for a decision.
 *
 * The strip is this product's own picture of a dispatch: one cell per household
 * in the zone, in grid order, each in its status tone. It fills green as the
 * warning lands and shows at a glance where it has not.
 */

import type { Tally } from "@/lib/tally";
import type { StatusTone } from "@/lib/statusTone";

function Figure({
  tone,
  label,
  value,
  of,
  note,
  alarm = false,
}: {
  tone: StatusTone;
  label: string;
  value: number;
  of?: number;
  note: string;
  alarm?: boolean;
}) {
  return (
    <div className="px-5 py-4">
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <span className="tone-dot" data-tone={tone} aria-hidden />
        {label}
      </p>
      <p className="figures mt-1 text-[30px] leading-9 font-medium">
        <span
          className={value === 0 ? "text-muted" : alarm ? "text-tone-red" : undefined}
        >
          {value}
        </span>
        {of !== undefined && <span className="text-base font-normal text-muted"> of {of}</span>}
      </p>
      <p className="mt-0.5 text-[13px] text-muted">{note}</p>
    </div>
  );
}

export function ReachSummary({ tally }: { tally: Tally }) {
  return (
    <section
      aria-label="Delivery summary"
      className="overflow-hidden rounded-lg border border-line bg-surface"
    >
      <div className="grid grid-cols-2 divide-rule max-lg:[&>*:nth-child(-n+2)]:border-b max-lg:[&>*:nth-child(odd)]:border-r lg:grid-cols-4 lg:divide-x [&>*]:border-rule">
        <Figure
          tone="green"
          label="Reached"
          value={tally.reached}
          of={tally.total}
          note={
            tally.confirmed === 0
              ? "None confirmed yet"
              : `${tally.confirmed} confirmed by keypress`
          }
        />
        <Figure
          tone="grey"
          label="In flight"
          value={tally.inFlight}
          note="Queued or sending"
        />
        <Figure
          tone="amber"
          label="Rerouting"
          value={tally.rerouting}
          note="Trying the next channel"
        />
        <Figure
          tone="red"
          label="Unreached"
          value={tally.unreached}
          note="Needs a person"
          alarm
        />
      </div>
      {tally.total > 0 && (
        <div className="flex gap-[3px] border-t border-rule px-5 py-4" aria-hidden>
          {tally.tones.map((tone, index) => (
            <span key={index} className="strip-cell" data-tone={tone} />
          ))}
        </div>
      )}
    </section>
  );
}

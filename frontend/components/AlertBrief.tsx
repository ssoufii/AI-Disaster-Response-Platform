/**
 * What the dispatcher sent: the message and the structured facts beside it.
 *
 * Shown next to the deliveries because it is what every household's version is
 * rewritten *from*. The facts are the dispatcher's own; nothing downstream may
 * add to them (Domain Rule 1), which is why they are printed here as entered.
 */

import type { AlertDetails } from "@/lib/types";

function factLabel(key: string): string {
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function factValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(String).join(", ");
  }
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function AlertBrief({ alert }: { alert: AlertDetails }) {
  const facts = Object.entries(alert.facts);

  return (
    <section className="rounded-lg border border-line bg-surface px-5 py-4">
      <h2 className="text-base font-medium">Dispatcher&apos;s alert</h2>
      <p className="mt-2">{alert.raw_message}</p>
      {facts.length > 0 && (
        <dl className="mt-3 space-y-2 border-t border-rule pt-3">
          {facts.map(([key, value]) => (
            <div key={key}>
              <dt className="text-[13px] text-muted">{factLabel(key)}</dt>
              <dd>{factValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

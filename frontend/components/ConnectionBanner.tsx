/**
 * Says out loud that the grid below has stopped updating.
 *
 * The one thing this console must never do is look fine while it is stale, so
 * the banner is tied to `reconnecting` and nothing else: it appears the moment
 * the socket drops and stays up through every failed retry, clearing only once
 * the connection is back *and* resynced. It also names what is and is not
 * affected — delivery keeps running on the backend without this page.
 */

import type { ConnectionState } from "@/lib/alertSocketController";

export function ConnectionBanner({ state }: { state: ConnectionState }) {
  if (state !== "reconnecting") {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="mb-4 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900"
    >
      <span className="mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-500" aria-hidden />
      <p className="text-sm">
        <span className="font-medium">Connection lost — reconnecting.</span> Statuses below may be
        out of date. Delivery is unaffected; this console is.
      </p>
    </div>
  );
}

"use client";

/**
 * The console for one alert: its title, the answer, and every household.
 *
 * It is handed the snapshot its caller already fetched and seeds its reducer
 * from it, so the grid it renders on its first paint is complete — the socket
 * then only patches it. Nothing here waits on the connection to show something.
 *
 * It also owns the two halves of an outage: the banner while the socket is
 * down, and reseeding the reducer from the resynced snapshot once it is back.
 *
 * `token` is null on the public simulation demo, whose read routes and socket
 * are open; a dispatcher's console passes the desk token (#20).
 */

import { useCallback, useEffect, useReducer, useState, type ReactNode } from "react";

import { AlertBrief } from "@/components/AlertBrief";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { HouseholdStatusGrid } from "@/components/HouseholdStatusGrid";
import { ReachSummary } from "@/components/ReachSummary";
import { useAlertSocket } from "@/hooks/useAlertSocket";
import type { ConnectionState } from "@/lib/alertSocketController";
import { consoleReducer, initialConsoleState } from "@/lib/consoleState";
import { finding, tally } from "@/lib/tally";
import type {
  AlertDetails,
  AlertSocketEvent,
  AlertStatusSnapshot,
  Severity,
} from "@/lib/types";

const SEVERITY_LABELS: Record<Severity, string> = {
  advisory: "Advisory",
  warning: "Warning",
  evacuate_now: "Evacuate now",
};

function formatClock(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** Whether the grid is current, said in two words beside the title. */
function LiveState({ connection }: { connection: ConnectionState }) {
  const live = connection === "live";
  return (
    <span className="flex items-center gap-2 text-muted">
      <span
        className={`tone-dot ${live ? "live-dot" : ""}`}
        data-tone={live ? "green" : connection === "reconnecting" ? "amber" : "grey"}
        aria-hidden
      />
      {live ? "Live" : connection === "reconnecting" ? "Reconnecting" : "Connecting"}
    </span>
  );
}

/** "Next mock alert in 1:12" — only the simulation demo knows when that is. */
function NextDispatch({ at }: { at: string }) {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);

  if (now === null) {
    return null;
  }
  const seconds = Math.round((new Date(at).getTime() - now) / 1000);
  if (Number.isNaN(seconds)) {
    return null;
  }
  if (seconds <= 0) {
    return <span className="text-muted">Next mock alert starting soon</span>;
  }
  return (
    <span className="figures text-muted">
      Next mock alert in {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
    </span>
  );
}

export function AlertConsole({
  snapshot,
  token,
  alert = null,
  zoneName = null,
  nextDispatchAt = null,
  aside = null,
}: {
  snapshot: AlertStatusSnapshot;
  token: string | null;
  alert?: AlertDetails | null;
  zoneName?: string | null;
  nextDispatchAt?: string | null;
  /** Extra panels for the right-hand column, under the dispatcher's alert. */
  aside?: ReactNode;
}) {
  const [state, dispatch] = useReducer(consoleReducer, snapshot, initialConsoleState);

  const onEvent = useCallback((event: AlertSocketEvent) => {
    // The event's own `type` is the action's: both are applied to exactly one
    // household's row, and the reducer decides what each means for it.
    if (event.type === "household_unreached") {
      dispatch({ type: "household_unreached", event });
      return;
    }
    dispatch({ type: "delivery_update", event });
  }, []);

  const onResync = useCallback((resynced: AlertStatusSnapshot) => {
    dispatch({ type: "snapshot_resync", snapshot: resynced });
  }, []);

  const connection = useAlertSocket(snapshot.alert_id, token, onEvent, onResync);
  const totals = tally(state);
  const households = `${totals.total} household${totals.total === 1 ? "" : "s"}`;
  const context = [
    alert && SEVERITY_LABELS[alert.severity],
    zoneName,
    households,
    alert && `dispatched ${formatClock(alert.created_at)}`,
  ].filter(Boolean);

  return (
    <main className="mx-auto max-w-[1152px] px-4 pt-6 pb-16 sm:px-6">
      <header className="mb-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h1 className="text-2xl leading-8 font-semibold tracking-[-0.01em]">
            {alert?.title ?? "Alert delivery"}
          </h1>
          <div className="flex items-center gap-4">
            {nextDispatchAt !== null && <NextDispatch at={nextDispatchAt} />}
            <LiveState connection={connection} />
          </div>
        </div>
        <p className="mt-1 text-muted">{context.join(" · ")}</p>
        {/* The one thing to know right now, in a sentence, before any figure. */}
        <p
          className={`mt-3 text-base ${totals.unreached > 0 ? "font-medium" : ""}`}
          aria-live="polite"
        >
          {finding(totals)}
        </p>
      </header>

      <ConnectionBanner state={connection} />

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid min-w-0 gap-3">
          <ReachSummary tally={totals} />
          <HouseholdStatusGrid state={state} />
        </div>
        {(alert !== null || aside !== null) && (
          <aside className="grid content-start gap-3 self-start lg:sticky lg:top-4">
            {alert !== null && <AlertBrief alert={alert} />}
            {aside}
          </aside>
        )}
      </div>
    </main>
  );
}

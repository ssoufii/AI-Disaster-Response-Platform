/**
 * The dispatcher console for one alert.
 *
 * A server component, and that is the whole point: the snapshot is fetched and
 * the grid rendered before anything reaches the browser, so the socket opens
 * underneath a console that is already showing every household. A dispatcher
 * watching a live incident never sees a blank screen while a connection is
 * negotiated (CLAUDE.md, WebSocket Contract).
 */

import { AlertConsole } from "@/components/AlertConsole";
import { AlertNotFoundError, fetchAlertStatus } from "@/lib/api";
import type { AlertStatusSnapshot } from "@/lib/types";

const ALERT_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  dispatching: "Dispatching",
  completed: "Completed",
};

function Panel({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-slate-600">{detail}</p>
    </main>
  );
}

export default async function AlertConsolePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let snapshot: AlertStatusSnapshot;
  try {
    snapshot = await fetchAlertStatus(id);
  } catch (error) {
    // Never a blank page, not even on the failure path: a dispatcher has to be
    // able to tell "nothing is happening" from "this console is not working".
    if (error instanceof AlertNotFoundError) {
      return <Panel title="Alert not found" detail={`No alert with id ${id}.`} />;
    }
    return (
      <Panel
        title="Cannot reach the dispatch service"
        detail={`The status of alert ${id} could not be loaded. Delivery is unaffected — this console is not.`}
      />
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6">
        <p className="text-sm uppercase tracking-wide text-slate-500">Dispatcher console</p>
        <h1 className="mt-1 text-2xl font-semibold">Alert {snapshot.alert_id}</h1>
        <p className="mt-1 text-slate-600">
          {ALERT_STATUS_LABELS[snapshot.status] ?? snapshot.status} ·{" "}
          {snapshot.households.length} household
          {snapshot.households.length === 1 ? "" : "s"} in zone
        </p>
      </header>

      <AlertConsole snapshot={snapshot} />
    </main>
  );
}

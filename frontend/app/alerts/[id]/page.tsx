/**
 * The dispatcher console for one alert.
 *
 * A server component, and that is the whole point: the snapshot is fetched and
 * the grid rendered before anything reaches the browser, so the socket opens
 * underneath a console that is already showing every household. A dispatcher
 * watching a live incident never sees a blank screen while a connection is
 * negotiated (CLAUDE.md, WebSocket Contract).
 *
 * It is also where the console's auth guard sits (#20). The token is read from
 * the request's own cookie, so an unauthenticated visitor is turned away here —
 * before any household's name or number is fetched, let alone rendered. The same
 * token is handed to the client half, which needs it for the socket handshake and
 * for the resync fetch it makes after an outage.
 */

import { cookies } from "next/headers";
import Link from "next/link";

import { AlertConsole } from "@/components/AlertConsole";
import { AlertNotFoundError, UnauthorizedError, fetchAlertStatus } from "@/lib/api";
import { DISPATCHER_TOKEN_COOKIE } from "@/lib/auth";
import type { AlertStatusSnapshot } from "@/lib/types";

const ALERT_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  dispatching: "Dispatching",
  completed: "Completed",
};

function Panel({
  title,
  detail,
  signInTo,
}: {
  title: string;
  detail: string;
  signInTo?: string;
}) {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-slate-600">{detail}</p>
      {signInTo ? (
        <Link
          className="mt-4 inline-block rounded bg-slate-900 px-4 py-2 text-white"
          href={`/sign-in?next=${encodeURIComponent(signInTo)}`}
        >
          Sign in
        </Link>
      ) : null}
    </main>
  );
}

export default async function AlertConsolePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const token = (await cookies()).get(DISPATCHER_TOKEN_COOKIE)?.value ?? null;

  if (!token) {
    // Turned away before the snapshot is even requested: there is no version of
    // this page an unauthenticated visitor should see part of.
    return (
      <Panel
        title="Sign in to watch this alert"
        detail="The console needs the dispatch desk's token before it can show a delivery in progress."
        signInTo={`/alerts/${id}`}
      />
    );
  }

  let snapshot: AlertStatusSnapshot;
  try {
    snapshot = await fetchAlertStatus(id, token);
  } catch (error) {
    // Never a blank page, not even on the failure path: a dispatcher has to be
    // able to tell "nothing is happening" from "this console is not working".
    if (error instanceof UnauthorizedError) {
      // A token that was present and refused — rotated, mistyped, or expired
      // with the browser session. Distinct from the service being unreachable,
      // because the dispatcher can fix this one in ten seconds.
      return (
        <Panel
          title="That token was not accepted"
          detail="The dispatch service refused this console's token. Sign in again with the desk's current one."
          signInTo={`/alerts/${id}`}
        />
      );
    }
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

      <AlertConsole snapshot={snapshot} token={token} />
    </main>
  );
}

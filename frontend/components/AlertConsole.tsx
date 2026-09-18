"use client";

/**
 * The live half of the console.
 *
 * It is handed the snapshot the page already fetched and seeds its reducer from
 * it, so the grid it renders on its first paint is the same one the server
 * rendered — the socket then only patches it. Nothing here waits on the
 * connection to show something.
 *
 * It also owns the two halves of an outage: the banner while the socket is
 * down, and reseeding the reducer from the resynced snapshot once it is back.
 */

import { useCallback, useReducer } from "react";

import { ConnectionBanner } from "@/components/ConnectionBanner";
import { HouseholdStatusGrid } from "@/components/HouseholdStatusGrid";
import { useAlertSocket } from "@/hooks/useAlertSocket";
import { consoleReducer, initialConsoleState } from "@/lib/consoleState";
import type { AlertSocketEvent, AlertStatusSnapshot } from "@/lib/types";

export function AlertConsole({ snapshot }: { snapshot: AlertStatusSnapshot }) {
  const [state, dispatch] = useReducer(consoleReducer, snapshot, initialConsoleState);

  const onEvent = useCallback((event: AlertSocketEvent) => {
    dispatch({ type: "delivery_update", event });
  }, []);

  const onResync = useCallback((resynced: AlertStatusSnapshot) => {
    dispatch({ type: "snapshot_resync", snapshot: resynced });
  }, []);

  const connection = useAlertSocket(snapshot.alert_id, onEvent, onResync);

  return (
    <>
      <ConnectionBanner state={connection} />
      <HouseholdStatusGrid state={state} />
    </>
  );
}

"use client";

/**
 * The live half of the console.
 *
 * It is handed the snapshot the page already fetched and seeds its reducer from
 * it, so the grid it renders on its first paint is the same one the server
 * rendered — the socket then only patches it. Nothing here waits on the
 * connection to show something.
 */

import { useCallback, useReducer } from "react";

import { HouseholdStatusGrid } from "@/components/HouseholdStatusGrid";
import { useAlertSocket } from "@/hooks/useAlertSocket";
import { consoleReducer, initialConsoleState } from "@/lib/consoleState";
import type { AlertSocketEvent, AlertStatusSnapshot } from "@/lib/types";

export function AlertConsole({ snapshot }: { snapshot: AlertStatusSnapshot }) {
  const [state, dispatch] = useReducer(consoleReducer, snapshot, initialConsoleState);

  const onEvent = useCallback((event: AlertSocketEvent) => {
    dispatch({ type: "delivery_update", event });
  }, []);

  useAlertSocket(snapshot.alert_id, onEvent);

  return <HouseholdStatusGrid state={state} />;
}

"use client";

/**
 * Subscribes the console to one alert's live delivery updates, and reports
 * whether that subscription is actually current.
 *
 * Opened only after the page has its snapshot, so the socket carries diffs onto
 * a grid that is already on screen.
 *
 * The connection lifecycle — drop, backoff, reconnect, resync — lives in
 * `lib/alertSocketController.ts`, deliberately outside React so it is testable
 * without a DOM. What stays here is React's share of it: handlers held in a ref
 * so a new inline callback on each render does not tear the socket down, frame
 * parsing, and the connection state the page renders its banner from.
 */

import { useEffect, useRef, useState } from "react";

import { fetchAlertStatus } from "@/lib/api";
import { connectAlertSocket, type ConnectionState } from "@/lib/alertSocketController";
import { WS_BASE_URL } from "@/lib/env";
import {
  isDeliveryUpdateEvent,
  type AlertSocketEvent,
  type AlertStatusSnapshot,
} from "@/lib/types";

export function useAlertSocket(
  alertId: string,
  onEvent: (event: AlertSocketEvent) => void,
  onResync: (snapshot: AlertStatusSnapshot) => void,
): ConnectionState {
  const handlers = useRef({ onEvent, onResync });
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    handlers.current = { onEvent, onResync };
  }, [onEvent, onResync]);

  useEffect(() => {
    const connectionHandle = connectAlertSocket({
      url: `${WS_BASE_URL}/ws/alerts/${alertId}`,
      // The socket is a diff channel; this endpoint is the source of truth the
      // diffs apply on top of, on load and again after every outage.
      fetchSnapshot: () => fetchAlertStatus(alertId),
      onFrame: (data: string) => {
        let payload: unknown;
        try {
          payload = JSON.parse(data);
        } catch {
          // Nothing the console can do with a frame it cannot read, and throwing
          // here would take the socket down with it.
          return;
        }
        // Event types this build predates (`household_unreached` and the
        // dispatch lifecycle events) are ignored rather than mishandled.
        if (isDeliveryUpdateEvent(payload)) {
          handlers.current.onEvent(payload);
        }
      },
      onResync: (snapshot) => handlers.current.onResync(snapshot),
      onConnectionState: setConnection,
    });

    return () => {
      connectionHandle.close();
    };
  }, [alertId]);

  return connection;
}

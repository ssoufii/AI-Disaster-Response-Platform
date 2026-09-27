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
 *
 * Both halves of the connection carry the dispatcher token (#20): the handshake
 * has it on the URL, because a browser `WebSocket` has no header to put it in,
 * and the resync fetch sends it as a bearer header like any other read.
 */

import { useEffect, useRef, useState } from "react";

import { fetchAlertStatus } from "@/lib/api";
import { alertSocketUrl } from "@/lib/auth";
import { connectAlertSocket, type ConnectionState } from "@/lib/alertSocketController";
import { WS_BASE_URL } from "@/lib/env";
import {
  isDeliveryUpdateEvent,
  isHouseholdUnreachedEvent,
  type AlertSocketEvent,
  type AlertStatusSnapshot,
} from "@/lib/types";

export function useAlertSocket(
  alertId: string,
  token: string,
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
      url: alertSocketUrl(WS_BASE_URL, alertId, token),
      // The socket is a diff channel; this endpoint is the source of truth the
      // diffs apply on top of, on load and again after every outage.
      fetchSnapshot: () => fetchAlertStatus(alertId, token),
      onFrame: (data: string) => {
        let payload: unknown;
        try {
          payload = JSON.parse(data);
        } catch {
          // Nothing the console can do with a frame it cannot read, and throwing
          // here would take the socket down with it.
          return;
        }
        // Event types this build predates (the dispatch lifecycle events) are
        // ignored rather than mishandled.
        if (isDeliveryUpdateEvent(payload) || isHouseholdUnreachedEvent(payload)) {
          handlers.current.onEvent(payload);
        }
      },
      onResync: (snapshot) => handlers.current.onResync(snapshot),
      onConnectionState: setConnection,
    });

    return () => {
      connectionHandle.close();
    };
    // A new token is a new connection: the old one was opened with a credential
    // the backend may no longer accept.
  }, [alertId, token]);

  return connection;
}

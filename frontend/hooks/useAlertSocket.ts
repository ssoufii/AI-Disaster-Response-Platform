"use client";

/**
 * Subscribes the console to one alert's live delivery updates.
 *
 * Opened only after the page has its snapshot, so the socket carries diffs onto
 * a grid that is already on screen.
 *
 * The handler is held in a ref so a new inline callback on each render does not
 * tear the socket down and open a new one — the effect depends on the alert id
 * alone.
 *
 * Reconnect with backoff and the "connection lost" banner are #11's; this hook
 * deliberately stops at opening the socket and delivering what arrives on it.
 */

import { useEffect, useRef } from "react";

import { WS_BASE_URL } from "@/lib/env";
import { isDeliveryUpdateEvent, type AlertSocketEvent } from "@/lib/types";

export function useAlertSocket(alertId: string, onEvent: (event: AlertSocketEvent) => void): void {
  const handler = useRef(onEvent);

  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    const socket = new WebSocket(`${WS_BASE_URL}/ws/alerts/${alertId}`);

    socket.onmessage = (message: MessageEvent<string>) => {
      let payload: unknown;
      try {
        payload = JSON.parse(message.data);
      } catch {
        // Nothing the console can do with a frame it cannot read, and throwing
        // here would take the socket down with it.
        return;
      }
      // Event types this build predates (`household_unreached` and the dispatch
      // lifecycle events) are ignored rather than mishandled.
      if (isDeliveryUpdateEvent(payload)) {
        handler.current(payload);
      }
    };

    return () => {
      socket.close();
    };
  }, [alertId]);
}

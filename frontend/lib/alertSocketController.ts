/**
 * Keeps one alert's socket connected, and says plainly when it is not.
 *
 * A dispatcher console that silently goes stale during a disaster is a critical
 * failure (CLAUDE.md, Frontend Conventions): a dispatcher trusts an all-green
 * screen and stops looking elsewhere. So a dropped socket is reported the moment
 * it drops, retried with exponential backoff forever, and — this is the part
 * that is easy to miss — the snapshot is re-fetched before the reconnected
 * socket is trusted again, because the events that arrived during the outage are
 * gone and no one is going to resend them.
 *
 * Deliberately free of React and of the browser: everything it touches (the
 * socket, the snapshot fetch, the timer) arrives through `options`, so the drop,
 * the backoff schedule and the resync are all observable in a test without a DOM
 * or a real server. `useAlertSocket` is the thin React wrapper over it.
 */

import type { AlertStatusSnapshot } from "@/lib/types";

/**
 * What the console is showing.
 *
 * `live` means the socket is open *and* current. The gap matters: a socket that
 * has just reopened is open but has not yet resynced, and until it has, the grid
 * is still as stale as it was mid-outage — so it stays `reconnecting` and the
 * banner stays up.
 */
export type ConnectionState = "connecting" | "live" | "reconnecting";

/** Only the parts of `WebSocket` this file touches, so a test can hand it a fake. */
export interface SocketLike {
  onopen: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  close(): void;
}

export type TimerHandle = ReturnType<typeof setTimeout>;

export interface AlertSocketOptions {
  url: string;
  /** Re-read on every reconnect — the outage's events are not replayed. */
  fetchSnapshot: () => Promise<AlertStatusSnapshot>;
  /** One raw socket frame. Parsing is the caller's; this file only sequences. */
  onFrame: (data: string) => void;
  onResync: (snapshot: AlertStatusSnapshot) => void;
  onConnectionState: (state: ConnectionState) => void;
  openSocket?: (url: string) => SocketLike;
  setTimer?: (run: () => void, delayMs: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
}

export interface AlertSocketConnection {
  close(): void;
}

export const RECONNECT_BASE_DELAY_MS = 1_000;
export const RECONNECT_MAX_DELAY_MS = 30_000;

/**
 * How long to wait before reconnect attempt `attempt` (0-based): 1s, 2s, 4s, 8s,
 * 16s, then 30s from there on.
 *
 * Capped rather than given up on. A backend redeploy is back in seconds and a
 * longer outage is exactly when the console must not quietly stop trying.
 */
export function backoffDelayMs(attempt: number): number {
  return Math.min(RECONNECT_BASE_DELAY_MS * 2 ** attempt, RECONNECT_MAX_DELAY_MS);
}

export function connectAlertSocket(options: AlertSocketOptions): AlertSocketConnection {
  const openSocket = options.openSocket ?? ((url: string) => new WebSocket(url));
  const setTimer = options.setTimer ?? ((run: () => void, delayMs: number) => setTimeout(run, delayMs));
  const clearTimer = options.clearTimer ?? ((handle: TimerHandle) => clearTimeout(handle));

  let socket: SocketLike | null = null;
  let timer: TimerHandle | null = null;
  /** Reconnects since the last time the console was known current. */
  let attempt = 0;
  let closed = false;
  let resyncing = false;
  let held: string[] = [];
  let reported: ConnectionState | null = null;

  function report(state: ConnectionState): void {
    if (reported === state) {
      return;
    }
    reported = state;
    options.onConnectionState(state);
  }

  function open(): void {
    const current = openSocket(options.url);
    socket = current;

    current.onopen = () => {
      if (closed || socket !== current) {
        return;
      }
      // A first connection has missed nothing — the page was server-rendered
      // from a snapshot moments ago. One that follows a drop has, so it is not
      // trusted until it has re-read the snapshot.
      if (attempt === 0) {
        report("live");
        return;
      }
      void resync(current);
    };

    current.onmessage = (event: MessageEvent<string>) => {
      if (closed || socket !== current) {
        return;
      }
      // Held back rather than applied: a frame that arrives mid-resync is newer
      // than the snapshot in flight, and applying it first would let the older
      // snapshot land on top of it.
      if (resyncing) {
        held.push(event.data);
        return;
      }
      options.onFrame(event.data);
    };

    // `error` and `close` both mean the same thing here, and a failing socket
    // usually fires both — whichever lands first does the work.
    current.onerror = () => drop(current);
    current.onclose = () => drop(current);
  }

  function drop(current: SocketLike): void {
    if (closed || socket !== current) {
      return;
    }
    socket = null;
    resyncing = false;
    held = [];
    current.close();
    scheduleReconnect();
  }

  async function resync(current: SocketLike): Promise<void> {
    resyncing = true;

    let snapshot: AlertStatusSnapshot;
    try {
      snapshot = await options.fetchSnapshot();
    } catch {
      // The socket is up but the console cannot be shown as current, so the
      // banner stays and the whole connection is retried on the next backoff
      // step. Clearing it here would be the silent staleness this file exists
      // to prevent.
      drop(current);
      return;
    }

    if (closed || socket !== current) {
      return;
    }

    options.onResync(snapshot);

    const buffered = held;
    held = [];
    resyncing = false;
    for (const data of buffered) {
      options.onFrame(data);
    }

    attempt = 0;
    report("live");
  }

  function scheduleReconnect(): void {
    if (closed) {
      return;
    }
    // Before the wait, not after it: the banner has to be up while the console
    // is stale, and at the tail of the schedule "after" is half a minute late.
    report("reconnecting");

    const delayMs = backoffDelayMs(attempt);
    attempt += 1;
    timer = setTimer(() => {
      timer = null;
      if (!closed) {
        open();
      }
    }, delayMs);
  }

  report("connecting");
  open();

  return {
    close(): void {
      closed = true;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      const current = socket;
      socket = null;
      current?.close();
    },
  };
}

/**
 * The console's failure path, which is the only part of it a dispatcher's trust
 * actually rests on: a drop has to show, a retry has to keep coming, and a
 * recovered socket must not be called current until it has re-read the snapshot.
 *
 * Run with the Node test runner (`npm test` in `frontend/`) — no test framework
 * is installed, and none is needed: `connectAlertSocket` takes its socket, its
 * snapshot fetch and its timer as arguments precisely so this file can supply
 * all three. Twilio and Anthropic are nowhere near this layer.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  RECONNECT_MAX_DELAY_MS,
  backoffDelayMs,
  connectAlertSocket,
  type AlertSocketOptions,
  type ConnectionState,
  type SocketLike,
  type TimerHandle,
} from "../lib/alertSocketController.ts";
import type { AlertStatusSnapshot } from "../lib/types.ts";

const SOCKET_URL = "ws://test/ws/alerts/alert-1";

function snapshotWith(householdId: string): AlertStatusSnapshot {
  return {
    alert_id: "alert-1",
    status: "dispatching",
    households: [
      {
        household_id: householdId,
        name: "Test household",
        preferred_channel: "sms",
        last_known_status: "unknown",
        current_attempt: null,
      },
    ],
  };
}

/** A socket whose open, message, error and close this test drives by hand. */
class FakeSocket implements SocketLike {
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  closed = false;

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.({} as Event);
  }

  deliver(data: string): void {
    this.onmessage?.({ data } as MessageEvent<string>);
  }

  drop(): void {
    this.onclose?.({} as CloseEvent);
  }
}

interface Harness {
  sockets: FakeSocket[];
  delays: number[];
  states: ConnectionState[];
  frames: string[];
  resyncs: AlertStatusSnapshot[];
  /** Runs the pending reconnect timer, as the scheduled wait elapsing would. */
  elapse(): void;
  close(): void;
}

function harness(
  overrides: Partial<Pick<AlertSocketOptions, "fetchSnapshot">> = {},
): Harness {
  const sockets: FakeSocket[] = [];
  const delays: number[] = [];
  const states: ConnectionState[] = [];
  const frames: string[] = [];
  const resyncs: AlertStatusSnapshot[] = [];
  const pending: Array<() => void> = [];

  const connection = connectAlertSocket({
    url: SOCKET_URL,
    fetchSnapshot: overrides.fetchSnapshot ?? (() => Promise.resolve(snapshotWith("h-1"))),
    onFrame: (data) => frames.push(data),
    onResync: (snapshot) => resyncs.push(snapshot),
    onConnectionState: (state) => states.push(state),
    openSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    setTimer: (run, delayMs) => {
      delays.push(delayMs);
      pending.push(run);
      return pending.length as unknown as TimerHandle;
    },
    clearTimer: () => {
      pending.length = 0;
    },
  });

  return {
    sockets,
    delays,
    states,
    frames,
    resyncs,
    elapse() {
      const run = pending.shift();
      assert.ok(run !== undefined, "expected a reconnect to be scheduled");
      run();
    },
    close: () => connection.close(),
  };
}

/** Lets the controller's own awaited `fetchSnapshot` settle. */
function flush(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

test("backoff doubles from one second and then holds at the cap", () => {
  assert.deepEqual(
    [0, 1, 2, 3, 4].map(backoffDelayMs),
    [1_000, 2_000, 4_000, 8_000, 16_000],
  );
  assert.equal(backoffDelayMs(5), RECONNECT_MAX_DELAY_MS);
  assert.equal(backoffDelayMs(40), RECONNECT_MAX_DELAY_MS);
});

test("a first connection goes live without a resync — it has missed nothing", async () => {
  const h = harness();

  assert.equal(h.sockets.length, 1);
  assert.deepEqual(h.states, ["connecting"]);

  h.sockets[0].open();
  await flush();

  assert.deepEqual(h.states, ["connecting", "live"]);
  assert.deepEqual(h.resyncs, []);
  h.close();
});

test("a dropped socket reports reconnecting before the first retry is waited out", () => {
  const h = harness();
  h.sockets[0].open();

  h.sockets[0].drop();

  // The banner is driven off this state, and it has to be up while the console
  // is stale — not after the backoff has elapsed.
  assert.deepEqual(h.states, ["connecting", "live", "reconnecting"]);
  assert.deepEqual(h.delays, [1_000]);
  assert.equal(h.sockets.length, 1, "the retry socket opens only once the wait elapses");
  h.close();
});

test("each failed reconnect attempt opens a new socket and waits longer", () => {
  const h = harness();
  h.sockets[0].open();
  h.sockets[0].drop();

  for (let i = 0; i < 6; i += 1) {
    h.elapse();
    h.sockets[h.sockets.length - 1].drop();
  }

  assert.equal(h.sockets.length, 7, "one initial socket plus six retries");
  assert.deepEqual(h.delays, [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000]);
  h.close();
});

test("the banner stays up across repeated failures rather than flickering", () => {
  const h = harness();
  h.sockets[0].open();
  h.sockets[0].drop();

  for (let i = 0; i < 4; i += 1) {
    h.elapse();
    h.sockets[h.sockets.length - 1].drop();
  }

  assert.deepEqual(h.states, ["connecting", "live", "reconnecting"]);
  assert.equal(
    h.states.filter((state) => state === "live").length,
    1,
    "never reported live again while still down",
  );
  h.close();
});

test("a reconnect resyncs from the snapshot before it is called live", async () => {
  const order: string[] = [];
  const h = harness({
    fetchSnapshot: () => {
      order.push("fetch");
      return Promise.resolve(snapshotWith("h-joined-during-outage"));
    },
  });
  h.sockets[0].open();
  h.sockets[0].drop();
  h.elapse();

  h.sockets[1].open();
  // Open, but not yet current: the outage's events are gone until the snapshot
  // lands, so the banner must still be up here.
  assert.deepEqual(h.states, ["connecting", "live", "reconnecting"]);

  await flush();

  order.push("live");
  assert.deepEqual(order, ["fetch", "live"]);
  assert.deepEqual(h.states, ["connecting", "live", "reconnecting", "live"]);
  assert.equal(h.resyncs.length, 1);
  assert.equal(h.resyncs[0].households[0].household_id, "h-joined-during-outage");
  h.close();
});

test("frames arriving mid-resync are applied after the snapshot, not under it", async () => {
  let release = (): void => {};
  const h = harness({
    fetchSnapshot: () =>
      new Promise<AlertStatusSnapshot>((resolve) => {
        release = () => resolve(snapshotWith("h-1"));
      }),
  });
  h.sockets[0].open();
  h.sockets[0].drop();
  h.elapse();
  h.sockets[1].open();
  await flush();

  h.sockets[1].deliver('{"seq":1}');
  assert.deepEqual(h.frames, [], "held back while the snapshot is in flight");

  release();
  await flush();

  assert.deepEqual(h.resyncs.length, 1);
  assert.deepEqual(h.frames, ['{"seq":1}'], "and applied on top of it afterwards");
  h.close();
});

test("a resync that fails keeps the banner up and retries the connection", async () => {
  let attempts = 0;
  const h = harness({
    fetchSnapshot: () => {
      attempts += 1;
      return attempts === 1
        ? Promise.reject(new Error("backend unreachable"))
        : Promise.resolve(snapshotWith("h-1"));
    },
  });
  h.sockets[0].open();
  h.sockets[0].drop();

  h.elapse();
  h.sockets[1].open();
  await flush();

  // The socket came back but the snapshot did not, so the console is still
  // stale and must not say otherwise.
  assert.deepEqual(h.states, ["connecting", "live", "reconnecting"]);
  assert.deepEqual(h.resyncs, []);
  assert.deepEqual(h.delays, [1_000, 2_000]);

  h.elapse();
  h.sockets[2].open();
  await flush();

  assert.deepEqual(h.states, ["connecting", "live", "reconnecting", "live"]);
  assert.equal(h.resyncs.length, 1);
  h.close();
});

test("a recovered connection starts its backoff over on the next drop", async () => {
  const h = harness();
  h.sockets[0].open();
  h.sockets[0].drop();
  h.elapse();
  h.sockets[1].open();
  await flush();

  h.sockets[1].drop();

  assert.deepEqual(h.delays, [1_000, 1_000], "not 1s then 2s — this is a fresh outage");
  h.close();
});

test("closing the console stops the reconnect loop", () => {
  const h = harness();
  h.sockets[0].open();
  h.sockets[0].drop();

  h.close();

  assert.equal(h.sockets[0].closed, true);
  assert.throws(() => h.elapse(), "no reconnect is left pending after close");
  assert.equal(h.sockets.length, 1);
});

test("a socket error is treated as a drop, and the pair is not retried twice", () => {
  const h = harness();
  h.sockets[0].open();

  h.sockets[0].onerror?.({} as Event);
  h.sockets[0].drop();

  assert.deepEqual(h.delays, [1_000], "error then close is one outage, not two");
  h.close();
});

/**
 * A scripted dispatch for the static demo at `/demo`.
 *
 * The published demo (GitHub Pages) has no backend to talk to, so instead of a
 * socket it plays this script: a snapshot to seed the console, then a timeline
 * of the same `delivery_update` / `household_unreached` events the backend
 * emits, fed through the real reducer and rendered by the real grid. Nothing
 * here is a second implementation of the console — only the event source is
 * fake.
 *
 * The households mirror the seed data CLAUDE.md asks for: low-literacy English,
 * a non-English SMS, Spanish voice, ASL video, a household rescued by its
 * fallback, and one number that fails on every channel. Names are invented.
 */

import type {
  AlertSocketEvent,
  AlertStatusSnapshot,
  Channel,
  DeliveryStatus,
  DeliveryUpdateEvent,
  HouseholdUnreachedEvent,
} from "@/lib/types";

export const DEMO_ALERT_ID = "demo-flood-evacuation";

export type DemoStep =
  | { atMs: number; kind: "event"; event: AlertSocketEvent }
  /**
   * The socket dropping and coming back, so the demo shows the reconnect
   * banner. The real console also re-reads the snapshot after an outage; the
   * script simply sends nothing while "disconnected", so there is nothing to
   * resync.
   */
  | { atMs: number; kind: "connection"; state: "reconnecting" | "live" };

const HOUSEHOLDS = [
  { id: "hh-rivera", name: "Rivera household", channel: "sms" },
  { id: "hh-nguyen", name: "Nguyen household", channel: "sms" },
  { id: "hh-garcia", name: "García household", channel: "voice" },
  { id: "hh-okafor", name: "Okafor household", channel: "video" },
  { id: "hh-patel", name: "Patel household", channel: "sms" },
  { id: "hh-test", name: "Test line (always fails)", channel: "sms" },
] as const satisfies readonly { id: string; name: string; channel: Channel }[];

export function demoSnapshot(): AlertStatusSnapshot {
  return {
    alert_id: DEMO_ALERT_ID,
    status: "dispatching",
    households: HOUSEHOLDS.map((household) => ({
      household_id: household.id,
      name: household.name,
      preferred_channel: household.channel,
      last_known_status: "unknown",
      current_attempt: null,
    })),
  };
}

/**
 * The timeline, relative to when playback starts. Timestamps are filled in
 * from `startedAt` so the grid's "Updated" column reads as now.
 */
export function demoTimeline(startedAt: Date): DemoStep[] {
  const at = (ms: number) => new Date(startedAt.getTime() + ms).toISOString();

  const update = (
    atMs: number,
    household_id: string,
    channel: Channel,
    status: DeliveryStatus,
    attempt_number: number,
    fallback_channel: Channel | null = null,
  ): DemoStep => {
    const event: DeliveryUpdateEvent = {
      type: "delivery_update",
      alert_id: DEMO_ALERT_ID,
      household_id,
      channel,
      status,
      attempt_number,
      fallback_triggered: fallback_channel !== null,
      fallback_channel,
      timestamp: at(atMs),
    };
    return { atMs, kind: "event", event };
  };

  const unreached = (
    atMs: number,
    household_id: string,
    last_channel: Channel,
    attempts_made: number,
  ): DemoStep => {
    const event: HouseholdUnreachedEvent = {
      type: "household_unreached",
      alert_id: DEMO_ALERT_ID,
      household_id,
      last_channel,
      attempts_made,
      last_known_status: "unreached",
      timestamp: at(atMs),
    };
    return { atMs, kind: "event", event };
  };

  return [
    // Every household's first attempt goes out on its preferred channel.
    update(600, "hh-rivera", "sms", "queued", 1),
    update(800, "hh-nguyen", "sms", "queued", 1),
    update(1000, "hh-garcia", "voice", "queued", 1),
    update(1200, "hh-okafor", "video", "queued", 1),
    update(1400, "hh-patel", "sms", "queued", 1),
    update(1600, "hh-test", "sms", "queued", 1),

    update(2600, "hh-rivera", "sms", "delivered", 1),
    update(3000, "hh-garcia", "voice", "sending", 1),
    update(3400, "hh-nguyen", "sms", "delivered", 1),

    // SMS failures reroute to each household's next channel (Domain Rule 2: a
    // fallback is a new attempt, never the old one rewritten).
    update(4200, "hh-patel", "sms", "failed", 1, "voice"),
    update(4600, "hh-test", "sms", "failed", 1, "voice"),
    update(5400, "hh-patel", "voice", "queued", 2),
    update(5600, "hh-test", "voice", "queued", 2),

    update(6200, "hh-okafor", "video", "delivered", 1),
    update(6600, "hh-garcia", "voice", "delivered", 1),

    { atMs: 7600, kind: "connection", state: "reconnecting" },
    { atMs: 10_600, kind: "connection", state: "live" },

    // Delivered is not received (Domain Rule 6): only a keypress on the call
    // turns a household green-confirmed.
    update(11_400, "hh-garcia", "voice", "confirmed_received", 1),
    update(12_200, "hh-patel", "voice", "delivered", 2),
    update(12_800, "hh-test", "voice", "no_answer", 2, "video"),
    update(13_600, "hh-test", "video", "queued", 3),
    update(14_600, "hh-patel", "voice", "confirmed_received", 2),
    update(15_800, "hh-test", "video", "failed", 3),

    // The chain ran out: red, and flagged for a person (Domain Rule 4).
    unreached(16_600, "hh-test", "video", 3),
  ];
}

"use client";

/**
 * The public simulation demo: the real console, pointed at whichever alert the
 * demo backend dispatched last.
 *
 * It has no alert id of its own, so it asks `GET /alerts/latest`, loads that
 * alert's snapshot, and only then mounts the console — snapshot first, socket
 * second, exactly as a dispatcher's console does. It keeps asking, so when the
 * backend dispatches its next mock alert the console moves on to it.
 *
 * Asking which alert is newest is not delivery polling: every status on screen
 * still arrives over the socket. This only notices that there is a new alert to
 * subscribe to.
 *
 * The demo backend is on free hosting and may be asleep when a visitor arrives.
 * That is said outright rather than shown as a blank or broken page.
 */

import { useEffect, useState } from "react";

import { AboutDemo } from "@/components/AboutDemo";
import { AlertConsole } from "@/components/AlertConsole";
import { fetchAlertStatus, fetchLatestAlert } from "@/lib/api";
import type { AlertStatusSnapshot, LatestAlert } from "@/lib/types";

const RETRY_MS = 3_000;
const CHECK_FOR_NEW_ALERT_MS = 5_000;

type Phase =
  | { kind: "waking"; attempts: number }
  | { kind: "no-alert" }
  | { kind: "ready"; alert: LatestAlert; snapshot: AlertStatusSnapshot };

function Waiting({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-[1152px] px-4 pt-6 pb-16 sm:px-6">
      <AboutDemo />
      <h1 className="text-2xl leading-8 font-semibold tracking-[-0.01em]">{title}</h1>
      <div className="mt-3 flex max-w-xl items-start gap-3" role="status" aria-live="polite">
        <span className="tone-dot live-dot mt-2" data-tone="grey" aria-hidden />
        <p className="text-base">{children}</p>
      </div>
    </main>
  );
}

export function LiveConsole() {
  const [phase, setPhase] = useState<Phase>({ kind: "waking", attempts: 0 });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let currentId: string | null = null;
    let failures = 0;

    async function check() {
      let delay = CHECK_FOR_NEW_ALERT_MS;
      try {
        const latest = await fetchLatestAlert();
        if (cancelled) {
          return;
        }
        failures = 0;
        if (latest === null) {
          currentId = null;
          setPhase({ kind: "no-alert" });
          delay = RETRY_MS;
        } else if (latest.id !== currentId) {
          const snapshot = await fetchAlertStatus(latest.id, null);
          if (cancelled) {
            return;
          }
          currentId = latest.id;
          setPhase({ kind: "ready", alert: latest, snapshot });
        } else {
          // Same alert: only the countdown to the next one may have moved.
          setPhase((previous) =>
            previous.kind === "ready" &&
            previous.alert.next_dispatch_at !== latest.next_dispatch_at
              ? { ...previous, alert: latest }
              : previous,
          );
        }
      } catch {
        if (cancelled) {
          return;
        }
        failures += 1;
        // A console that is already showing an alert keeps showing it: its own
        // socket banner says when it is stale. Only a console with nothing to
        // show says the server is waking.
        if (currentId === null) {
          setPhase({ kind: "waking", attempts: failures });
        }
        delay = RETRY_MS;
      }
      timer = setTimeout(check, delay);
    }

    void check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  if (phase.kind === "waking") {
    return (
      <Waiting title="Waking up the demo server">
        The live demo runs on free hosting, which puts it to sleep when nobody is watching. It
        usually takes under a minute to start
        {phase.attempts > 1 ? ` — still trying (attempt ${phase.attempts}).` : "."}
      </Waiting>
    );
  }

  if (phase.kind === "no-alert") {
    return (
      <Waiting title="Starting the first mock alert">
        The demo server is up and is about to dispatch its first alert. This takes a few seconds.
      </Waiting>
    );
  }

  return (
    // Keyed by alert: a new alert is a new console, with its own reducer and
    // its own socket, rather than the old one's rows patched into shape.
    <AlertConsole
      key={phase.alert.id}
      snapshot={phase.snapshot}
      token={null}
      alert={phase.alert}
      zoneName={phase.alert.zone_name}
      nextDispatchAt={phase.alert.next_dispatch_at}
      intro={<AboutDemo />}
    />
  );
}

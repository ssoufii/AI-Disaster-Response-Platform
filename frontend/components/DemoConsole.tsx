"use client";

/**
 * The console, driven by `lib/demoScript.ts` instead of a socket.
 *
 * Same reducer, same banner, same grid as `AlertConsole` — the only thing
 * swapped out is where events come from, so what the demo shows is what a
 * dispatcher would see.
 */

import { useEffect, useReducer, useState } from "react";

import { ConnectionBanner } from "@/components/ConnectionBanner";
import { HouseholdStatusGrid } from "@/components/HouseholdStatusGrid";
import type { ConnectionState } from "@/lib/alertSocketController";
import { consoleReducer, initialConsoleState } from "@/lib/consoleState";
import { demoSnapshot, demoTimeline } from "@/lib/demoScript";

/** One playback. Replaying remounts it, so every run starts from the empty snapshot. */
function DemoPlayback({ onReplay }: { onReplay: () => void }) {
  const [state, dispatch] = useReducer(consoleReducer, demoSnapshot(), initialConsoleState);
  const [connection, setConnection] = useState<ConnectionState>("live");
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    const steps = demoTimeline(new Date());
    const timers = steps.map((step) =>
      setTimeout(() => {
        if (step.kind === "connection") {
          setConnection(step.state);
        } else if (step.event.type === "household_unreached") {
          dispatch({ type: "household_unreached", event: step.event });
        } else {
          dispatch({ type: "delivery_update", event: step.event });
        }
      }, step.atMs),
    );
    const lastAt = Math.max(...steps.map((step) => step.atMs));
    timers.push(setTimeout(() => setFinished(true), lastAt + 400));

    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="text-sm text-slate-600">
          {finished ? "Playback finished." : "Playing a scripted dispatch…"}
        </p>
        <button
          type="button"
          onClick={onReplay}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          Replay
        </button>
      </div>
      <ConnectionBanner state={connection} />
      <HouseholdStatusGrid state={state} />
    </>
  );
}

export function DemoConsole() {
  const [run, setRun] = useState(0);
  return <DemoPlayback key={run} onReplay={() => setRun((n) => n + 1)} />;
}

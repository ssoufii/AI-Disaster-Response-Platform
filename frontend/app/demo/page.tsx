/**
 * A scripted playback of the dispatcher console, needing no backend.
 *
 * This is the page the GitHub Pages build publishes. It renders the real
 * console components over events from `lib/demoScript.ts`; see `DemoConsole`.
 */

import type { Metadata } from "next";

import { DemoConsole } from "@/components/DemoConsole";

export const metadata: Metadata = {
  title: "Dispatcher console demo",
  description: "A scripted playback of the live delivery console.",
};

export default function DemoPage() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-6">
        <p className="text-sm uppercase tracking-wide text-slate-500">
          Dispatcher console · demo
        </p>
        <h1 className="mt-1 text-2xl font-semibold">Flood evacuation, Riverside zone</h1>
        <p className="mt-2 max-w-3xl text-slate-600">
          A scripted playback. The events are fake, but the console that renders them is the
          real one. Each household is reached on its preferred channel, and a failed delivery
          reroutes to the next channel. The connection drops partway through, and a household
          is flagged for a person once every channel has failed. Open a row&apos;s history to
          see each attempt.
        </p>
      </header>

      <DemoConsole />
    </main>
  );
}

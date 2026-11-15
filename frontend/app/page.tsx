/**
 * There is no console without an alert to watch.
 *
 * Choosing or drafting one is `AlertComposer`'s job, which is not built yet;
 * until it is, this says where the console lives rather than 404ing at the root.
 *
 * The static GitHub Pages build (`NEXT_PUBLIC_STATIC_DEMO`) has no live console
 * or sign-in to point at, so there it introduces the project and links to the
 * scripted demo instead.
 */

import Link from "next/link";

const STATIC_DEMO = process.env.NEXT_PUBLIC_STATIC_DEMO === "1";

function StaticDemoHome() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <p className="text-sm uppercase tracking-wide text-slate-500">AI Disaster Response Platform</p>
      <h1 className="mt-1 text-2xl font-semibold">Disaster alerts that reach every household</h1>
      <p className="mt-2 text-slate-600">
        The Claude API rewrites a dispatcher&apos;s alert for each household: plain-language SMS,
        a voice script, an ASL video caption, or the household&apos;s own language. Twilio
        delivers it. When a delivery fails, it reroutes to the household&apos;s next channel
        automatically, and this console shows each step live.
      </p>
      <p className="mt-2 text-slate-600">
        This site has no backend, so the console below plays back a scripted dispatch.
      </p>
      <Link
        className="mt-6 inline-block rounded bg-slate-900 px-4 py-2 text-white"
        href="/demo"
      >
        Watch the demo
      </Link>
    </main>
  );
}

export default function Home() {
  if (STATIC_DEMO) {
    return <StaticDemoHome />;
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <p className="text-sm uppercase tracking-wide text-slate-500">Dispatcher console</p>
      <h1 className="mt-1 text-2xl font-semibold">Pick an alert to watch</h1>
      <p className="mt-2 text-slate-600">
        Live delivery status for one alert is at <code>/alerts/&lt;alert id&gt;</code>.
      </p>
      <p className="mt-2 text-slate-600">
        Watching one needs the dispatch desk&apos;s token —{" "}
        <Link className="underline" href="/sign-in">
          sign in
        </Link>{" "}
        before an incident, not during one.
      </p>
      <p className="mt-2 text-slate-600">
        No backend running?{" "}
        <Link className="underline" href="/demo">
          Watch a scripted demo
        </Link>
        .
      </p>
    </main>
  );
}

/**
 * The root of the site.
 *
 * Two builds share it. The public demo build (`NEXT_PUBLIC_LIVE_DEMO=1`, set by
 * `.github/workflows/pages.yml`) opens straight onto the live console, pointed
 * at the simulation backend. A dispatch desk's own deployment has no alert to
 * show at the root, so it says where a console lives instead of 404ing.
 */

import Link from "next/link";

import { LiveConsole } from "@/components/LiveConsole";

const LIVE_DEMO = process.env.NEXT_PUBLIC_LIVE_DEMO === "1";

export default function Home() {
  if (LIVE_DEMO) {
    return <LiveConsole />;
  }

  return (
    <main className="mx-auto max-w-[1152px] px-4 pt-6 pb-16 sm:px-6">
      <h1 className="text-2xl leading-8 font-semibold tracking-[-0.01em]">Dispatcher console</h1>
      <p className="mt-3 max-w-xl text-base">
        Each alert has its own console at <code>/alerts/&lt;alert id&gt;</code>, showing every
        household in the zone and how its delivery stands.
      </p>
      <p className="mt-2 max-w-xl text-muted">
        The console needs the dispatch desk&apos;s token.{" "}
        <Link className="text-ink underline underline-offset-4" href="/sign-in">
          Sign in
        </Link>{" "}
        before an incident, not during one.
      </p>
    </main>
  );
}

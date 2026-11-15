"use client";

/**
 * Where a dispatcher presents the desk's token.
 *
 * One field, because there is one credential: #20 puts user and role management
 * out of scope, and the token is shared by the people already trusted to order an
 * evacuation. The form stores it in this browser and sends the dispatcher on to
 * whatever they were trying to reach.
 *
 * A client component, and it has to be: the token is stored in a cookie that the
 * browser half of the console also reads, and nothing is posted to a server that
 * would need to set one.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { storeToken } from "@/lib/auth";

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [token, setToken] = useState("");

  // Where the dispatcher was headed before they were asked to sign in. Confined
  // to a path on this console: a `next` pointing anywhere else would make this
  // form a redirector for whoever wrote the link.
  const requested = params.get("next") ?? "/";
  const destination = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/";

  return (
    <form
      className="mt-6"
      onSubmit={(event) => {
        event.preventDefault();
        storeToken(token.trim());
        // `refresh` because the page being returned to is server-rendered from
        // the snapshot, and it has to be re-rendered now that the request will
        // carry a token.
        router.replace(destination);
        router.refresh();
      }}
    >
      <label className="block text-[13px] text-muted" htmlFor="dispatcher-token">
        Dispatcher token
      </label>
      <input
        id="dispatcher-token"
        name="dispatcher-token"
        type="password"
        autoComplete="current-password"
        value={token}
        onChange={(event) => setToken(event.target.value)}
        className="mt-1 h-9 w-full rounded-md border border-line bg-surface px-3"
      />
      <button
        type="submit"
        disabled={token.trim() === ""}
        className="pressable mt-4 inline-flex h-9 items-center rounded-md bg-ink px-4 font-medium text-surface disabled:opacity-40"
      >
        Open the console
      </button>
    </form>
  );
}

export default function SignInPage() {
  return (
    <main className="mx-auto max-w-[1152px] px-4 pt-6 pb-16 sm:px-6">
      <div className="max-w-sm">
      <h1 className="text-2xl leading-8 font-semibold tracking-[-0.01em]">Sign in</h1>
      <p className="mt-3 text-base">
        The console needs the dispatch desk&apos;s token before it can show a delivery in progress.
      </p>
      {/* `useSearchParams` reads from the request, so the form is rendered on the
          client — the boundary is explicit rather than left to fail at build. */}
      <Suspense fallback={null}>
        <SignInForm />
      </Suspense>
      </div>
    </main>
  );
}

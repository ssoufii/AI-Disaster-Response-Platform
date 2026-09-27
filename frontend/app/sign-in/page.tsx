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
      <label className="block text-sm font-medium text-slate-700" htmlFor="dispatcher-token">
        Dispatcher token
      </label>
      <input
        id="dispatcher-token"
        name="dispatcher-token"
        type="password"
        autoComplete="current-password"
        value={token}
        onChange={(event) => setToken(event.target.value)}
        className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
      />
      <button
        type="submit"
        disabled={token.trim() === ""}
        className="mt-4 rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
      >
        Open the console
      </button>
    </form>
  );
}

export default function SignInPage() {
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <p className="text-sm uppercase tracking-wide text-slate-500">Dispatcher console</p>
      <h1 className="mt-1 text-2xl font-semibold">Sign in</h1>
      <p className="mt-2 text-slate-600">
        The console needs the dispatch desk&apos;s token before it can show a delivery in progress.
      </p>
      {/* `useSearchParams` reads from the request, so the form is rendered on the
          client — the boundary is explicit rather than left to fail at build. */}
      <Suspense fallback={null}>
        <SignInForm />
      </Suspense>
    </main>
  );
}

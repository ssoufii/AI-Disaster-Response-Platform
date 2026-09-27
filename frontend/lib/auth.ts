/**
 * The dispatcher token, and where the console keeps it.
 *
 * One shared token for the dispatch desk rather than per-user accounts — #20 puts
 * user and role management out of scope, and a console nobody can sign into
 * during an incident is worse than a credential held by the people already
 * trusted to order an evacuation.
 *
 * It lives in a cookie because the console needs it in three places that do not
 * share any other storage: the server component that renders the page, the
 * browser-side resync fetch, and the WebSocket URL. A cookie is the one thing a
 * server component can read (via `next/headers`) that the browser also sends on
 * its own.
 *
 * Deliberately readable by JavaScript, which is not an oversight: the browser
 * half of the console has to put the token in an `Authorization` header and in
 * the socket URL itself, so an `httpOnly` cookie could not do the job. What that
 * costs is that a script running on this origin could read it — and what it buys
 * is a token that is revocable server-side by changing one environment variable,
 * rather than a password anyone reuses.
 *
 * Everything here is a pure function over strings so it is testable without a
 * DOM or a server (`npm test`); the two callers do the reading.
 */

export const DISPATCHER_TOKEN_COOKIE = "dispatcher_token";

/**
 * The token out of a `Cookie` header or `document.cookie`, or `null`.
 *
 * Both are the same format, which is why one parser serves the server component
 * and the browser. A cookie value is percent-encoded on the way in, so it is
 * decoded on the way out.
 */
export function tokenFromCookies(cookieHeader: string | null | undefined): string | null {
  if (!cookieHeader) {
    return null;
  }

  for (const pair of cookieHeader.split(";")) {
    const separator = pair.indexOf("=");
    if (separator === -1) {
      continue;
    }
    if (pair.slice(0, separator).trim() !== DISPATCHER_TOKEN_COOKIE) {
      continue;
    }
    const value = decodeURIComponent(pair.slice(separator + 1).trim());
    return value || null;
  }

  return null;
}

/** The `Authorization` header a signed-in console sends, or nothing at all. */
export function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * The socket URL for one alert, with the token on it.
 *
 * The token goes in the query string because a browser's `WebSocket` constructor
 * takes a URL and nothing else — there is no handshake header to put it in. The
 * backend reads it there and refuses the handshake without it.
 */
export function alertSocketUrl(baseUrl: string, alertId: string, token: string | null): string {
  const url = `${baseUrl.replace(/\/$/, "")}/ws/alerts/${encodeURIComponent(alertId)}`;
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}

/**
 * Store the token in this browser, as a session cookie.
 *
 * Session-scoped on purpose: a shared desk credential should not outlive the
 * browser it was typed into. `SameSite=Strict` because nothing off this origin
 * has any business sending it, and `Secure` on HTTPS so a credential is never
 * sent in the clear — added conditionally rather than always, because a `Secure`
 * cookie is dropped outright on a plain-HTTP dev console.
 */
export function storeToken(token: string): void {
  const secure = typeof location !== "undefined" && location.protocol === "https:";
  document.cookie = [
    `${DISPATCHER_TOKEN_COOKIE}=${encodeURIComponent(token)}`,
    "path=/",
    "SameSite=Strict",
    ...(secure ? ["Secure"] : []),
  ].join("; ");
}

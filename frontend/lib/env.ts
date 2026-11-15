/**
 * Where the backend lives.
 *
 * Both are read as literal `process.env.NEXT_PUBLIC_*` member accesses, because
 * that is the form Next inlines at build time — pulling them out of a computed
 * lookup would leave them undefined in the browser bundle.
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * The socket's base URL. Given explicitly, or else the API's with the scheme
 * swapped (http → ws, https → wss) — they are the same server, and deriving it
 * leaves a deployment one URL to configure instead of two that must agree.
 */
export const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL ?? API_BASE_URL.replace(/^http/, "ws");

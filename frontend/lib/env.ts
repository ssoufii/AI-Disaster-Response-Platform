/**
 * Where the backend lives.
 *
 * Both are read as literal `process.env.NEXT_PUBLIC_*` member accesses, because
 * that is the form Next inlines at build time — pulling them out of a computed
 * lookup would leave them undefined in the browser bundle.
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

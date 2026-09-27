/**
 * How the console carries the dispatcher token.
 *
 * Three pure functions, and each one is where a whole path breaks if it is wrong:
 * the cookie parser is read by both the server component and the browser, the
 * header builder is on every API read, and the socket URL is the only place a
 * `WebSocket` can carry a credential at all — its constructor takes a URL and
 * nothing else, so there is no header to put it in.
 *
 * Run with the Node test runner (`npm test` in `frontend/`); no framework is
 * installed and none is needed for functions over strings.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  DISPATCHER_TOKEN_COOKIE,
  alertSocketUrl,
  authHeaders,
  tokenFromCookies,
} from "../lib/auth.ts";

const TOKEN = "desk-token";
const ALERT = "alert-1";
const WS_BASE = "ws://localhost:8000";

test("the token is read out of a cookie header", () => {
  assert.equal(tokenFromCookies(`${DISPATCHER_TOKEN_COOKIE}=${TOKEN}`), TOKEN);
});

test("the token is found among other cookies", () => {
  const header = `theme=dark; ${DISPATCHER_TOKEN_COOKIE}=${TOKEN}; other=1`;

  assert.equal(tokenFromCookies(header), TOKEN);
});

test("a percent-encoded token comes back as it was stored", () => {
  assert.equal(tokenFromCookies(`${DISPATCHER_TOKEN_COOKIE}=a%20b%2Bc`), "a b+c");
});

test("no cookie at all is no token, not an empty one", () => {
  // The distinction the console renders on: null is "sign in", and a wrong token
  // is a different panel.
  assert.equal(tokenFromCookies(undefined), null);
  assert.equal(tokenFromCookies(null), null);
  assert.equal(tokenFromCookies(""), null);
  assert.equal(tokenFromCookies("theme=dark"), null);
});

test("an empty cookie value is no token either", () => {
  assert.equal(tokenFromCookies(`${DISPATCHER_TOKEN_COOKIE}=`), null);
});

test("a cookie whose name merely ends in the token's is not the token", () => {
  assert.equal(tokenFromCookies(`not_${DISPATCHER_TOKEN_COOKIE}=${TOKEN}`), null);
});

test("a read carries the token as a bearer credential", () => {
  assert.deepEqual(authHeaders(TOKEN), { Authorization: `Bearer ${TOKEN}` });
});

test("a read with no token sends no header at all", () => {
  // Rather than an empty bearer, which the API would refuse with the same 401
  // but which reads in a log as a credential that was tried.
  assert.deepEqual(authHeaders(null), {});
});

test("the socket URL carries the token in its query string", () => {
  assert.equal(
    alertSocketUrl(WS_BASE, ALERT, TOKEN),
    `${WS_BASE}/ws/alerts/${ALERT}?token=${TOKEN}`,
  );
});

test("a token with URL-significant characters is encoded", () => {
  assert.equal(
    alertSocketUrl(WS_BASE, ALERT, "a b&c=d"),
    `${WS_BASE}/ws/alerts/${ALERT}?token=a%20b%26c%3Dd`,
  );
});

test("a trailing slash on the base URL does not double up", () => {
  assert.equal(
    alertSocketUrl(`${WS_BASE}/`, ALERT, TOKEN),
    `${WS_BASE}/ws/alerts/${ALERT}?token=${TOKEN}`,
  );
});

test("without a token the socket URL carries no empty query parameter", () => {
  // The backend refuses the handshake either way; this keeps the refusal reading
  // as "no credential" rather than "a blank one".
  assert.equal(alertSocketUrl(WS_BASE, ALERT, null), `${WS_BASE}/ws/alerts/${ALERT}`);
});

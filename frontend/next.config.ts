import type { NextConfig } from "next";

/**
 * Two builds from one app.
 *
 * Normally the console is server-rendered: `/alerts/[id]` reads the dispatcher's
 * cookie and fetches the snapshot before anything reaches the browser.
 *
 * The GitHub Pages build (`.github/workflows/pages.yml`) sets `PAGES_BASE_PATH`
 * and exports a static site instead. Pages has no server and no backend, so
 * that workflow removes the server-only routes before building and publishes
 * the home page and the scripted `/demo`.
 */
const pagesBasePath = process.env.PAGES_BASE_PATH;

const nextConfig: NextConfig =
  pagesBasePath === undefined
    ? {}
    : {
        output: "export",
        basePath: pagesBasePath,
        trailingSlash: true,
      };

export default nextConfig;

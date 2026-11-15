/**
 * The one piece of chrome: the product's mark and name, and where its code is.
 *
 * Shares the content column's edges, so the mark sits exactly above the page
 * title. No colour here on purpose — colour on this screen always means a
 * household's status.
 */

import Link from "next/link";

const REPO_URL = "https://github.com/ssoufii/AI-Disaster-Response-Platform";

/** A signal going out from a point: the product in one glyph. */
function Mark() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
      <circle cx="5" cy="15" r="2.25" fill="currentColor" />
      <path
        d="M5 8.5a6.5 6.5 0 0 1 6.5 6.5M5 3a12 12 0 0 1 12 12"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function TopBar() {
  return (
    <header className="mx-auto flex h-12 max-w-[1152px] items-center justify-between px-4 sm:px-6">
      <Link href="/" className="pressable flex items-center gap-2 font-medium text-ink">
        <Mark />
        Disaster Response
        <span className="font-normal text-muted max-sm:hidden">Dispatcher console</span>
      </Link>
      <a
        href={REPO_URL}
        className="pressable text-muted underline-offset-4 hover:text-ink hover:underline"
      >
        Source on GitHub
      </a>
    </header>
  );
}

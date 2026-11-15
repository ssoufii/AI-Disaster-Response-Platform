/**
 * Says plainly what on this screen is real and what is simulated.
 *
 * A visitor watching households get "called" deserves to know nobody is being
 * called — and a reviewer deserves to know which parts are the production code.
 */

export function AboutDemo() {
  return (
    <section className="rounded-lg border border-line bg-surface px-5 py-4">
      <h2 className="text-base font-medium">About this demo</h2>
      <p className="mt-2">
        This is the platform&apos;s real backend running in simulation mode. A mock alert goes out
        every couple of minutes.
      </p>
      <dl className="mt-3 space-y-2 border-t border-rule pt-3">
        <div>
          <dt className="text-[13px] text-muted">Simulated</dt>
          <dd>
            The households, the phone network (no one is texted or called), and the message text,
            which comes from pre-written templates instead of the Claude API.
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-muted">Real</dt>
          <dd>
            Rerouting a failed delivery to the next channel, flagging a household nobody could
            reach, the audit trail of attempts, and this live feed.
          </dd>
        </div>
      </dl>
    </section>
  );
}

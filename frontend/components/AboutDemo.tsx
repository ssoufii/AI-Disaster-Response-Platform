/**
 * The first thing on the public demo: this is a demonstration, what is
 * simulated, and what the same system does in a real deployment.
 *
 * It sits above the console rather than beside it because it changes how
 * everything below should be read. A visitor watching households get "called"
 * has to know nobody is being called before they see a single status.
 */

export function AboutDemo() {
  return (
    <section
      aria-label="About this demo"
      className="mb-6 rounded-lg border border-line bg-surface px-5 py-4"
    >
      <h2 className="text-base font-medium">This site is a demonstration</h2>
      <p className="mt-1 max-w-3xl">
        Nothing here is a real emergency. The alerts, households and deliveries below are
        simulated, and nobody is texted or called. It shows how the platform behaves while an
        alert goes out.
      </p>
      <div className="mt-3 grid gap-x-8 gap-y-3 border-t border-rule pt-3 md:grid-cols-2">
        <div>
          <h3 className="text-[13px] text-muted">In this demo</h3>
          <p className="mt-0.5">
            A mock alert goes out every couple of minutes to 60 invented households. A simulator
            stands in for the phone network and decides which deliveries fail, and the message
            text comes from pre-written templates. The rerouting, the unreached flag and this
            live feed are the platform&apos;s real code.
          </p>
        </div>
        <div>
          <h3 className="text-[13px] text-muted">In a real emergency</h3>
          <ol className="mt-0.5 list-decimal space-y-0.5 pl-5">
            <li>A dispatcher writes one alert for a zone, with the facts: shelter, route, closures.</li>
            <li>
              The Claude API rewrites it for each household: plain-language text, a voice script,
              an ASL video caption, or their own language. It never changes the facts.
            </li>
            <li>Twilio delivers it by text, phone call, or video message.</li>
            <li>
              If a delivery fails, it is retried on the household&apos;s next channel. If every
              channel fails, the household is flagged here for a person to follow up.
            </li>
          </ol>
        </div>
      </div>
    </section>
  );
}

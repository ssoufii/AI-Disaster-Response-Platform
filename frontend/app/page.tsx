/**
 * There is no console without an alert to watch.
 *
 * Choosing or drafting one is `AlertComposer`'s job, which is not built yet;
 * until it is, this says where the console lives rather than 404ing at the root.
 */

export default function Home() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <p className="text-sm uppercase tracking-wide text-slate-500">Dispatcher console</p>
      <h1 className="mt-1 text-2xl font-semibold">Pick an alert to watch</h1>
      <p className="mt-2 text-slate-600">
        Live delivery status for one alert is at <code>/alerts/&lt;alert id&gt;</code>.
      </p>
    </main>
  );
}

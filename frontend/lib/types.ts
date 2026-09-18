/**
 * The shapes the console reads from the backend.
 *
 * Two sources, one vocabulary: `GET /alerts/{id}/status` for the snapshot the
 * page renders on load, and the `WS /ws/alerts/{id}` events that patch it
 * afterwards. Both are mirrored from the backend's Pydantic schemas, and the WS
 * event has to stay in step with `backend/app/schemas/ws_events.py` and the
 * contract in `docs/architecture.md` — all three move together or none of them
 * do (CLAUDE.md, WebSocket Contract).
 */

export type Channel = "voice" | "sms" | "video" | "whatsapp";

export type DeliveryStatus =
  | "queued"
  | "sending"
  | "delivered"
  | "failed"
  | "no_answer"
  /** Only ever a human's keypress or reply — never inferred from `delivered`. */
  | "confirmed_received";

export type HouseholdStatus = "safe" | "unreached" | "unknown";

export type AlertStatus = "draft" | "dispatching" | "completed";

/** One attempt, exactly as the audit trail recorded it. */
export interface DeliveryAttempt {
  id: string;
  channel: Channel;
  attempt_number: number;
  status: DeliveryStatus;
  twilio_sid: string | null;
  started_at: string;
  completed_at: string | null;
  error_reason: string | null;
}

export interface HouseholdDeliveryStatus {
  household_id: string;
  name: string;
  preferred_channel: Channel;
  last_known_status: HouseholdStatus;
  /** Null when nothing has been attempted yet — not a failure, its own state. */
  current_attempt: DeliveryAttempt | null;
}

export interface AlertStatusSnapshot {
  alert_id: string;
  status: AlertStatus;
  households: HouseholdDeliveryStatus[];
}

/**
 * One household's delivery state changed.
 *
 * Carries the whole state rather than a delta, so a console that connected
 * mid-dispatch — or reconnected after a drop — can apply it without having
 * seen anything before it.
 */
export interface DeliveryUpdateEvent {
  type: "delivery_update";
  alert_id: string;
  household_id: string;
  channel: Channel;
  status: DeliveryStatus;
  attempt_number: number;
  fallback_triggered: boolean;
  fallback_channel: Channel | null;
  timestamp: string;
}

/**
 * What the console knows how to apply today.
 *
 * `household_unreached` (#13), `dispatch_started` and `dispatch_complete` are
 * part of the contract but are not emitted yet; the socket ignores any event
 * type it does not recognise, so they can land without breaking a console that
 * predates them.
 */
export type AlertSocketEvent = DeliveryUpdateEvent;

export function isDeliveryUpdateEvent(value: unknown): value is DeliveryUpdateEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { type?: unknown }).type === "delivery_update" &&
    typeof (value as { household_id?: unknown }).household_id === "string"
  );
}

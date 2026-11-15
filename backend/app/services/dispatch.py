"""Dispatching an alert: generate content for its zone, then deliver it.

One function, shared by the two things that dispatch: the console's
``POST /alerts/{id}/dispatch`` and the simulation demo's scheduler. Neither
talks to Claude or Twilio itself — that stays in ``content_generator`` and
``delivery_service``.
"""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.enums import AlertStatus
from app.models.household import Household
from app.schemas.alert import AlertDispatchRead
from app.services import content_generator, delivery_service


async def dispatch_alert(session: AsyncSession, alert: Alert) -> AlertDispatchRead:
    """Generate content for the alert's zone, then deliver it.

    Generation comes first: delivery has nothing to send until every household
    in the zone has its own AlertContent row. Households that already have
    content for this alert are left alone, so re-dispatching never gives a
    household a second row — and ``delivery_service`` makes the same promise
    about attempts, so re-dispatching never sends the same warning twice.
    """
    households = (
        await session.exec(select(Household).where(Household.zone_id == alert.zone_id))
    ).all()
    already_generated = set(
        (
            await session.exec(
                select(AlertContent.household_id).where(AlertContent.alert_id == alert.id)
            )
        ).all()
    )
    pending = [h for h in households if h.id not in already_generated]

    contents = await content_generator.generate_for_zone(alert, pending)
    for household, content in zip(pending, contents, strict=True):
        session.add(
            AlertContent(
                alert_id=alert.id,
                household_id=household.id,
                channel=household.preferred_channel,
                generated_text=content.sms_text,
                generated_script=content.voice_script,
                video_caption_text=content.asl_video_caption,
                language=content.language_used,
            )
        )

    alert.status = AlertStatus.DISPATCHING.value
    session.add(alert)
    await session.commit()

    deliveries_started = await delivery_service.deliver_alert(session, alert, households)

    return AlertDispatchRead(
        alert_id=alert.id,
        status=AlertStatus(alert.status),
        households=len(households),
        content_generated=len(pending),
        deliveries_started=deliveries_started,
    )

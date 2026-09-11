"""AlertContent table model.

One row per household per alert: the Claude-generated rendering of an alert for
one household's language, literacy level, and channel. Content is regenerated
per alert rather than reused — an alert's facts are specific to that alert.

The column names follow the data model in docs/architecture.md; they map onto
the generation contract in ``app/schemas/alert_content.py`` as:

    generated_text     <- sms_text
    generated_script   <- voice_script
    video_caption_text <- asl_video_caption
    language           <- language_used
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class AlertContent(SQLModel, table=True):
    __tablename__ = "alert_content"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_id: uuid.UUID = Field(foreign_key="alert.id", index=True)
    household_id: uuid.UUID = Field(foreign_key="household.id", index=True)
    channel: str
    generated_text: str
    generated_script: str
    video_caption_text: str
    language: str
    # Timezone-aware for the same reason as Alert.created_at: every timestamp
    # this system emits is UTC with an offset.
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

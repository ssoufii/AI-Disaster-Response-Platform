"""Zone table model."""

import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Zone(SQLModel, table=True):
    __tablename__ = "zone"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True)
    # GeoJSON or a simple polygon/radius. Real geofencing is deferred — see
    # docs/architecture.md, Open Design Decisions.
    geo_boundary: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))

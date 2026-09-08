"""Request/response schemas for zones."""

import uuid

from pydantic import BaseModel, Field


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1)
    geo_boundary: dict | None = None


class ZoneRead(BaseModel):
    id: uuid.UUID
    name: str
    geo_boundary: dict | None = None

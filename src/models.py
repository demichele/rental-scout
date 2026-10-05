from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Listing(BaseModel):
    """Contrato de aviso. bedrooms = dormitorios; ambientes es independiente."""

    source: str
    external_id: str
    url: str
    title: str
    locality: str
    barrio_name: str | None = None
    is_gated: bool
    bedrooms: int | None = None
    ambientes: int | None = None
    bathrooms: float | None = None
    m2: float | None = None
    has_pool: bool
    currency: str
    price: float
    price_usd: float | None = None
    expenses: float | None = None
    property_type: str
    published_at: datetime | None = None
    photos: list[str] = Field(default_factory=list)
    lat: float | None = None
    lng: float | None = None
    raw_hash: str


NotificationKind = Literal["new", "price_drop"]

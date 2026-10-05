from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFS_PATH = PROJECT_ROOT / "prefs.yaml"
DEFAULT_SALE_PREFS_PATH = PROJECT_ROOT / "prefs.sale.yaml"

Operation = Literal["rent", "sale"]


class Prefs(BaseModel):
    locality: str
    gated_only: bool
    min_bedrooms: int
    min_ambientes: int = 5
    require_pool: bool
    property_type: str
    min_price_usd: int
    max_price_usd: int
    watch_max_price_usd: int
    min_drop_usd: int
    min_drop_pct: float = Field(gt=0, le=1)
    barrios: list[str] = Field(default_factory=list)
    localities: list[str] = Field(default_factory=list)
    enabled_adapters: list[str] = Field(default_factory=lambda: ["zonaprop"])
    operation: Operation = "rent"
    listing_source: str | None = None


def load_prefs(path: Path | None = None) -> Prefs:
    prefs_path = path or DEFAULT_PREFS_PATH
    with prefs_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Prefs.model_validate(data)


def load_sale_prefs(path: Path | None = None) -> Prefs | None:
    prefs_path = path or DEFAULT_SALE_PREFS_PATH
    if not prefs_path.is_file():
        return None
    return load_prefs(prefs_path)


def load_searches() -> list[Prefs]:
    """Alquiler y, si existe, venta. Cada hunt usa su propio Prefs."""
    searches = [load_prefs()]
    sale = load_sale_prefs()
    if sale is not None:
        searches.append(sale)
    return searches


def prefs_for_listing(listing_operation: Operation) -> Prefs:
    if listing_operation == "sale":
        sale = load_sale_prefs()
        if sale is not None:
            return sale
    return load_prefs()

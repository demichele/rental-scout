from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFS_PATH = PROJECT_ROOT / "prefs.yaml"


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
    enabled_adapters: list[str] = Field(default_factory=lambda: ["zonaprop"])


def load_prefs(path: Path | None = None) -> Prefs:
    prefs_path = path or DEFAULT_PREFS_PATH
    with prefs_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Prefs.model_validate(data)

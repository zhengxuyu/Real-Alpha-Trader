from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


@dataclass
class Factor:
    """Factor definition for ranking calculations"""
    id: str
    name: str
    description: str
    columns: List[Dict[str, Any]]
    compute: Callable[[Dict[str, pd.DataFrame], Optional[pd.DataFrame]], pd.DataFrame]
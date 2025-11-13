from typing import Optional

from pydantic import BaseModel


class AccountCreate(BaseModel):
    """Create a new AI Trading Account"""

    name: str  # Display name (e.g., "GPT Trader", "Claude Analyst")
    model: str = "gpt-4-turbo"
    base_url: str = "https://api.openai.com/v1"
    api_key: str
    binance_api_key: Optional[str] = None
    binance_secret_key: Optional[str] = None
    account_type: str = "AI"  # "AI" or "MANUAL"


class AccountUpdate(BaseModel):
    """Update AI Trading Account"""

    name: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    binance_api_key: Optional[str] = None
    binance_secret_key: Optional[str] = None


class AccountOut(BaseModel):
    """AI Trading Account output"""

    id: int
    user_id: int
    name: str
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # Will be masked in API responses
    binance_api_key: Optional[str] = None  # Will be masked in API responses
    binance_secret_key: Optional[str] = None  # Will be masked in API responses
    account_type: str
    is_active: bool
    # Balance fields - fetched from Binance in real-time, included for API compatibility
    initial_capital: float = 0.0  # Current balance from Binance (used as baseline)
    current_cash: float = 0.0  # Current balance from Binance
    frozen_cash: float = 0.0  # Always 0 - not tracked

    class Config:
        from_attributes = True


class AccountOverview(BaseModel):
    """Account overview with portfolio information"""

    account: AccountOut
    total_assets: float  # Total assets in USDT
    positions_value: float  # Total positions value in USDT


class StrategyConfigBase(BaseModel):
    """Base fields shared by strategy config schemas"""

    trigger_mode: str
    interval_seconds: Optional[int] = None
    tick_batch_size: Optional[int] = None
    enabled: bool = True


class StrategyConfigUpdate(StrategyConfigBase):
    """Incoming payload for updating strategy configuration"""

    pass


class StrategyConfig(StrategyConfigBase):
    """Strategy configuration response"""

    last_trigger_at: Optional[str] = None

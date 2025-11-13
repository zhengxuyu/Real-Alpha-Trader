"""
Broker Factory
Factory for creating broker instances based on account configuration.
Creates a new broker instance for each call to avoid state sharing issues.
"""

import logging
from typing import Optional

from database.models import Account

from .broker_interface import BrokerInterface
from .broker_binance import BinanceBroker

logger = logging.getLogger(__name__)

# Default broker implementation
_DEFAULT_BROKER: Optional[str] = "Binance"


def get_broker(account: Account) -> Optional[BrokerInterface]:
    """
    Get a new broker instance for the given account.
    Creates a fresh instance on each call to avoid state sharing issues.

    Args:
        account: Account object with broker configuration

    Returns:
        BrokerInterface instance, or None if broker is not available

    Note:
        Each call creates a new broker instance to ensure isolation
        between different operations and avoid potential state conflicts.
    """
    broker_type = getattr(account, "broker_type", None) or _DEFAULT_BROKER

    if broker_type == "Binance" or broker_type is None:
        # Use unified api_key and secret_key variable names
        api_key = account.binance_api_key
        secret_key = account.binance_secret_key
        if not api_key or not secret_key:
            logger.warning(f"Account {account.id} does not have Binance API keys configured")
            return None
        return BinanceBroker()

    # Future: Add other broker implementations here
    # elif broker_type == "Coinbase":
    #     return CoinbaseBroker()

    logger.error(f"Unsupported broker type: {broker_type} for account {account.id}")
    return None


def set_default_broker(broker_name: str) -> None:
    """
    Set the default broker type.

    Args:
        broker_name: Name of the broker (e.g., "Binance")
    """
    global _DEFAULT_BROKER
    _DEFAULT_BROKER = broker_name
    logger.info(f"Default broker set to: {broker_name}")

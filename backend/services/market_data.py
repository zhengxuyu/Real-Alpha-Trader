import logging
import time
from typing import Any, Dict, List, Tuple

from .hyperliquid_market_data import (
    get_all_symbols_from_hyperliquid,
    get_kline_data_from_hyperliquid,
    get_last_price_from_hyperliquid,
    get_market_status_from_hyperliquid,
    hyperliquid_client,
)

logger = logging.getLogger(__name__)


def get_last_price_with_timestamp(symbol: str, market: str = "CRYPTO") -> Tuple[float, float]:
    """Get latest price and its event timestamp.

    Returns:
        (price, timestamp_seconds)
    """
    key = f"{symbol}.{market}"

    # Import inside function to avoid circular imports
    from .price_cache import get_cached_price_with_timestamp, record_price_update

    # Prefer cached value when still valid
    cached = get_cached_price_with_timestamp(symbol, market)
    if cached is not None:
        price, ts = cached
        logger.debug(f"Using cached price for {key}: {price} @ {ts}")
        return price, ts

    logger.info(f"Getting real-time price for {key} from API...")

    try:
        price = get_last_price_from_hyperliquid(symbol)
        if price and price > 0:
            logger.info(f"Got real-time price for {key} from Hyperliquid: {price}")
            # Use current time as event timestamp for direct API fetches
            event_time = time.time()
            record_price_update(symbol, market, float(price), event_time)
            return float(price), event_time
        raise Exception(f"Hyperliquid returned invalid price: {price}")
    except Exception as hl_err:
        logger.error(f"Failed to get price from Hyperliquid: {hl_err}")
        raise Exception(f"Unable to get real-time price for {key}: {hl_err}")


def get_last_price(symbol: str, market: str = "CRYPTO") -> float:
    """Backwards-compatible helper that only returns the price."""
    price, _ = get_last_price_with_timestamp(symbol, market)
    return price


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100) -> List[Dict[str, Any]]:
    key = f"{symbol}.{market}"

    try:
        data = get_kline_data_from_hyperliquid(symbol, period, count)
        if data:
            logger.info(f"Got K-line data for {key} from Hyperliquid, total {len(data)} items")
            return data
        raise Exception("Hyperliquid returned empty K-line data")
    except Exception as hl_err:
        logger.error(f"Failed to get K-line data from Hyperliquid: {hl_err}")
        raise Exception(f"Unable to get K-line data for {key}: {hl_err}")


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    key = f"{symbol}.{market}"

    try:
        status = get_market_status_from_hyperliquid(symbol)
        logger.info(f"Retrieved market status for {key} from Hyperliquid: {status.get('market_status')}")
        return status
    except Exception as hl_err:
        logger.error(f"Failed to get market status: {hl_err}")
        raise Exception(f"Unable to get market status for {key}: {hl_err}")


def get_all_symbols() -> List[str]:
    """Get all available trading pairs"""
    try:
        symbols = get_all_symbols_from_hyperliquid()
        logger.info(f"Got {len(symbols)} trading pairs from Hyperliquid")
        return symbols
    except Exception as hl_err:
        logger.error(f"Failed to get trading pairs list: {hl_err}")
        return ["BTC/USD", "ETH/USD", "SOL/USD"]  # default trading pairs

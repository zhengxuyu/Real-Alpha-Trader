"""
Broker Adapter - Convenience functions for using broker interface
Provides backward-compatible wrapper functions that use the broker interface.
Includes async wrappers for use in async contexts to avoid blocking the event loop.
"""

import asyncio
import concurrent.futures
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from database.models import Account

from .broker_factory import get_broker

# Thread pool executor for running synchronous broker calls in async contexts
# This prevents blocking the async event loop
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="broker_executor")


def get_balance(account: Account) -> Optional[Decimal]:
    """
    Get account balance - uses broker interface.
    Uses get_balance_and_positions for efficiency (single API call).

    Args:
        account: Account object

    Returns:
        USD balance as Decimal, or None if unavailable
    """
    broker = get_broker(account)
    if not broker:
        return None
    balance, _ = broker.get_balance_and_positions(account)
    return balance


def get_positions(account: Account) -> List[Dict]:
    """
    Get account positions - uses broker interface.
    Uses get_balance_and_positions for efficiency (single API call).

    Args:
        account: Account object

    Returns:
        List of position dictionaries
    """
    broker = get_broker(account)
    if not broker:
        return []
    _, positions = broker.get_balance_and_positions(account)
    return positions


def get_balance_and_positions(account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
    """
    Get both balance and positions - uses broker interface.

    Args:
        account: Account object

    Returns:
        Tuple of (balance, positions)
    """
    broker = get_broker(account)
    if not broker:
        return None, []
    return broker.get_balance_and_positions(account)


def get_open_orders(account: Account) -> List[Dict]:
    """
    Get open orders - uses broker interface.

    Args:
        account: Account object

    Returns:
        List of open order dictionaries
    """
    broker = get_broker(account)
    if not broker:
        return []
    return broker.get_open_orders(account)


def get_closed_orders(account: Account, limit: int = 100) -> List[Dict]:
    """
    Get closed orders - uses broker interface.

    Args:
        account: Account object
        limit: Maximum number of orders to retrieve

    Returns:
        List of closed order dictionaries
    """
    broker = get_broker(account)
    if not broker:
        return []
    return broker.get_closed_orders(account, limit)


def execute_order(
    account: Account, symbol: str, side: str, quantity: float, price: float, ordertype: str = "market"
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Execute an order - uses broker interface.

    Args:
        account: Account object
        symbol: Trading symbol
        side: Order side ("BUY" or "SELL")
        quantity: Order quantity
        price: Order price
        ordertype: Order type ("market", "limit", etc.)

    Returns:
        Tuple of (success, order_id_or_error, result)
    """
    broker = get_broker(account)
    if not broker:
        return False, "Broker not available for account", None
    return broker.execute_order(account, symbol, side, quantity, price, ordertype)


def cancel_order(account: Account, order_id: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Cancel an order - uses broker interface.

    Args:
        account: Account object
        order_id: Broker's order ID

    Returns:
        Tuple of (success, error_message, result)
    """
    broker = get_broker(account)
    if not broker:
        return False, "Broker not available for account", None
    return broker.cancel_order(account, order_id)


# ============================================================================
# Async wrappers for use in async contexts (WebSocket, async API endpoints)
# These run synchronous broker calls in a thread pool to avoid blocking
# the async event loop
# ============================================================================


async def get_balance_async(account: Account) -> Optional[Decimal]:
    """Async wrapper for get_balance - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, get_balance, account)


async def get_positions_async(account: Account) -> List[Dict]:
    """Async wrapper for get_positions - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, get_positions, account)


async def get_balance_and_positions_async(account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
    """Async wrapper for get_balance_and_positions - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, get_balance_and_positions, account)


async def get_open_orders_async(account: Account) -> List[Dict]:
    """Async wrapper for get_open_orders - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, get_open_orders, account)


async def get_closed_orders_async(account: Account, limit: int = 100) -> List[Dict]:
    """Async wrapper for get_closed_orders - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, get_closed_orders, account, limit)


async def execute_order_async(
    account: Account, symbol: str, side: str, quantity: float, price: float, ordertype: str = "market"
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """Async wrapper for execute_order - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, execute_order, account, symbol, side, quantity, price, ordertype)


async def cancel_order_async(account: Account, order_id: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """Async wrapper for cancel_order - runs in thread pool to avoid blocking"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, cancel_order, account, order_id)

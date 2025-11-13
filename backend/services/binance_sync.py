"""
Binance Data Synchronization Service
Synchronizes account balance, positions, and orders from Binance API
"""

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from database.models import Account

logger = logging.getLogger(__name__)

# Binance API base URL
BINANCE_API_BASE_URL = "https://api.binance.com"

# Thread-safe cache for balance and positions
_cache_lock = threading.Lock()
_balance_positions_cache: Dict[str, tuple] = {}
_balance_positions_last_call_time: Dict[str, float] = {}

# Global rate limiter for all Binance API calls
_global_binance_last_call_time: float = 0.0
_global_binance_lock = threading.Lock()


def _generate_signature(query_string: str, secret_key: str) -> str:
    """Generate HMAC SHA256 signature for Binance API"""
    return hmac.new(secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_signed_request(
    api_key: str, secret_key: str, endpoint: str, params: Optional[Dict] = None, method: str = "GET"
) -> Dict:
    """
    Make a signed request to Binance API

    Args:
        api_key: Binance API key
        secret_key: Binance secret key
        endpoint: API endpoint (e.g., "/api/v3/account")
        params: Query parameters
        method: HTTP method (GET, POST, DELETE)

    Returns:
        Parsed JSON response
    """
    if params is None:
        params = {}

    # Add timestamp
    params["timestamp"] = int(time.time() * 1000)

    # Create query string
    query_string = urllib.parse.urlencode(params)

    # Generate signature
    signature = _generate_signature(query_string, secret_key)
    query_string += f"&signature={signature}"

    # Build URL
    url = f"{BINANCE_API_BASE_URL}{endpoint}?{query_string}"

    # Create request
    req = urllib.request.Request(url, method=method)
    req.add_header("X-MBX-APIKEY", api_key)

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            response_data = response.read().decode("utf-8")
            return json.loads(response_data)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            raise Exception(f"Binance API error: {error_data.get('msg', error_body)}")
        except Exception as parse_err:
            raise Exception(f"Binance API HTTP error {e.code}: {error_body} - {parse_err}")
    except Exception as e:
        raise Exception(f"Failed to make Binance API request: {str(e)}")


def _make_public_request(endpoint: str, params: Optional[Dict] = None) -> Dict:
    """
    Make a public (unsigned) request to Binance API

    Args:
        endpoint: API endpoint
        params: Query parameters

    Returns:
        Parsed JSON response
    """
    if params is None:
        params = {}

    query_string = urllib.parse.urlencode(params)
    url = f"{BINANCE_API_BASE_URL}{endpoint}?{query_string}" if query_string else f"{BINANCE_API_BASE_URL}{endpoint}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            response_data = response.read().decode("utf-8")
            return json.loads(response_data)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            raise Exception(f"Binance API error: {error_data.get('msg', error_body)}")
        except Exception as parse_err:
            raise Exception(f"Binance API HTTP error {e.code}: {error_body}")
    except Exception as e:
        raise Exception(f"Failed to make Binance API request: {str(e)}")


def _apply_rate_limiting() -> None:
    """Apply rate limiting for Binance API calls"""
    global _global_binance_last_call_time

    from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS

    current_time = time.time()
    min_interval = RATE_LIMIT_INTERVAL_SECONDS

    with _global_binance_lock:
        time_since_last_call = current_time - _global_binance_last_call_time

        if time_since_last_call < min_interval:
            sleep_time = min_interval - time_since_last_call
            logger.info(
                f"Rate limiting: sleeping {sleep_time:.2f}s before Binance API call (min interval: {min_interval}s)"
            )
            _global_binance_lock.release()
            try:
                time.sleep(sleep_time)
            finally:
                _global_binance_lock.acquire()
            current_time = time.time()

        _global_binance_last_call_time = current_time


def map_symbol_to_binance_pair(symbol: str) -> str:
    """
    Map internal symbol to Binance trading pair.

    Args:
        symbol: Internal trading symbol (e.g., "BTC", "ETH")

    Returns:
        Binance trading pair (e.g., "BTCUSDT", "ETHUSDT")
    """
    # Binance uses USDT as quote currency for most pairs
    return f"{symbol.upper()}USDT"


def get_binance_balance_and_positions(account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
    """
    Get both balance and positions from Binance in a single API call.
    Returns tuple of (balance: Optional[Decimal], positions: List[Dict]).
    Balance is in USDT, positions are all non-USDT assets.
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} (ID: {account.id}) does not have Binance API keys configured")
        return None, []

    # Cache mechanism
    import hashlib
    from services.trading_commands import CACHE_TTL_SECONDS

    api_key_hash = hashlib.md5(account.binance_api_key.encode()).hexdigest()[:8]
    cache_key = f"binance_{account.id}_{api_key_hash}"
    cache_ttl = CACHE_TTL_SECONDS

    current_time = time.time()

    # Thread-safe cache check
    with _cache_lock:
        if cache_key in _balance_positions_cache:
            cached_balance, cached_positions, cached_time = _balance_positions_cache[cache_key]
            if current_time - cached_time < cache_ttl:
                logger.debug(f"Using cached Binance balance and positions for account {account.id}")
                return cached_balance, cached_positions

        # Apply rate limiting
        _cache_lock.release()
        try:
            _apply_rate_limiting()
        finally:
            _cache_lock.acquire()

    try:
        # Get account information (includes balances)
        account_info = _make_signed_request(
            api_key=account.binance_api_key, secret_key=account.binance_secret_key, endpoint="/api/v3/account"
        )

        # Extract balances
        balances = account_info.get("balances", [])

        # Find USDT balance
        usdt_balance = Decimal("0")
        positions = []

        for balance_info in balances:
            asset = balance_info.get("asset", "")
            free = Decimal(balance_info.get("free", "0"))
            locked = Decimal(balance_info.get("locked", "0"))
            total = free + locked

            if asset == "USDT" or asset == "BUSD":
                usdt_balance += total
            elif total > Decimal("0"):
                # This is a position (non-zero balance in a non-stablecoin asset)
                positions.append(
                    {
                        "symbol": asset,
                        "quantity": total,
                        "available_quantity": free,
                        "avg_cost": Decimal("0"),  # Would need trade history to calculate
                    }
                )

        balance = usdt_balance if usdt_balance >= 0 else None

        # Thread-safe cache update
        with _cache_lock:
            _balance_positions_cache[cache_key] = (balance, positions, time.time())

        logger.debug(f"Fetched Binance balance: ${usdt_balance:.2f}, positions: {len(positions)}")
        return balance, positions

    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.error(
                f"Binance API authentication failed (401 Unauthorized) for account {account.name}. Please check if the API key and secret key are correct and have proper permissions."
            )
        elif e.code == 403:
            logger.error(f"Binance API forbidden (403) for account {account.name}. Please check API key permissions.")
        elif e.code == 451:
            logger.error(
                f"Binance API unavailable (451) for account {account.name}. Service unavailable from restricted location. Please check Binance terms of service."
            )
        else:
            logger.error(f"Binance API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return None, []
    except Exception as e:
        logger.error(f"Failed to get balance and positions from Binance for account {account.name}: {e}", exc_info=True)
        return None, []


def get_binance_open_orders(account: Account) -> List[Dict]:
    """
    Get open orders from Binance.
    Returns list of order dictionaries.
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} does not have Binance API keys configured")
        return []

    try:
        _apply_rate_limiting()

        # Get all open orders
        orders_data = _make_signed_request(
            api_key=account.binance_api_key, secret_key=account.binance_secret_key, endpoint="/api/v3/openOrders"
        )

        orders = []
        for order_info in orders_data:
            symbol = order_info.get("symbol", "")
            # Remove USDT suffix to get base asset
            base_symbol = symbol.replace("USDT", "").replace("BUSD", "")

            order_id = str(order_info.get("orderId", ""))
            side = order_info.get("side", "").upper()  # BUY or SELL
            order_type = order_info.get("type", "").upper()  # MARKET, LIMIT, etc.
            quantity = float(order_info.get("origQty", 0))
            price = float(order_info.get("price", 0))
            status = order_info.get("status", "").upper()

            orders.append(
                {
                    "order_id": order_id,
                    "symbol": base_symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "price": price if price > 0 else None,
                    "status": status,
                }
            )

        return orders
    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.error(f"Binance API authentication failed (401 Unauthorized) for account {account.name}.")
        elif e.code == 403:
            logger.error(f"Binance API forbidden (403) for account {account.name}.")
        else:
            logger.error(f"Binance API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to get open orders from Binance for account {account.name}: {e}", exc_info=True)
        return []


def get_binance_closed_orders(account: Account, limit: int = 100) -> List[Dict]:
    """
    Get closed/completed orders from Binance.
    Returns list of completed order dictionaries.
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} does not have Binance API keys configured")
        return []

    try:
        _apply_rate_limiting()

        # Get all orders (including filled and cancelled)
        all_orders_data = _make_signed_request(
            api_key=account.binance_api_key,
            secret_key=account.binance_secret_key,
            endpoint="/api/v3/allOrders",
            params={"limit": limit},
        )

        # Filter for filled orders only
        orders = []
        for order_info in all_orders_data:
            status = order_info.get("status", "").upper()
            if status not in ["FILLED", "PARTIALLY_FILLED"]:
                continue

            symbol = order_info.get("symbol", "")
            base_symbol = symbol.replace("USDT", "").replace("BUSD", "")

            order_id = str(order_info.get("orderId", ""))
            side = order_info.get("side", "").upper()
            price = float(order_info.get("price", 0))
            quantity = float(order_info.get("executedQty", 0))
            cost = float(order_info.get("cummulativeQuoteQty", 0))
            # Fee is not directly available in allOrders, would need to get from trades
            fee = 0.0

            # Get close time
            close_time = int(order_info.get("updateTime", order_info.get("time", 0)))

            orders.append(
                {
                    "order_id": order_id,
                    "symbol": base_symbol,
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "cost": cost,
                    "fee": fee,
                    "status": "FILLED",
                    "close_time": close_time,
                }
            )

        # Sort by close time descending (most recent first)
        orders.sort(key=lambda x: x.get("close_time", 0), reverse=True)
        return orders[:limit]

    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.error(f"Binance API authentication failed (401 Unauthorized) for account {account.name}.")
        elif e.code == 403:
            logger.error(f"Binance API forbidden (403) for account {account.name}.")
        else:
            logger.error(f"Binance API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to get closed orders from Binance for account {account.name}: {e}", exc_info=True)
        return []


def execute_binance_order(
    api_key: str, secret_key: str, symbol: str, side: str, quantity: float, price: float, ordertype: str = "market"
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Execute an order on Binance.

    Args:
        api_key: Binance API key
        secret_key: Binance secret key
        symbol: Trading symbol (e.g., "BTC", "ETH")
        side: Order side ("BUY" or "SELL")
        quantity: Order quantity
        price: Order price (required for LIMIT orders, ignored for MARKET orders)
        ordertype: Order type ("market", "limit", etc.)

    Returns:
        Tuple of (success: bool, error_message_or_order_id: Optional[str], result: Optional[Dict])
    """
    if not api_key or not secret_key:
        return False, "Binance API keys not configured", None

    try:
        _apply_rate_limiting()

        # Map symbol to Binance pair
        pair = map_symbol_to_binance_pair(symbol)

        # Prepare order parameters
        # Map order type: "market" -> "MARKET", "limit" -> "LIMIT"
        order_type_upper = ordertype.upper()
        if order_type_upper == "MARKET":
            binance_type = "MARKET"
        elif order_type_upper == "LIMIT":
            binance_type = "LIMIT"
        else:
            # Default to MARKET if unknown
            logger.warning(f"Unknown order type {ordertype}, defaulting to MARKET")
            binance_type = "MARKET"

        # Adjust quantity to comply with Binance LOT_SIZE filter
        # For BTC/USDT, stepSize is typically 0.00001 (5 decimal places)
        # We need to round down to the nearest valid step size
        # Common step sizes: BTC=0.00001, ETH=0.0001, SOL=0.01, BNB=0.001, XRP=1, DOGE=1
        step_size_map = {
            "BTC": 0.00001,
            "ETH": 0.0001,
            "SOL": 0.01,
            "BNB": 0.001,
            "XRP": 1.0,
            "DOGE": 1.0,
        }

        # Minimum NOTIONAL (order value) requirements in USDT
        # Binance typically requires minimum 10 USDT for most pairs
        min_notional_map = {
            "BTC": 10.0,
            "ETH": 10.0,
            "SOL": 10.0,
            "BNB": 10.0,
            "XRP": 10.0,
            "DOGE": 10.0,
        }

        step_size = step_size_map.get(symbol.upper(), 0.00001)  # Default to BTC step size
        min_notional = min_notional_map.get(symbol.upper(), 10.0)  # Default to 10 USDT

        # Check if order value meets minimum NOTIONAL requirement
        estimated_notional = quantity * price
        if estimated_notional < min_notional:
            return (
                False,
                f"Order value {estimated_notional:.2f} USDT is below minimum {min_notional} USDT for {symbol}",
                None,
            )

        # Round down to nearest step size
        quantity_adjusted = (quantity // step_size) * step_size

        if quantity_adjusted <= 0:
            return (
                False,
                f"Adjusted quantity {quantity_adjusted} is too small (original: {quantity}, stepSize: {step_size})",
                None,
            )

        # Re-check NOTIONAL after quantity adjustment
        adjusted_notional = quantity_adjusted * price
        if adjusted_notional < min_notional:
            # If adjusted quantity doesn't meet minimum, round up to meet minimum requirement
            min_quantity_needed = (min_notional / price) // step_size * step_size
            # Add one more step to ensure we meet minimum
            min_quantity_needed = min_quantity_needed + step_size
            min_notional_check = min_quantity_needed * price

            if min_notional_check >= min_notional:
                logger.info(
                    f"Adjusting quantity from {quantity_adjusted:.8f} to {min_quantity_needed:.8f} "
                    f"to meet minimum order value requirement"
                )
                quantity_adjusted = min_quantity_needed
            else:
                return (
                    False,
                    f"Adjusted order value {adjusted_notional:.2f} USDT is below minimum {min_notional} USDT for {symbol}",
                    None,
                )

        # Format quantity as string to avoid scientific notation
        # Binance requires quantity in format: '^([0-9]{1,20})(\.[0-9]{1,20})?$'
        # Use format to remove trailing zeros and avoid scientific notation
        quantity_str = f"{quantity_adjusted:.10f}".rstrip("0").rstrip(".")

        # Ensure we have at least one digit after decimal if fractional
        if "." in quantity_str and quantity_str.split(".")[-1] == "":
            quantity_str = quantity_str.rstrip(".")

        params = {
            "symbol": pair,
            "side": side.upper(),  # BUY or SELL
            "type": binance_type,
            "quantity": quantity_str,
        }

        # Add price and timeInForce for LIMIT orders
        if binance_type == "LIMIT":
            # Format price as string to avoid scientific notation
            price_str = f"{price:.20f}".rstrip("0").rstrip(".")
            params["price"] = price_str
            params["timeInForce"] = "GTC"  # Good Till Cancel

        logger.info(
            f"Placing Binance order: {side} {quantity_str} {symbol} @ {price} (pair={pair}, ordertype={ordertype})"
        )

        # Execute order
        result = _make_signed_request(
            api_key=api_key, secret_key=secret_key, endpoint="/api/v3/order", params=params, method="POST"
        )

        # Check for errors (Binance returns error response with "code" and "msg" fields on error)
        if "code" in result:
            error_msg = result.get("msg", "Unknown error")
            logger.error(f"Binance API error: {error_msg}")
            return False, error_msg, result

        # Extract order ID
        order_id = str(result.get("orderId", ""))
        if order_id:
            logger.info(
                f"Binance order placed successfully: orderId={order_id}, pair={pair}, side={side}, quantity={quantity}"
            )
            return True, order_id, result
        else:
            logger.warning(f"Binance order response missing orderId: {result}")
            return False, "Missing order ID in response", result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            error_msg = error_data.get("msg", error_body)
        except Exception as parse_err:
            error_msg = f"HTTP error {e.code}: {error_body}"
        logger.error(f"Failed to execute Binance order: {error_msg}", exc_info=True)
        return False, error_msg, None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to execute Binance order: {error_msg}", exc_info=True)
        return False, error_msg, None


def cancel_binance_order(
    api_key: str, secret_key: str, order_id: str, symbol: str
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Cancel an order on Binance.

    Args:
        api_key: Binance API key
        secret_key: Binance secret key
        order_id: Order ID to cancel
        symbol: Trading symbol (e.g., "BTC", "ETH") - needed to build trading pair

    Returns:
        Tuple of (success: bool, error_message: Optional[str], result: Optional[Dict])
    """
    if not api_key or not secret_key:
        return False, "Binance API keys not configured", None

    try:
        _apply_rate_limiting()

        # Map symbol to Binance pair
        pair = map_symbol_to_binance_pair(symbol)

        params = {
            "symbol": pair,
            "orderId": order_id,
        }

        logger.info(f"Cancelling Binance order: orderId={order_id}, pair={pair}")

        result = _make_signed_request(
            api_key=api_key, secret_key=secret_key, endpoint="/api/v3/order", params=params, method="DELETE"
        )

        # Check for errors (Binance returns error response with "code" and "msg" fields on error)
        if "code" in result:
            error_msg = result.get("msg", "Unknown error")
            logger.error(f"Binance API error: {error_msg}")
            return False, error_msg, result

        logger.info(f"Binance order cancelled successfully: orderId={order_id}")
        return True, None, result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            error_msg = error_data.get("msg", error_body)
        except Exception as parse_err:
            error_msg = f"HTTP error {e.code}: {error_body} - {parse_err}"
        logger.error(f"Failed to cancel Binance order: {error_msg}", exc_info=True)
        return False, error_msg, None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to cancel Binance order: {error_msg}", exc_info=True)
        return False, error_msg, None

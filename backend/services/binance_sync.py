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
from datetime import datetime, timezone

from database.models import Account

logger = logging.getLogger(__name__)

# Binance API base URL
BINANCE_API_BASE_URL = "https://api.binance.com"

# Thread-safe cache for balance and positions
_cache_lock = threading.Lock()
_balance_positions_cache: Dict[str, tuple] = {}
_balance_positions_last_call_time: Dict[str, float] = {}


def clear_balance_cache(account: Optional[Account] = None) -> None:
    """
    Clear cached balance and positions data.
    
    Args:
        account: If provided, clears cache only for this account.
                 If None, clears cache for all accounts.
    """
    global _balance_positions_cache
    
    with _cache_lock:
        if account is None:
            # Clear all cached data
            _balance_positions_cache.clear()
            logger.info("Cleared all balance and positions cache")
        else:
            # Clear cache for specific account
            # Need to match the cache key generation logic used in get_binance_balance_and_positions
            if not account.binance_api_key:
                logger.debug(f"Account {account.id} ({account.name}) has no Binance API key, nothing to clear")
                return
            
            import hashlib
            api_key_hash = hashlib.md5(account.binance_api_key.encode()).hexdigest()[:8]
            cache_key = f"binance_{account.id}_{api_key_hash}"
            if cache_key in _balance_positions_cache:
                old_balance, _, _ = _balance_positions_cache[cache_key]
                del _balance_positions_cache[cache_key]
                logger.info(
                    f"[BALANCE_UPDATE] Cleared balance cache for account {account.id} ({account.name}), "
                    f"previous cached balance: ${float(old_balance):.2f} USDT"
                )
            else:
                logger.info(
                    f"[BALANCE_UPDATE] No cache found for account {account.id} ({account.name}) to clear"
                )

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
            cache_age = current_time - cached_time
            if cache_age < cache_ttl:
                logger.info(
                    f"[BALANCE_UPDATE] Using cached balance for account {account.id} ({account.name}): "
                    f"${float(cached_balance):.2f} USDT, cache age: {cache_age:.2f}s"
                )
                return cached_balance, cached_positions
            else:
                logger.info(
                    f"[BALANCE_UPDATE] Cache expired for account {account.id} ({account.name}), "
                    f"cache age: {cache_age:.2f}s, fetching fresh data from Binance"
                )

        # Apply rate limiting
        _cache_lock.release()
        try:
            _apply_rate_limiting()
        finally:
            _cache_lock.acquire()

    try:
        logger.info(
            f"[BALANCE_UPDATE] Fetching balance from Binance API for account {account.id} ({account.name})"
        )
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
            # Check if balance changed
            old_balance = None
            if cache_key in _balance_positions_cache:
                old_balance, _, _ = _balance_positions_cache[cache_key]
            
            _balance_positions_cache[cache_key] = (balance, positions, time.time())
            
            # Log balance update
            if old_balance is not None and old_balance != balance:
                balance_change = float(balance) - float(old_balance)
                logger.info(
                    f"[BALANCE_UPDATE] Balance updated for account {account.id} ({account.name}): "
                    f"${float(old_balance):.2f} → ${float(balance):.2f} USDT "
                    f"(change: ${balance_change:+.2f} USDT), positions: {len(positions)}"
                )
            else:
                logger.info(
                    f"[BALANCE_UPDATE] Balance fetched for account {account.id} ({account.name}): "
                    f"${float(balance):.2f} USDT, positions: {len(positions)}"
                )
        
        return balance, positions

    except urllib.error.HTTPError as e:
        # Clear cache on API failure to avoid returning stale data
        with _cache_lock:
            if cache_key in _balance_positions_cache:
                old_balance, _, _ = _balance_positions_cache[cache_key]
                del _balance_positions_cache[cache_key]
                logger.warning(
                    f"[BALANCE_UPDATE] API error for account {account.id} ({account.name}), "
                    f"cleared cache (previous balance: ${float(old_balance):.2f} USDT), error code: {e.code}"
                )
            else:
                logger.warning(
                    f"[BALANCE_UPDATE] API error for account {account.id} ({account.name}), "
                    f"no cache to clear, error code: {e.code}"
                )
        
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
        # Clear cache on exception to avoid returning stale data
        with _cache_lock:
            if cache_key in _balance_positions_cache:
                old_balance, _, _ = _balance_positions_cache[cache_key]
                del _balance_positions_cache[cache_key]
                logger.warning(
                    f"[BALANCE_UPDATE] Exception for account {account.id} ({account.name}), "
                    f"cleared cache (previous balance: ${float(old_balance):.2f} USDT), error: {str(e)}"
                )
            else:
                logger.warning(
                    f"[BALANCE_UPDATE] Exception for account {account.id} ({account.name}), "
                    f"no cache to clear, error: {str(e)}"
                )
        
        logger.error(f"[BALANCE_UPDATE] Failed to get balance and positions from Binance for account {account.name}: {e}", exc_info=True)
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


def calculate_avg_cost_from_trades(account: Account, symbol: str) -> Optional[float]:
    """
    Calculate average cost from Binance trade history for a specific symbol.
    Uses FIFO (First In First Out) method: starts from current position, traces back through trades
    to find which BUY trades contribute to the current position.
    
    Args:
        account: Account to calculate avg_cost for
        symbol: Symbol to calculate avg_cost for (e.g., "BTC", "XRP")
        
    Returns:
        Average cost as float, or None if cannot be calculated
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} does not have Binance API keys configured")
        return None
    
    try:
        # First, get current actual position from Binance
        _, positions_data = get_binance_balance_and_positions(account)
        current_position_qty = Decimal("0")
        for pos in positions_data:
            if (pos.get("symbol") or "").upper() == symbol.upper():
                current_position_qty = Decimal(str(pos.get("quantity", 0)))
                break
        
        if current_position_qty <= 0:
            logger.debug(f"Account {account.name} has no active position for {symbol} on Binance")
            return None
        
        trading_pair = map_symbol_to_binance_pair(symbol)
        _apply_rate_limiting()
        
        # Get trade history from Binance (sorted by time, oldest first)
        trades_data = _make_signed_request(
            api_key=account.binance_api_key,
            secret_key=account.binance_secret_key,
            endpoint="/api/v3/myTrades",
            params={"symbol": trading_pair, "limit": 1000},  # Get recent 1000 trades
        )
        
        if not trades_data or len(trades_data) == 0:
            logger.debug(f"Account {account.name} has no trade history for {symbol}")
            return None
        
        # Reverse trades to process from most recent to oldest
        # We'll trace back from current position to find which BUY trades contribute
        trades_reversed = list(reversed(trades_data))
        
        # Track remaining position to account for (FIFO: sell oldest first)
        remaining_qty = current_position_qty
        contributing_buys = []  # BUY trades that contribute to current position
        
        # Process from most recent to oldest
        for trade_info in trades_reversed:
            is_buyer = trade_info.get("isBuyer", True)
            qty = Decimal(str(trade_info.get("qty", "0")))
            price = Decimal(str(trade_info.get("price", "0")))
            
            if is_buyer:
                # BUY trade: if we still need to account for position, this BUY contributes
                if remaining_qty > 0:
                    # This BUY contributes to current position
                    # Amount contributed is min(remaining_qty, qty)
                    contributed_qty = min(remaining_qty, qty)
                    contributing_buys.append({
                        "price": price,
                        "qty": contributed_qty,
                    })
                    remaining_qty -= contributed_qty
                    
                    if remaining_qty <= 0:
                        # We've accounted for all current position
                        break
            else:
                # SELL trade: when going backwards, SELL increases what we need to account for
                # (because we're reversing the sell)
                remaining_qty += qty
        
        if not contributing_buys:
            logger.debug(f"Account {account.name} could not trace current position {symbol} back to BUY trades")
            return None
        
        # Calculate weighted average cost from contributing BUY trades
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        
        for buy in contributing_buys:
            total_cost += buy["price"] * buy["qty"]
            total_qty += buy["qty"]
        
        if total_qty > 0:
            avg_cost = float(total_cost / total_qty)
            logger.info(
                f"Calculated avg_cost for {account.name} {symbol} using FIFO: "
                f"${avg_cost:.6f} (from {len(contributing_buys)} BUY trades, "
                f"total qty: {float(total_qty):.8f}, current position: {float(current_position_qty):.8f})"
            )
            return avg_cost
        else:
            logger.debug(f"Account {account.name} has no valid BUY trades contributing to {symbol} position")
            return None
            
    except Exception as e:
        logger.warning(f"Failed to calculate avg_cost from trades for {account.name} {symbol}: {e}", exc_info=True)
        return None


def get_binance_trade_history(account: Account, symbol: Optional[str] = None, limit: int = 1000) -> List[Dict]:
    """
    Get trade history from Binance for all symbols or a specific symbol.
    Returns list of trade dictionaries with trade details.
    
    Args:
        account: Account to get trades for
        symbol: Optional symbol to filter (e.g., "BTC", "XRP"). If None, gets all trades.
        limit: Maximum number of trades to return per symbol
        
    Returns:
        List of trade dictionaries with keys: symbol, side, price, quantity, commission, trade_time, etc.
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} does not have Binance API keys configured")
        return []
    
    all_trades = []
    
    # Supported symbols for trading
    symbols_to_check = [symbol] if symbol else ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
    
    # For multiple symbols, reduce limit per symbol to avoid too many API calls
    # If querying all symbols, get fewer trades per symbol to stay within overall limit
    per_symbol_limit = limit if symbol else min(limit // len(symbols_to_check), 100)
    if per_symbol_limit < 1:
        per_symbol_limit = 1
    
    try:
        for sym in symbols_to_check:
            try:
                trading_pair = map_symbol_to_binance_pair(sym)
                _apply_rate_limiting()
                
                # Get trade history from Binance
                trades_data = _make_signed_request(
                    api_key=account.binance_api_key,
                    secret_key=account.binance_secret_key,
                    endpoint="/api/v3/myTrades",
                    params={"symbol": trading_pair, "limit": per_symbol_limit},
                )
                
                if not trades_data:
                    continue
                
                # Convert Binance trade format to our format
                for trade_info in trades_data:
                    is_buyer = trade_info.get("isBuyer", True)
                    side = "BUY" if is_buyer else "SELL"
                    
                    # Parse trade time
                    trade_time_ms = trade_info.get("time", 0)
                    trade_time = datetime.fromtimestamp(trade_time_ms / 1000.0, tz=timezone.utc) if trade_time_ms else None
                    
                    # Get commission (usually in quote currency or base currency)
                    commission_asset = trade_info.get("commissionAsset", "")
                    commission_amount = float(trade_info.get("commission", 0))
                    
                    # Convert commission to USDT if needed (simplified - assumes commission is in USDT or base asset)
                    commission = commission_amount
                    if commission_asset != "USDT" and commission_asset:
                        # If commission is in base asset, estimate value (rough approximation)
                        price = float(trade_info.get("price", 0))
                        if commission_asset == sym:
                            commission = commission_amount * price
                    
                    all_trades.append({
                        "symbol": sym,
                        "side": side,
                        "price": float(trade_info.get("price", 0)),
                        "quantity": float(trade_info.get("qty", 0)),
                        "commission": commission,
                        "trade_time": trade_time,
                        "order_id": trade_info.get("orderId"),
                        "trade_id": trade_info.get("id"),
                    })
                
            except Exception as sym_err:
                logger.debug(f"Failed to get trades for {sym} from Binance: {sym_err}")
                continue
        
        # Sort by trade_time descending (most recent first)
        min_datetime = datetime.min.replace(tzinfo=timezone.utc)
        all_trades.sort(key=lambda x: x.get("trade_time") or min_datetime, reverse=True)
        # Limit total trades (not per symbol, but overall)
        return all_trades[:limit]
        
    except Exception as e:
        logger.error(f"Failed to get trade history from Binance for account {account.name}: {e}", exc_info=True)
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

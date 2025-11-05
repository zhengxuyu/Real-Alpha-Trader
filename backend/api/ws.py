import asyncio
import json
import logging
import threading
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

from database.connection import SessionLocal
from database.models import Account, AIDecisionLog, CryptoPrice, Order, Position, Trade, User
from fastapi import WebSocket, WebSocketDisconnect
from repositories.account_repo import get_account, get_or_create_default_account
from repositories.order_repo import list_orders
from repositories.position_repo import list_positions
from repositories.user_repo import get_or_create_user, get_user
from services.asset_calculator import calc_positions_value
from services.asset_curve_calculator import get_all_asset_curves_data_new
from services.broker_adapter import get_balance_and_positions, get_open_orders
from services.market_data import get_last_price
from services.order_matching import create_order
from services.scheduler import add_account_snapshot_job, remove_account_snapshot_job
from sqlalchemy.orm import Session


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        pass  # WebSocket is already accepted in the endpoint

    def register(self, account_id: Optional[int], websocket: WebSocket):
        if account_id is not None:
            self.active_connections.setdefault(account_id, set()).add(websocket)
            # Add scheduled snapshot task for new account (30 seconds to avoid Binance API rate limits)
            add_account_snapshot_job(account_id, interval_seconds=30)

    def unregister(self, account_id: Optional[int], websocket: WebSocket):
        if account_id is not None and account_id in self.active_connections:
            self.active_connections[account_id].discard(websocket)
            if not self.active_connections[account_id]:
                del self.active_connections[account_id]
                # Remove the scheduled task for this account
                remove_account_snapshot_job(account_id)

    async def send_to_account(self, account_id: int, message: dict):
        """Send message to all WebSocket connections for an account.

        Handles connection cleanup automatically when connections are closed.
        """
        if account_id not in self.active_connections:
            logger.debug(f"No active connections for account {account_id}")
            return

        try:
            payload = json.dumps(message, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to serialize message for account {account_id}: {e}")
            return

        # Make a copy of the list to avoid modification during iteration
        connections_to_remove = []
        for ws in list(self.active_connections[account_id]):
            try:
                # Send message - FastAPI WebSocket will raise exception if connection is closed
                await ws.send_text(payload)
            except (RuntimeError, ConnectionError, WebSocketDisconnect, OSError) as e:
                # Connection is closed or error occurred - silently remove (expected behavior)
                connections_to_remove.append(ws)
            except Exception as e:
                # Check if it's a connection-related exception by name
                exc_name = type(e).__name__
                if exc_name in ("ClientDisconnected", "ConnectionClosedError", "ConnectionClosedOK"):
                    # Connection closed exceptions - silently remove
                    connections_to_remove.append(ws)
                else:
                    # Other unexpected errors - log at debug level without stack trace
                    logger.debug(f"Unexpected error sending to WebSocket (account {account_id}): {exc_name}: {e}")
                    connections_to_remove.append(ws)

        # Remove closed connections
        if connections_to_remove:
            for ws in connections_to_remove:
                self.active_connections[account_id].discard(ws)
            # Clean up empty account entry
            if not self.active_connections[account_id]:
                del self.active_connections[account_id]

    async def broadcast_to_all(self, message: dict):
        """Broadcast message to all connected clients"""
        try:
            payload = json.dumps(message, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to serialize broadcast message: {e}")
            return

        for account_id, websockets in list(self.active_connections.items()):
            for ws in list(websockets):
                try:
                    # Check if WebSocket is still open before sending
                    if ws.client_state.name != "CONNECTED":
                        websockets.discard(ws)
                        continue
                    await ws.send_text(payload)
                except (WebSocketDisconnect, RuntimeError, ConnectionError, OSError) as e:
                    # Client disconnected or connection error - silently remove connection
                    # These are expected when clients disconnect and don't need logging
                    websockets.discard(ws)
                except Exception as e:
                    # Check if it's a connection-related exception by name
                    exc_name = type(e).__name__
                    if exc_name in ("ClientDisconnected", "ConnectionClosedError", "ConnectionClosedOK"):
                        # Connection closed exceptions - silently remove
                        websockets.discard(ws)
                    else:
                        # Other unexpected errors - log at debug level without stack trace
                        logger.debug(
                            f"Failed to broadcast message to WebSocket (account {account_id}): {exc_name}: {e}"
                        )
                        websockets.discard(ws)

    def has_connections(self) -> bool:
        return any(self.active_connections.values())

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        if loop and loop.is_running():
            self._loop = loop

    def schedule_task(self, coro):
        """Schedule an async coroutine to run in the event loop.

        Args:
            coro: Coroutine object to execute
        """
        if not asyncio.iscoroutine(coro):
            logger.error(f"schedule_task called with non-coroutine: {type(coro)}, value: {coro}")
            return

        loop = None
        if self._loop and self._loop.is_running() and not self._loop.is_closed():
            loop = self._loop
        else:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running() and not loop.is_closed():
                    self._loop = loop
                else:
                    loop = None
            except RuntimeError:
                loop = None

        if loop and loop.is_running() and not loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)

                # Handle future exceptions - this is critical for error propagation
                def _handle_future_exception(fut):
                    try:
                        fut.result()  # This will raise if the coro raised an exception
                    except Exception as e:
                        logger.error(f"Scheduled task failed: {type(e).__name__}: {e}", exc_info=True)

                future.add_done_callback(_handle_future_exception)
            except Exception as e:
                logger.error(f"Failed to schedule coroutine in event loop: {type(e).__name__}: {e}", exc_info=True)
                # Fall through to thread-based execution
                loop = None

        if not loop:
            # Fallback: run in a dedicated daemon thread to avoid blocking the caller
            def _run():
                try:
                    asyncio.run(coro)
                except Exception as e:
                    logger.error(f"Failed to run coroutine in thread: {type(e).__name__}: {e}", exc_info=True)

            threading.Thread(target=_run, daemon=True).start()


manager = ConnectionManager()


async def broadcast_asset_curve_update(timeframe: str = "1h"):
    """Broadcast asset curve updates to all connected clients"""
    db = SessionLocal()
    try:
        asset_curves = get_all_asset_curves_data(db, timeframe)
        await manager.broadcast_to_all({"type": "asset_curve_update", "timeframe": timeframe, "data": asset_curves})
    except Exception as e:
        logging.error(f"Failed to broadcast asset curve update: {e}")
    finally:
        db.close()


async def broadcast_arena_asset_update(update_payload: dict):
    """Broadcast aggregated arena asset update to all connected clients"""
    message = {
        "type": "arena_asset_update",
        **update_payload,
    }
    await manager.broadcast_to_all(message)


async def broadcast_trade_update(trade_data: dict):
    """Broadcast trade update to specific account when trade is executed

    Args:
        trade_data: Dictionary containing trade information including account_id
    """
    account_id = trade_data.get("account_id")
    if not account_id:
        logging.warning("broadcast_trade_update called without account_id")
        return

    try:
        await manager.send_to_account(account_id, {"type": "trade_update", "trade": trade_data})
    except Exception as e:
        logging.error(f"Failed to broadcast trade update: {e}")


async def broadcast_position_update(account_id: int, positions_data: list):
    """Broadcast position update to specific account when positions change

    Args:
        account_id: Account ID to send update to
        positions_data: List of position dictionaries
    """
    try:
        await manager.send_to_account(account_id, {"type": "position_update", "positions": positions_data})
    except Exception as e:
        logging.error(f"Failed to broadcast position update: {e}")


async def broadcast_model_chat_update(decision_data: dict):
    """Broadcast AI decision update to specific account

    Args:
        decision_data: Dictionary containing AI decision information including account_id
    """
    account_id = decision_data.get("account_id")
    if not account_id:
        logger.warning("broadcast_model_chat_update called without account_id")
        return

    try:
        await manager.send_to_account(account_id, {"type": "model_chat_update", "decision": decision_data})
    except Exception as e:
        # Only log if it's not just because there are no active connections
        if account_id in manager.active_connections:
            logger.error(
                f"Failed to broadcast model chat update (account_id={account_id}): {type(e).__name__}: {e}",
                exc_info=True,
            )


def get_all_asset_curves_data(db: Session, timeframe: str = "1h"):
    """Get timeframe-based asset curve data for all accounts - WebSocket version

    Uses the new algorithm that draws curves by accounts and creates all-time lists.

    Args:
        timeframe: Time period for the curve, options: "5m", "1h", "1d"
    """
    return get_all_asset_curves_data_new(db, timeframe)


async def _send_snapshot_optimized(db: Session, account_id: int):
    """
    Optimized snapshot: Read all data from database cache instead of calling Binance API.
    Broker data is synced to database by background task every 30 seconds.
    """
    # The db parameter should already be from the correct database (real or paper)
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            logging.warning(f"[SNAPSHOT] Account {account_id} not found in database for snapshot")
            return

        # Get balance from cache (updated by broker_data_sync task)
        from services.broker_data_sync import AccountBalanceCache
        balance = AccountBalanceCache.get_balance(account_id)
        if balance is None:
            # If balance not in cache yet, try to get from Binance directly (fallback)
            logger.warning(f"[SNAPSHOT] Balance not in cache for account {account_id}, fetching from Binance as fallback")
            try:
                from services.broker_adapter import get_balance_and_positions
                balance, _ = get_balance_and_positions(account)
            except Exception as binance_err:
                logger.debug(f"[SNAPSHOT] Failed to get balance from Binance fallback: {binance_err}")
                balance = None
        current_cash = float(balance) if balance is not None else 0.0
    except Exception as e:
        logging.error(f"[SNAPSHOT] Error getting account or balance for {account_id}: {e}", exc_info=True)
        # Return early - don't send incomplete snapshot
        return

    logger.debug(f"_send_snapshot_optimized: account_id={account_id}, cash=${current_cash:.2f}")
    logging.info(f"[SNAPSHOT] Sending snapshot for account {account_id}, cash=${current_cash:.2f}")

    # Get positions from database (synced by broker_data_sync task)
    db_positions = db.query(Position).filter(
        Position.account_id == account_id,
        Position.market == "CRYPTO"
    ).all()
    
    # Convert database positions to format expected by frontend
    positions = [
        {
            "id": i,
            "account_id": account_id,
            "symbol": pos.symbol,
            "name": pos.name,
            "market": "CRYPTO",
            "quantity": float(pos.quantity),
            "available_quantity": float(pos.available_quantity),
            "avg_cost": float(pos.avg_cost),
        }
        for i, pos in enumerate(db_positions) if float(pos.quantity) > 0
    ]

    # Get orders from database (synced by broker_data_sync task)
    db_orders = db.query(Order).filter(
        Order.account_id == account_id,
        Order.market == "CRYPTO",
        Order.status.in_(["PENDING", "PARTIALLY_FILLED", "NEW"])
    ).all()
    
    # Convert database orders to format expected by frontend
    orders = [
        {
            "id": order.id,
            "account_id": account_id,
            "order_no": order.order_no,
            "symbol": order.symbol,
            "name": order.name,
            "market": "CRYPTO",
            "side": order.side,
            "order_type": order.order_type,
            "price": float(order.price) if order.price else None,
            "quantity": float(order.quantity),
            "filled_quantity": float(order.filled_quantity),
            "status": order.status,
        }
        for order in db_orders
    ]

    # Trades are fetched on-demand via REST API (/api/arena/trades)
    # Not included in snapshot to avoid expensive Binance API calls
    trades = []
    ai_decisions = (
        db.query(AIDecisionLog)
        .filter(AIDecisionLog.account_id == account_id)
        .order_by(AIDecisionLog.decision_time.desc())
        .limit(10)
        .all()
    )

    # Calculate positions value from database data
    positions_value = 0.0
    for pos in positions:
        try:
            price = get_last_price(pos["symbol"], "CRYPTO")
            if price:
                positions_value += float(price) * float(pos["quantity"])
        except Exception:
            pass

    overview = {
        "account": {
            "id": account.id,
            "user_id": account.user_id,
            "name": account.name,
            "account_type": account.account_type,
            "initial_capital": current_cash,  # Use current balance as baseline for return calculation
            "current_cash": current_cash,
            "frozen_cash": 0.0,  # Not tracked - all data from Binance
        },
        "total_assets": positions_value + current_cash,
        "positions_value": positions_value,
    }

    # Enrich positions with latest price and market value
    enriched_positions = []
    price_error_message = None

    # Group positions by symbol to reduce API calls
    unique_symbols = set((p["symbol"], p["market"]) for p in positions)
    price_cache = {}

    # Fetch all unique prices in one go
    for symbol, market in unique_symbols:
        try:
            price = get_last_price(symbol, market)
            price_cache[(symbol, market)] = price
        except Exception as e:
            price_cache[(symbol, market)] = None
            error_msg = str(e)
            if "cookie" in error_msg.lower() and price_error_message is None:
                price_error_message = error_msg

    for p in positions:
        price = price_cache.get((p["symbol"], p["market"]))
        quantity = float(p["quantity"])
        avg_cost = float(p["avg_cost"])
        
        # Calculate market value and unrealized P&L
        market_value = None
        unrealized_pnl = None
        current_price = None
        notional = 0.0
        
        if price is not None:
            current_price = float(price)
            market_value = current_price * quantity
            
            # Calculate notional (cost basis) and unrealized P&L
            if avg_cost > 0:
                notional = avg_cost * quantity
                cost_basis = notional
                # Unrealized P&L = Current Value - Cost Basis
                unrealized_pnl = market_value - cost_basis
            else:
                # If avg_cost is 0 or missing, we cannot calculate accurate P&L
                # Set notional to 0 and unrealized_pnl to None/0 to avoid showing incorrect value
                notional = 0.0
                cost_basis = 0.0
                unrealized_pnl = 0.0  # Set to 0 instead of market_value to avoid confusion
                logger.warning(
                    f"[P&L] {p['symbol']}: avg_cost is 0 or unset. Cannot calculate accurate unrealized P&L. "
                    f"Setting unrealized_pnl to 0. Please ensure avg_cost is correctly set in database. "
                    f"Market value: ${market_value:.2f}"
                )
            
            # Debug log for positions to verify calculation (including XRP)
            if p["symbol"] in ["BTC", "BNB", "ETH", "SOL", "XRP"]:
                logger.info(
                    f"[P&L] {p['symbol']}: price=${current_price:.2f}, qty={quantity:.8f}, "
                    f"avg_cost=${avg_cost:.2f}, notional=${notional:.2f}, market_value=${market_value:.2f}, "
                    f"cost_basis=${cost_basis:.2f}, unrealized_pnl=${unrealized_pnl:.2f}"
                )
        else:
            # If price is None but we have avg_cost, we can still calculate notional
            if avg_cost > 0:
                notional = avg_cost * quantity
                logger.warning(
                    f"[P&L] {p['symbol']}: Price unavailable, but avg_cost=${avg_cost:.2f}, "
                    f"qty={quantity:.8f}, notional=${notional:.2f}"
                )
            else:
                logger.warning(
                    f"[P&L] {p['symbol']}: Both price and avg_cost unavailable/unset. "
                    f"qty={quantity:.8f}"
                )
        
        enriched_positions.append(
            {
                "id": p["id"],
                "account_id": p["account_id"],
                "symbol": p["symbol"],
                "name": p["name"],
                "market": p["market"],
                "quantity": quantity,
                "available_quantity": float(p["available_quantity"]),
                "avg_cost": avg_cost,
                "last_price": current_price,
                "current_price": current_price,  # Add current_price for compatibility
                "notional": notional,  # Add notional field
                "market_value": market_value,
                "current_value": market_value,  # Add current_value for compatibility
                "unrealized_pnl": unrealized_pnl,
            }
        )

    # Calculate total unrealized P&L
    total_unrealized_pnl = sum(
        (pos.get("unrealized_pnl") or 0) for pos in enriched_positions
    )
    
    # Prepare response data - exclude expensive asset curve calculation for frequent updates
    response_data = {
        "type": "snapshot",  # Frontend expects "snapshot" type
        "overview": overview,
        "positions": enriched_positions,
        "total_unrealized_pnl": total_unrealized_pnl,
        "orders": [
            {
                "id": o.id,
                "order_no": o.order_no,
                "user_id": o.account_id,
                "symbol": o.symbol,
                "name": o.name,
                "market": o.market,
                "side": o.side,
                "order_type": o.order_type,
                "price": float(o.price) if o.price is not None else None,
                "quantity": float(o.quantity),
                "filled_quantity": float(o.filled_quantity),
                "status": o.status,
            }
            for o in orders[:10]  # Reduced from 20 to 10
        ],
        "trades": [
            {
                "id": t.get("id", 0),
                "order_id": t.get("order_id"),
                "user_id": t.get("account_id", account_id),
                "symbol": t.get("symbol", ""),
                "name": t.get("name", t.get("symbol", "")),
                "market": t.get("market", "CRYPTO"),
                "side": t.get("side", "BUY"),
                "price": float(t.get("price", 0)),
                "quantity": float(t.get("quantity", 0)),
                "commission": float(t.get("commission", 0)),
                "trade_time": str(t.get("trade_time")) if t.get("trade_time") else None,
            }
            for t in trades
        ],
        "ai_decisions": [
            {
                "id": d.id,
                "decision_time": str(d.decision_time),
                "reason": d.reason,
                "operation": d.operation,
                "symbol": d.symbol,
                "prev_portion": float(d.prev_portion),
                "target_portion": float(d.target_portion),
                "total_balance": float(d.total_balance),
                "executed": str(d.executed).lower() if d.executed else "false",
                "order_id": d.order_id,
            }
            for d in ai_decisions
        ],
        # Asset curves only included occasionally (every minute)
        "timestamp": datetime.now().timestamp(),
    }

    # Only include expensive asset curve data every 60 seconds
    current_second = int(datetime.now().timestamp()) % 60
    if current_second < 10:  # First 10 seconds of each minute
        try:
            response_data["all_asset_curves"] = get_all_asset_curves_data(db, "1h")
            # Keep type as "snapshot" - frontend expects this
        except Exception as e:
            logging.error(f"Failed to get asset curves: {e}")

    if price_error_message:
        response_data["warning"] = {"type": "market_data_error", "message": price_error_message}

    await manager.send_to_account(account_id, response_data)


async def _send_snapshot(db: Session, account_id: int):
    """
    Send snapshot - now uses database cache instead of real-time Binance API calls.
    Delegates to _send_snapshot_optimized for better performance.
    """
    # Simply delegate to optimized version which reads from database
    await _send_snapshot_optimized(db, account_id)


# Removed duplicate websocket_endpoint and old dead code - using the websocket_endpoint below
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        manager.set_event_loop(asyncio.get_running_loop())
    except RuntimeError:
        pass
    account_id: int | None = None
    user_id: int | None = None  # Initialize user_id to avoid UnboundLocalError

    try:
        while True:
            # Check if WebSocket is still connected before trying to receive
            if websocket.client_state.name != "CONNECTED":
                break

            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                # Client disconnected gracefully
                break
            except Exception as e:
                # Handle other connection errors
                logging.error(f"WebSocket receive error: {e}")
                break

            try:
                msg = json.loads(data)
            except json.JSONDecodeError as e:
                logging.error(f"Invalid JSON received: {e}")
                try:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON format"}))
                except Exception:
                    break
                continue
            kind = msg.get("type")
            db: Session = SessionLocal()  # Paper DB for account metadata
            try:
                if kind == "bootstrap":
                    #  mode: Create or get default default user
                    username = msg.get("username", "default")
                    user = get_or_create_user(db, username)

                    # Get existing account for this user (from metadata DB)
                    # Balance and positions are fetched from Binance in real-time
                    account = get_or_create_default_account(db, user.id, account_name=f"{username} AI Trader")

                    if not account:
                        # Allow connection but with no account (frontend will handle this)
                        account_id = None
                    else:
                        account_id = account.id

                        # Account exists in metadata database - no need to create trading DB records
                        # All trading data is fetched from Binance in real-time

                    # Register the connection (handles None account_id gracefully)
                    manager.register(account_id, websocket)

                    # Send bootstrap confirmation with account info
                    try:
                        if account and hasattr(account, "id"):
                            # Ensure account is an Account object, not a dict
                            await manager.send_to_account(
                                account_id,
                                {
                                    "type": "bootstrap_ok",
                                    "user": {"id": user.id, "username": user.username},
                                    "account": {"id": account.id, "name": account.name, "user_id": account.user_id},
                                },
                            )
                            await _send_snapshot_optimized(db, account_id)
                        else:
                            # Send bootstrap with no account info
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "bootstrap_ok",
                                        "user": {"id": user.id, "username": user.username},
                                        "account": None,
                                    }
                                )
                            )
                    except Exception as e:
                        logging.error(f"Failed to send bootstrap response: {e}", exc_info=True)
                        # Try to send bootstrap_ok even if snapshot fails
                        try:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "bootstrap_ok",
                                        "user": {"id": user.id, "username": user.username},
                                        "account": {"id": account.id, "name": account.name} if account else None,
                                        "error": str(e),
                                    }
                                )
                            )
                        except Exception as send_err:
                            logging.error(f"Failed to send error response: {send_err}")
                        # Don't break - continue to process other messages
                        continue
                elif kind == "subscribe":
                    # subscribe existing user_id
                    uid = int(msg.get("user_id"))
                    u = get_user(db, uid)
                    if not u:
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": "user not found"}))
                        except Exception:
                            break
                        continue
                    user_id = uid
                    manager.register(user_id, websocket)
                    try:
                        await _send_snapshot_optimized(db, user_id)
                    except Exception as e:
                        logging.error(f"Failed to send snapshot: {e}")
                        break
                elif kind == "switch_user":
                    # Switch to different user account
                    target_username = msg.get("username")
                    if not target_username:
                        await websocket.send_text(json.dumps({"type": "error", "message": "username required"}))
                        continue

                    # Unregister from current user if any
                    if user_id is not None:
                        manager.unregister(user_id, websocket)

                    # Find target user
                    target_user = get_or_create_user(db, target_username, 100000.0)
                    user_id = target_user.id

                    # Register to new user
                    manager.register(user_id, websocket)

                    # Send confirmation and snapshot
                    await manager.send_to_account(
                        user_id,
                        {"type": "user_switched", "user": {"id": target_user.id, "username": target_user.username}},
                    )
                    await _send_snapshot(db, user_id)
                elif kind == "switch_account":
                    # Switch to different account by ID
                    target_account_id = msg.get("account_id")
                    if not target_account_id:
                        await websocket.send_text(json.dumps({"type": "error", "message": "account_id required"}))
                        continue

                    # Unregister from current account if any
                    if account_id is not None:
                        manager.unregister(account_id, websocket)

                    # Get target account from paper DB (metadata)
                    target_account = get_account(db, target_account_id)
                    if not target_account:
                        await websocket.send_text(json.dumps({"type": "error", "message": "account not found"}))
                        continue

                    account_id = target_account.id

                    # Register to new account
                    manager.register(account_id, websocket)

                    # Send confirmation and snapshot
                    await manager.send_to_account(
                        account_id,
                        {
                            "type": "account_switched",
                            "account": {
                                "id": target_account.id,
                                "user_id": target_account.user_id,
                                "name": target_account.name,
                            },
                        },
                    )
                    await _send_snapshot(db, account_id)
                elif kind == "get_snapshot":
                    if account_id is not None:
                        account = get_account(db, account_id)
                        if account:
                            await _send_snapshot_optimized(db, account_id)
                elif kind == "get_asset_curve":
                    # Get asset curve data with specific timeframe
                    timeframe = msg.get("timeframe", "1h")
                    if timeframe not in ["5m", "1h", "1d"]:
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": "Invalid timeframe. Must be 5m, 1h, or 1d"})
                        )
                        continue

                    asset_curves = get_all_asset_curves_data(db, timeframe)
                    await websocket.send_text(
                        json.dumps({"type": "asset_curve_data", "timeframe": timeframe, "data": asset_curves})
                    )
                elif kind == "place_order":
                    if account_id is None:
                        await websocket.send_text(json.dumps({"type": "error", "message": "not authenticated"}))
                        continue

                    try:
                        # Get account metadata from paper DB
                        account_meta = get_account(db, account_id)
                        if not account_meta:
                            await websocket.send_text(json.dumps({"type": "error", "message": "account not found"}))
                            continue

                        user = get_user(db, account_meta.user_id)
                        if not user:
                            await websocket.send_text(json.dumps({"type": "error", "message": "user not found"}))
                            continue

                        # Get account metadata from metadata database
                        account = get_account(db, account_id)
                        if not account:
                            await websocket.send_text(json.dumps({"type": "error", "message": "account not found"}))
                            continue

                        # Extract order parameters
                        symbol = msg.get("symbol")
                        name = msg.get("name", symbol)  # Use symbol as name if not provided
                        market = msg.get("market", "CRYPTO")
                        side = msg.get("side")
                        order_type = msg.get("order_type")
                        price = msg.get("price")
                        quantity = msg.get("quantity")

                        # Validate required parameters
                        if not all([symbol, side, order_type, quantity]):
                            await websocket.send_text(
                                json.dumps({"type": "error", "message": "missing required parameters"})
                            )
                            continue

                        # Convert quantity to float (crypto supports fractional quantities)
                        try:
                            quantity = float(quantity)
                        except (ValueError, TypeError):
                            await websocket.send_text(json.dumps({"type": "error", "message": "invalid quantity"}))
                            continue

                        # Orders are placed directly on Binance (via trading_commands.py)
                        # This endpoint is deprecated for real trading - orders should go through Binance API
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": "Orders are placed directly on Binance. Use the trading API instead.",
                                }
                            )
                        )

                        # Send updated snapshot
                        await _send_snapshot(db, account_id)

                    except ValueError as e:
                        # Business logic errors (insufficient funds, etc.)
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                        except Exception:
                            break
                    except Exception as e:
                        # Unexpected errors
                        logger.error(f"Order placement error: {e}", exc_info=True)
                        try:
                            await websocket.send_text(
                                json.dumps({"type": "error", "message": f"order placement failed: {str(e)}"})
                            )
                        except Exception:
                            break
                elif kind == "ping":
                    try:
                        await websocket.send_text(json.dumps({"type": "pong"}))
                    except Exception:
                        break
                else:
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": "unknown message"}))
                    except Exception:
                        break
            finally:
                db.close()
    except WebSocketDisconnect:
        if account_id is not None:
            manager.unregister(account_id, websocket)
        if user_id is not None:
            manager.unregister(user_id, websocket)
        return
    finally:
        # Clean up resources when user disconnects
        if account_id is not None:
            manager.unregister(account_id, websocket)
        if user_id is not None:
            manager.unregister(user_id, websocket)

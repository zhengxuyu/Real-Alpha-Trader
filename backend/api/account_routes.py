"""
Account and Asset Curve API Routes (Cleaned)
"""

import asyncio
import json
import logging
import threading
import requests
from datetime import date, datetime, timedelta, timezone
from openai import OpenAI
from openai import APIError as OpenAIAPIError
from decimal import Decimal
from typing import List, Optional

from api.ws import manager as ws_manager, _send_snapshot_optimized
from database.connection import SessionLocal
from database.models import Account, AccountAssetSnapshot, CryptoPrice, Order, Position, Trade, User
from fastapi import APIRouter, Depends, HTTPException
from repositories.strategy_repo import get_strategy_by_account, upsert_strategy
from schemas.account import StrategyConfig, StrategyConfigUpdate
from services.ai_decision_service import _extract_text_from_message, build_chat_completion_endpoints
from services.asset_calculator import calc_positions_value
from services.asset_curve_calculator import invalidate_asset_curve_cache
from services.broker_adapter import get_balance_and_positions, get_open_orders, get_closed_orders
from services.market_data import get_kline_data
from services.scheduler import reset_auto_trading_job
from services.trading_strategy import strategy_manager
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", tags=["account"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def mask_api_key(key: Optional[str]) -> str:
    """Mask API key, showing only last 4 characters"""
    if not key or len(key) <= 4:
        return "****"
    return "****" + key[-4:]


def _normalize_bool(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return bool(value)


def _serialize_strategy(account: Account, strategy) -> StrategyConfig:
    """Convert database strategy config to API schema."""
    last_trigger = strategy.last_trigger_at
    if last_trigger:
        if last_trigger.tzinfo is None:
            last_iso = last_trigger.replace(tzinfo=timezone.utc).isoformat()
        else:
            last_iso = last_trigger.astimezone(timezone.utc).isoformat()
    else:
        last_iso = None

    return StrategyConfig(
        trigger_mode=strategy.trigger_mode or "realtime",
        interval_seconds=strategy.interval_seconds,
        tick_batch_size=strategy.tick_batch_size,
        enabled=(strategy.enabled == "true" and account.auto_trading_enabled == "true"),
        last_trigger_at=last_iso,
    )


@router.get("/list")
async def list_all_accounts(db: Session = Depends(get_db)):
    """Get all active accounts - balances fetched from Binance in real-time"""
    try:
        # Get account metadata from metadata database
        accounts = db.query(Account).filter(Account.is_active == "true").all()
        logger.info(f"Found {len(accounts)} active accounts in metadata database")

        result = []
        for account in accounts:
            # Get balance from Binance in real-time (single API call)
            balance, _ = get_balance_and_positions(account)
            current_cash = float(balance) if balance is not None else 0.0

            user = db.query(User).filter(User.id == account.user_id).first()
            result.append(
                {
                    "id": account.id,
                    "user_id": account.user_id,
                    "username": user.username if user else "unknown",
                    "name": account.name,
                    "account_type": account.account_type,
                    "current_cash": current_cash,
                    "frozen_cash": 0.0,  # Not tracked - all data from Binance
                    "model": account.model,
                    "base_url": account.base_url,
                    "api_key": mask_api_key(account.api_key),
                    "binance_api_key": mask_api_key(account.binance_api_key),
                    "binance_secret_key": mask_api_key(account.binance_secret_key),
                    "is_active": account.is_active == "true",
                    "auto_trading_enabled": account.auto_trading_enabled == "true",
                }
            )

        logger.info(f"[ACCOUNT_LIST] Returning {len(result)} accounts")
        return result
    except Exception as e:
        logger.error(f"Failed to list accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list accounts: {str(e)}")


@router.get("/{account_id}/overview")
async def get_specific_account_overview(account_id: int, db: Session = Depends(get_db)):
    """Get overview for a specific account - data fetched from Binance in real-time"""
    logger.info(f"[ACCOUNT_OVERVIEW] Getting overview for account {account_id}")
    try:
        # Get account metadata from metadata database
        account = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").first()

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        # Get balance and positions from Binance in real-time (single API call)
        balance, positions = get_balance_and_positions(account)
        current_cash = float(balance) if balance is not None else 0.0
        positions_value = sum(float(pos["quantity"]) * 0.0 for pos in positions)  # Would need current price
        positions_count = len(positions)

        # Get open orders from Binance in real-time
        open_orders = get_open_orders(account)
        pending_orders = len(open_orders)

        result = {
            "account": {
                "id": account.id,
                "name": account.name,
                "account_type": account.account_type,
                "current_cash": current_cash,
                "frozen_cash": 0.0,  # Not tracked - all data from Binance
            },
            "total_assets": current_cash + positions_value,  # Would need to calculate positions value properly
            "positions_value": positions_value,
            "positions_count": positions_count,
            "pending_orders": pending_orders,
        }
        logger.info(f"[ACCOUNT_OVERVIEW] Returning overview for account {account_id}: cash=${current_cash:.2f}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account {account_id} overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get account overview: {str(e)}")


@router.get("/{account_id}/strategy", response_model=StrategyConfig)
async def get_account_strategy(account_id: int, db: Session = Depends(get_db)):
    """Fetch AI trading strategy configuration for an account."""
    account = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    strategy = get_strategy_by_account(db, account_id)
    if not strategy:
        strategy = upsert_strategy(
            db,
            account_id=account_id,
            trigger_mode="realtime",
            interval_seconds=1,
            tick_batch_size=1,
            enabled=(account.auto_trading_enabled == "true"),
        )
        strategy_manager.refresh_strategies(force=True)

    return _serialize_strategy(account, strategy)


@router.put("/{account_id}/strategy", response_model=StrategyConfig)
async def update_account_strategy(
    account_id: int,
    payload: StrategyConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update AI trading strategy configuration for an account."""
    logger.info(
        f"[STRATEGY] Updating strategy for account {account_id}: trigger_mode={payload.trigger_mode}, enabled={payload.enabled}, interval_seconds={payload.interval_seconds}, tick_batch_size={payload.tick_batch_size}"
    )

    account = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").first()
    if not account:
        logger.warning(f"[STRATEGY] Account {account_id} not found")
        raise HTTPException(status_code=404, detail="Account not found")

    valid_modes = {"realtime", "interval", "tick_batch"}
    if payload.trigger_mode not in valid_modes:
        logger.warning(f"[STRATEGY] Invalid trigger_mode: {payload.trigger_mode}")
        raise HTTPException(status_code=400, detail="Invalid trigger_mode")

    if payload.trigger_mode == "interval":
        if payload.interval_seconds is None or payload.interval_seconds <= 0:
            logger.warning(f"[STRATEGY] Invalid interval_seconds for interval mode: {payload.interval_seconds}")
            raise HTTPException(
                status_code=400,
                detail="interval_seconds must be > 0 for interval mode",
            )
    else:
        interval_seconds = None

    if payload.trigger_mode == "tick_batch":
        if payload.tick_batch_size is None or payload.tick_batch_size <= 0:
            logger.warning(f"[STRATEGY] Invalid tick_batch_size for tick_batch mode: {payload.tick_batch_size}")
            raise HTTPException(
                status_code=400,
                detail="tick_batch_size must be > 0 for tick_batch mode",
            )
    else:
        tick_batch_size = None

    interval_seconds = payload.interval_seconds if payload.trigger_mode == "interval" else None
    tick_batch_size = payload.tick_batch_size if payload.trigger_mode == "tick_batch" else None

    logger.info(
        f"[STRATEGY] Calling upsert_strategy with: account_id={account_id}, trigger_mode={payload.trigger_mode}, interval_seconds={interval_seconds}, tick_batch_size={tick_batch_size}, enabled={payload.enabled}"
    )
    strategy = upsert_strategy(
        db,
        account_id=account_id,
        trigger_mode=payload.trigger_mode,
        interval_seconds=interval_seconds,
        tick_batch_size=tick_batch_size,
        enabled=payload.enabled,
    )

    logger.info(
        f"[STRATEGY] Strategy updated in DB: trigger_mode={strategy.trigger_mode}, interval_seconds={strategy.interval_seconds}, tick_batch_size={strategy.tick_batch_size}, enabled={strategy.enabled}"
    )

    logger.info(f"[STRATEGY] Refreshing strategy manager (force=True)")
    strategy_manager.refresh_strategies(force=True)

    # Refresh account to get latest auto_trading_enabled status
    db.refresh(account)
    result = _serialize_strategy(account, strategy)
    logger.info(
        f"[STRATEGY] Returning serialized strategy: trigger_mode={result.trigger_mode}, enabled={result.enabled}"
    )
    return result


@router.get("/overview")
async def get_account_overview(db: Session = Depends(get_db)):
    """Get overview for the default account - uses correct database based on trade_mode"""
    logger.debug("[PAGE_LOAD] /account/overview endpoint called (main page default account overview)")
    logger.info("[PAGE_LOAD] /account/overview endpoint called (main page default account overview)")

    try:
        # Get account metadata from metadata database
        account = db.query(Account).filter(Account.is_active == "true").first()

        if not account:
            logger.debug("[PAGE_LOAD] No active account found")
            raise HTTPException(status_code=404, detail="No active account found")

        logger.debug(f"[PAGE_LOAD] Found account {account.id} ({account.name}) in metadata DB")
        logger.info(f"[PAGE_LOAD] Found account {account.id} ({account.name}) in metadata DB")

        # Get balance and positions from Binance in real-time (single API call)
        balance, positions = get_balance_and_positions(account)
        current_cash = float(balance) if balance is not None else 0.0
        positions_list = [
            {
                "symbol": pos["symbol"],
                "quantity": float(pos["quantity"]),
                "avg_cost": float(pos.get("avg_cost", 0)),
            }
            for pos in positions
        ]

        # Get open orders from Binance in real-time
        open_orders = get_open_orders(account)
        pending_orders = len(open_orders)

        # Calculate positions value (would need current prices for accurate calculation)
        positions_value = 0.0  # Would need to fetch current prices

        portfolio = {
            "total_assets": current_cash + positions_value,
            "cash": current_cash,
            "positions": positions_list,
            "positions_count": len(positions),
            "pending_orders": pending_orders,
        }

        result = {
            "account": {
                "id": account.id,
                "name": account.name,
                "account_type": account.account_type,
                "current_cash": current_cash,
                "frozen_cash": 0.0,  # Not tracked - all data from Binance
            },
            "portfolio": portfolio,
        }

        logger.info(f"[PAGE_LOAD] Returning overview: account_id={account.id}, cash=${current_cash:.2f}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {str(e)}")


@router.post("/")
async def create_new_account(payload: dict, db: Session = Depends(get_db)):
    """Create a new account - only stores metadata (LLM config), trading data fetched from Binance"""
    try:
        # Get the default user (or first user)
        user = db.query(User).filter(User.username == "default").first()
        if not user:
            user = db.query(User).first()

        if not user:
            raise HTTPException(status_code=404, detail="No user found")

        # Validate required fields
        if "name" not in payload or not payload["name"]:
            raise HTTPException(status_code=400, detail="Account name is required")

        # Create new account metadata in metadata database
        # Only store LLM configuration - trading data is fetched from Binance in real-time
        auto_trading_enabled = _normalize_bool(payload.get("auto_trading_enabled", True))
        auto_trading_value = "true" if auto_trading_enabled else "false"

        new_account = Account(
            user_id=user.id,
            version="v1",
            name=payload["name"],
            account_type=payload.get("account_type", "AI"),
            model=payload.get("model", "gpt-4-turbo"),
            base_url=payload.get("base_url", "https://api.openai.com/v1"),
            api_key=payload.get("api_key", ""),
            binance_api_key=payload.get("binance_api_key", ""),
            binance_secret_key=payload.get("binance_secret_key", ""),
            is_active="true",
            auto_trading_enabled=auto_trading_value,
        )

        db.add(new_account)
        db.commit()
        db.refresh(new_account)

        logger.info(f"Created account {new_account.id} ({new_account.name}) in metadata database")

        return {
            "id": new_account.id,
            "user_id": new_account.user_id,
            "username": user.username,
            "name": new_account.name,
            "account_type": new_account.account_type,
            "current_cash": 0.0,  # Will be fetched from Binance in real-time
            "frozen_cash": 0.0,
            "model": new_account.model,
            "base_url": new_account.base_url,
            "api_key": mask_api_key(new_account.api_key),
            "binance_api_key": mask_api_key(new_account.binance_api_key),
            "binance_secret_key": mask_api_key(new_account.binance_secret_key),
            "is_active": new_account.is_active == "true",
            "auto_trading_enabled": new_account.auto_trading_enabled == "true",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create account: {str(e)}")


@router.post("/sync-all-from-binance")
async def sync_all_accounts_from_binance(db: Session = Depends(get_db)):
    """DEPRECATED: Sync endpoint - Data is now fetched from Binance in real-time.

    This endpoint is kept for backward compatibility but is a no-op.
    All trading data (balance, positions, orders) is fetched from Binance in real-time.
    """
    return {"message": "Data is now fetched from Binance in real-time. Sync endpoint is deprecated.", "results": {}}


# Remove the rest of switch-trade-mode implementation
# The function is already marked as deprecated above with HTTPException 410


@router.post("/switch-trade-mode")
async def switch_global_trade_mode(payload: dict, db: Session = Depends(get_db)):
    """DEPRECATED: Paper trading removed. Only real trading is supported."""
    raise HTTPException(status_code=410, detail="Paper trading has been removed. Only real trading is supported.")


@router.put("/{account_id}")
async def update_account_settings(account_id: int, payload: dict, db: Session = Depends(get_db)):
    """Update account settings (for paper trading demo)"""
    try:
        logger.info(f"Updating account {account_id} with payload: {payload}")

        account = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").first()

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        # Update fields if provided (allow empty strings for api_key and base_url)
        if "name" in payload:
            if payload["name"]:
                account.name = payload["name"]
                logger.info(f"Updated name to: {payload['name']}")
            else:
                raise HTTPException(status_code=400, detail="Account name cannot be empty")

        if "model" in payload:
            account.model = payload["model"] if payload["model"] else None
            logger.info(f"Updated model to: {account.model}")

        if "base_url" in payload:
            account.base_url = payload["base_url"]
            logger.info(f"Updated base_url to: {account.base_url}")

        if "api_key" in payload:
            account.api_key = payload["api_key"]
            logger.info(f"Updated api_key (length: {len(payload['api_key']) if payload['api_key'] else 0})")

        if "auto_trading_enabled" in payload:
            auto_trading_enabled = _normalize_bool(payload.get("auto_trading_enabled"))
            account.auto_trading_enabled = "true" if auto_trading_enabled else "false"
            logger.info(f"Updated auto_trading_enabled to: {account.auto_trading_enabled}")

        # Note: trade_mode should not be updated via this endpoint
        # It should only be updated via the global switch_global_trade_mode endpoint
        if "trade_mode" in payload:
            logger.warning(
                f"Ignoring trade_mode update attempt for account {account_id}. Use global switch endpoint instead."
            )

        # Handle Binance API keys update
        if "binance_api_key" in payload:
            account.binance_api_key = payload["binance_api_key"]
            logger.info(f"Updated binance_api_key for account {account.name}")

        if "binance_secret_key" in payload:
            account.binance_secret_key = payload["binance_secret_key"]
            logger.info(f"Updated binance_secret_key for account {account.name}")

        # Note: current_cash cannot be updated - it's fetched from Binance in real-time
        if "current_cash" in payload:
            logger.warning(
                f"Attempted to update cash for account {account.name} - ignored (cash comes from Binance in real-time)"
            )

        db.commit()
        db.refresh(account)
        logger.info(f"Account {account_id} updated successfully")

        # Reset auto trading job after account update (async in background to avoid blocking response)
        def reset_job_async():
            try:
                reset_auto_trading_job()
                logger.info("Auto trading job reset successfully after account update")
            except Exception as e:
                logger.warning(f"Failed to reset auto trading job: {e}")

        # Run reset in background thread to not block API response
        reset_thread = threading.Thread(target=reset_job_async, daemon=True)
        reset_thread.start()
        logger.info("Auto trading job reset initiated in background")

        user = db.query(User).filter(User.id == account.user_id).first()

        # Get balance from Binance in real-time (for response)
        balance, _ = get_balance_and_positions(account)
        current_cash = float(balance) if balance is not None else 0.0

        return {
            "id": account.id,
            "user_id": account.user_id,
            "username": user.username if user else "unknown",
            "name": account.name,
            "account_type": account.account_type,
            "current_cash": current_cash,  # From Binance in real-time
            "frozen_cash": 0.0,  # Not tracked - all data from Binance
            "model": account.model,
            "base_url": account.base_url,
            "api_key": mask_api_key(account.api_key),
            "binance_api_key": mask_api_key(account.binance_api_key),
            "binance_secret_key": mask_api_key(account.binance_secret_key),
            "is_active": account.is_active == "true",
            "auto_trading_enabled": account.auto_trading_enabled == "true",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update account: {str(e)}")


@router.get("/asset-curve/timeframe")
async def get_asset_curve_by_timeframe(timeframe: str = "1d", db: Session = Depends(get_db)):
    """Get asset curve data for all accounts within a specified timeframe (20 data points)

    Args:
        timeframe: Time period, options: 5m, 1h, 1d
    """
    try:
        # Validate timeframe
        valid_timeframes = ["5m", "1h", "1d"]
        if timeframe not in valid_timeframes:
            raise HTTPException(
                status_code=400, detail=f"Invalid timeframe. Must be one of: {', '.join(valid_timeframes)}"
            )

        # Map timeframe to period for kline data
        timeframe_map = {"5m": "5m", "1h": "1h", "1d": "1d"}
        period = timeframe_map[timeframe]

        # Get all active accounts
        accounts = db.query(Account).filter(Account.is_active == "true").all()
        if not accounts:
            return []

        # Get all unique symbols from all account positions and trades
        symbols_query = db.query(Trade.symbol, Trade.market).distinct().all()
        unique_symbols = set()
        for symbol, market in symbols_query:
            unique_symbols.add((symbol, market))

        if not unique_symbols:
            # No trades yet, return initial capital for all accounts
            now = datetime.now()
            # Get balance from Binance for each account
            result = []
            for account in accounts:
                try:
                    balance, _ = get_balance_and_positions(account)
                    current_cash = float(balance) if balance is not None else 0.0
                except Exception:
                    current_cash = 0.0

                result.append(
                    {
                        "timestamp": int(now.timestamp()),
                        "datetime_str": now.isoformat(),
                        "user_id": account.user_id,
                        "username": account.name,
                        "total_assets": current_cash,
                        "cash": current_cash,
                        "positions_value": 0.0,
                    }
                )
            return result

        # Fetch kline data for all symbols (20 points)
        symbol_klines = {}
        for symbol, market in unique_symbols:
            try:
                klines = get_kline_data(symbol, market, period, 20)
                if klines:
                    symbol_klines[(symbol, market)] = klines
                    logger.info(f"Fetched {len(klines)} klines for {symbol}.{market}")
            except Exception as e:
                logger.warning(f"Failed to fetch klines for {symbol}.{market}: {e}")

        if not symbol_klines:
            raise HTTPException(status_code=500, detail="Failed to fetch market data")

        # Get timestamps from the first symbol's klines
        first_klines = next(iter(symbol_klines.values()))
        timestamps = [k["timestamp"] for k in first_klines]

        # Calculate asset value for each account at each timestamp
        result = []
        for account in accounts:
            account_id = account.id

            # Get all trades for this account
            trades = db.query(Trade).filter(Trade.account_id == account_id).order_by(Trade.trade_time.asc()).all()

            # Get current balance from Binance for this account
            try:
                balance, _ = get_balance_and_positions(account)
                current_cash = float(balance) if balance is not None else 0.0
            except Exception:
                current_cash = 0.0

            if not trades:
                # No trades, return current balance at all timestamps
                for i, ts in enumerate(timestamps):
                    result.append(
                        {
                            "timestamp": ts,
                            "datetime_str": first_klines[i]["datetime_str"],
                            "user_id": account.user_id,
                            "username": account.name,
                            "total_assets": current_cash,
                            "cash": current_cash,
                            "positions_value": 0.0,
                        }
                    )
                continue

            # Calculate holdings and cash at each timestamp
            for i, ts in enumerate(timestamps):
                ts_datetime = datetime.fromtimestamp(ts, tz=timezone.utc)

                # Calculate cash changes up to this timestamp
                cash_change = 0.0
                position_quantities = {}

                for trade in trades:
                    trade_time = trade.trade_time
                    if not trade_time.tzinfo:
                        trade_time = trade_time.replace(tzinfo=timezone.utc)

                    if trade_time <= ts_datetime:
                        # Update cash
                        trade_amount = float(trade.price) * float(trade.quantity) + float(trade.commission)
                        if trade.side == "BUY":
                            cash_change -= trade_amount
                        else:  # SELL
                            cash_change += trade_amount

                        # Update position
                        key = (trade.symbol, trade.market)
                        if key not in position_quantities:
                            position_quantities[key] = 0.0

                        if trade.side == "BUY":
                            position_quantities[key] += float(trade.quantity)
                        else:  # SELL
                            position_quantities[key] -= float(trade.quantity)

                # Get current balance from Binance
                try:
                    balance, _ = get_balance_and_positions(account)
                    base_cash = float(balance) if balance is not None else 0.0
                except Exception:
                    base_cash = 0.0
                current_cash = base_cash + cash_change

                # Calculate positions value using prices at this timestamp
                positions_value = 0.0
                for (symbol, market), quantity in position_quantities.items():
                    if quantity > 0 and (symbol, market) in symbol_klines:
                        klines = symbol_klines[(symbol, market)]
                        if i < len(klines):
                            price = klines[i]["close"]
                            if price:
                                positions_value += float(price) * quantity

                total_assets = current_cash + positions_value

                result.append(
                    {
                        "timestamp": ts,
                        "datetime_str": first_klines[i]["datetime_str"],
                        "user_id": account.user_id,
                        "username": account.name,
                        "total_assets": total_assets,
                        "cash": current_cash,
                        "positions_value": positions_value,
                    }
                )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get asset curve for timeframe: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get asset curve for timeframe: {str(e)}")


@router.post("/test-llm")
async def test_llm_connection(payload: dict):
    """Test LLM connection with provided credentials"""
    try:
        # Log incoming parameters for debugging
        safe_payload = {}
        for k, v in payload.items():
            if k == "api_key" and v:
                safe_payload[k] = ("*" * min(8, len(str(v)) - 4)) + str(v)[-4:] if len(str(v)) > 4 else "***"
            else:
                safe_payload[k] = v
        logger.info(f"[TEST-LLM] Received payload: {json.dumps(safe_payload, indent=2)}")

        model = payload.get("model", "gpt-3.5-turbo")
        base_url = payload.get("base_url", "https://api.openai.com/v1")
        api_key = payload.get("api_key", "")

        logger.info(
            f"[TEST-LLM] Parsed parameters - model: {model}, base_url: {base_url}, api_key_length: {len(api_key) if api_key else 0}"
        )

        if not api_key:
            logger.warning("[TEST-LLM] API key is missing")
            return {"success": False, "message": "API key is required"}

        if not base_url:
            logger.warning("[TEST-LLM] Base URL is missing")
            return {"success": False, "message": "Base URL is required"}

        # Use base_url as-is without modification
        logger.info(f"[TEST-LLM] Using base_url as provided: {base_url}")

        # Test the connection using OpenAI client library
        try:
            # Build messages based on model type
            model_lower = model.lower()

            # Reasoning models that don't support temperature parameter
            is_reasoning_model = any(x in model_lower for x in ["gpt-5", "o1-preview", "o1-mini", "o1-", "o3-", "o4-"])

            # o1 series specifically doesn't support system messages
            is_o1_series = any(x in model_lower for x in ["o1-preview", "o1-mini", "o1-"])

            # Build messages
            if is_o1_series:
                messages = [{"role": "user", "content": "Say 'Connection test successful' if you can read this."}]
            else:
                messages = [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Say 'Connection test successful' if you can read this."},
                ]

            # Create OpenAI client (simple, following OpenAI SDK example)
            logger.info(f"[TEST-LLM] Creating OpenAI client with base_url: {base_url} ｜ api_key: {api_key}")
            client = OpenAI(base_url=f"{base_url}", api_key=api_key)

            # Prepare request parameters
            completion_kwargs = {
                "model": model,
                "messages": messages,
            }

            # Reasoning models don't support temperature
            if not is_reasoning_model:
                completion_kwargs["temperature"] = 0

            # Use max_completion_tokens for newer models, max_tokens for older models
            is_new_model = is_reasoning_model or any(x in model_lower for x in ["gpt-4o"])
            if is_new_model:
                completion_kwargs["max_completion_tokens"] = 2000
            else:
                completion_kwargs["max_tokens"] = 2000

            # For GPT-5 series, set reasoning_effort
            if "gpt-5" in model_lower:
                completion_kwargs["reasoning_effort"] = "minimal"

            # Log request parameters (simplified for readability)
            log_params = {k: (f"[{len(v)} messages]" if k == "messages" else v) for k, v in completion_kwargs.items()}
            logger.info(f"[TEST-LLM] Request parameters: {json.dumps(log_params, indent=2)}")

            # Make the API call
            try:
                completion = client.chat.completions.create(**completion_kwargs)

                logger.info(f"[TEST-LLM] API call successful")

                # Extract response
                if completion.choices and len(completion.choices) > 0:
                    choice = completion.choices[0]
                    message = choice.message
                    finish_reason = choice.finish_reason

                    # Get content
                    content = message.content if message.content else ""

                    # For reasoning models, check reasoning field
                    if not content and is_reasoning_model:
                        # Try to get reasoning from message (if available)
                        if hasattr(message, "reasoning") and message.reasoning:
                            reasoning = message.reasoning
                            logger.info(f"[TEST-LLM] Model {model} responded with reasoning (reasoning model)")
                            snippet = reasoning[:100] + "..." if len(reasoning) > 100 else reasoning
                            return {
                                "success": True,
                                "message": f"Connection successful! Model {model} (reasoning model) responded correctly.",
                                "response": f"[Reasoning: {snippet}]",
                            }

                    # Standard content check
                    if content:
                        logger.info(f"[TEST-LLM] Model {model} responded successfully")
                        return {
                            "success": True,
                            "message": f"Connection successful! Model {model} responded correctly.",
                            "response": content,
                        }

                    # Empty content
                    logger.warning(f"[TEST-LLM] LLM responded but with empty content. finish_reason={finish_reason}")
                    return {
                        "success": False,
                        "message": f"LLM responded but with empty content (finish_reason: {finish_reason}). Try increasing token limit or using a different model.",
                    }
                else:
                    logger.warning(f"[TEST-LLM] Unexpected response format: no choices in response")
                    return {"success": False, "message": "Unexpected response format from LLM"}

            except OpenAIAPIError as e:
                error_message = str(e)
                error_type = type(e).__name__

                logger.warning(f"[TEST-LLM] OpenAI API error ({error_type}): {error_message}")

                # Extract more details from the error
                error_details = ""
                if hasattr(e, "response") and e.response is not None:
                    try:
                        error_body = e.response.json() if hasattr(e.response, "json") else None
                        if error_body and isinstance(error_body, dict):
                            error_info = error_body.get("error", {})
                            if isinstance(error_info, dict):
                                error_details = error_info.get("message", "")
                                logger.warning(f"[TEST-LLM] Error details from response: {error_details}")
                    except Exception:
                        pass

                # Map OpenAI errors to user-friendly messages
                if "401" in error_message or "authentication" in error_message.lower():
                    return {
                        "success": False,
                        "message": f"Authentication failed. Please check your API key.{' Details: ' + error_details if error_details else ''}",
                    }
                elif "403" in error_message or "permission" in error_message.lower():
                    return {
                        "success": False,
                        "message": "Permission denied. Your API key may not have access to this model.",
                    }
                elif "429" in error_message or "rate limit" in error_message.lower():
                    return {"success": False, "message": "Rate limit exceeded. Please try again later."}
                elif "404" in error_message or "not found" in error_message.lower():
                    return {"success": False, "message": f"Model '{model}' not found or endpoint not available."}
                else:
                    return {"success": False, "message": f"API error: {error_message}"}

        except Exception as e:
            logger.error(f"[TEST-LLM] Connection test failed: {e}", exc_info=True)
            error_msg = str(e)

            if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                return {"success": False, "message": f"Failed to connect to {base_url}. Please check the base URL."}
            elif "timeout" in error_msg.lower():
                return {"success": False, "message": "Request timed out. The LLM service may be unavailable."}
            else:
                return {"success": False, "message": f"Connection test failed: {error_msg}"}

    except Exception as e:
        logger.error(f"[TEST-LLM] Failed to test LLM connection: {e}", exc_info=True)
        return {"success": False, "message": f"Failed to test LLM connection: {str(e)}"}

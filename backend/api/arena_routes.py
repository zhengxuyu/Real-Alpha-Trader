"""
Alpha Arena aggregated data routes.
Provides completed trades, model chat summaries, and consolidated positions
for showcasing multi-model trading activity on the dashboard.
"""

from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Dict, List, Optional, Tuple

from database.connection import SessionLocal
from database.models import Account, AccountStrategyConfig, AIDecisionLog, Order, Position, Trade
from fastapi import APIRouter, Depends, Query
from services.broker_adapter import get_balance_and_positions
from services.binance_sync import get_binance_trade_history
from services.market_data import get_last_price
from services.price_cache import cache_price, get_cached_price
from sqlalchemy import desc
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arena", tags=["arena"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_latest_price(symbol: str, market: str = "CRYPTO") -> Optional[float]:
    """Get the latest price using cache when possible, fallback to market feed."""
    price = get_cached_price(symbol, market)
    if price is not None:
        return price

    try:
        price = get_last_price(symbol, market)
        if price:
            cache_price(symbol, market, price)
        return price

    except Exception:
        return None


def _analyze_balance_series(balances: List[float]) -> Tuple[float, float, List[float], float]:
    """Return biggest gain/loss deltas, percentage returns, and balance volatility."""
    if len(balances) < 2:
        return 0.0, 0.0, [], 0.0

    biggest_gain = float("-inf")
    biggest_loss = float("inf")
    returns: List[float] = []

    previous = balances[0]

    for current in balances[1:]:
        delta = current - previous
        if delta > biggest_gain:
            biggest_gain = delta
        if delta < biggest_loss:
            biggest_loss = delta

        if previous not in (0, None):
            try:
                returns.append(delta / previous)
            except ZeroDivisionError:
                pass

        previous = current

    if biggest_gain == float("-inf"):
        biggest_gain = 0.0
    if biggest_loss == float("inf"):
        biggest_loss = 0.0

    volatility = pstdev(balances) if len(balances) > 1 else 0.0

    return biggest_gain, biggest_loss, returns, volatility


def _compute_sharpe_ratio(returns: List[float]) -> Optional[float]:
    """Compute a simple Sharpe ratio approximation using sample returns."""
    if len(returns) < 2:
        return None

    avg_return = mean(returns)
    volatility = pstdev(returns)
    if volatility == 0:
        return None

    scaled_factor = sqrt(len(returns))
    return avg_return / volatility * scaled_factor


def _get_system_startup_time(db: Session) -> Optional[datetime]:
    """
    Get system startup time from SystemConfig.
    Returns None if not found (for backward compatibility).
    """
    try:
        from database.models import SystemConfig
        startup_config = db.query(SystemConfig).filter(SystemConfig.key == "system_startup_time").first()
        if startup_config and startup_config.value:
            try:
                return datetime.fromisoformat(startup_config.value.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                logger.warning(f"Invalid startup time format: {startup_config.value}")
                return None
    except Exception as e:
        logger.debug(f"Failed to get system startup time: {e}")
    return None


def _calculate_win_rate_from_trades(trades: List[Trade], db: Optional[Session] = None) -> Optional[float]:
    """
    Calculate win rate based on completed trades (buy-sell pairs).
    Uses FIFO method to match buy and sell trades.
    Only counts trades that occurred after system startup.
    Returns win rate as a ratio (0.0 to 1.0).
    
    Args:
        trades: List of all trades
        db: Database session (optional, used to get system startup time)
    """
    if not trades:
        logger.debug("No trades found for win rate calculation")
        return None
    
    # Filter trades to only include those after system startup
    startup_time = None
    if db:
        startup_time = _get_system_startup_time(db)
        if startup_time:
            # Filter trades to only include those after system startup
            # Ensure both times are timezone-aware for comparison
            original_count = len(trades)
            if startup_time.tzinfo is None:
                startup_time = startup_time.replace(tzinfo=timezone.utc)
            
            filtered_trades = []
            for t in trades:
                trade_time = t.trade_time
                if trade_time.tzinfo is None:
                    trade_time = trade_time.replace(tzinfo=timezone.utc)
                if trade_time >= startup_time:
                    filtered_trades.append(t)
            
            trades = filtered_trades
            filtered_count = len(trades)
            if original_count != filtered_count:
                logger.info(
                    f"Filtered trades for Win Rate: {original_count} total, "
                    f"{filtered_count} after system startup ({startup_time.isoformat()})"
                )
            if not trades:
                logger.debug(f"No trades found after system startup time: {startup_time.isoformat()}")
                return None
        else:
            logger.debug("System startup time not found, using all trades (backward compatibility mode)")
    
    from decimal import Decimal, ROUND_DOWN
    from collections import defaultdict
    
    # Group trades by symbol
    symbol_trades = defaultdict(list)
    for trade in trades:
        symbol_trades[trade.symbol].append(trade)
    
    completed_trades = []  # List of profit values for completed trades
    PROFIT_THRESHOLD = Decimal("0.000001")  # Small threshold for profit comparison to handle floating point precision
    
    # Process each symbol's trades using FIFO
    for symbol, symbol_trade_list in symbol_trades.items():
        # Sort by trade time
        symbol_trade_list.sort(key=lambda t: t.trade_time)
        
        # FIFO queue: list of buy trades waiting to be matched
        buy_queue = []
        
        buy_count = 0
        sell_count = 0
        
        for trade in symbol_trade_list:
            trade_qty = Decimal(str(trade.quantity))
            if trade_qty <= 0:
                continue
                
            trade_price = Decimal(str(trade.price))
            trade_commission = Decimal(str(trade.commission or 0))
            side = (trade.side or "").upper().strip()
            
            if side == "BUY":
                buy_count += 1
                # Add to buy queue
                buy_queue.append({
                    "quantity": trade_qty,
                    "price": trade_price,
                    "commission": trade_commission,
                })
            elif side == "SELL":
                sell_count += 1
                # Match with buy trades using FIFO
                sell_qty_remaining = trade_qty
                sell_price = trade_price
                sell_commission = trade_commission
                
                while sell_qty_remaining > 0 and buy_queue:
                    buy = buy_queue[0]
                    buy_qty = buy["quantity"]
                    buy_price = buy["price"]
                    buy_commission = buy["commission"]
                    
                    # Calculate how much to match
                    matched_qty = min(sell_qty_remaining, buy_qty)
                    
                    # Calculate profit/loss for this matched portion
                    # Proportionally allocate commissions based on matched quantity
                    if buy_qty > 0:
                        buy_commission_allocated = buy_commission * (matched_qty / buy_qty)
                    else:
                        buy_commission_allocated = Decimal("0")
                    
                    if trade_qty > 0:
                        sell_commission_allocated = sell_commission * (matched_qty / trade_qty)
                    else:
                        sell_commission_allocated = Decimal("0")
                    
                    # Calculate costs and revenues with proper precision
                    buy_cost = (buy_price * matched_qty + buy_commission_allocated).quantize(
                        Decimal("0.000001"), rounding=ROUND_DOWN
                    )
                    sell_revenue = (sell_price * matched_qty - sell_commission_allocated).quantize(
                        Decimal("0.000001"), rounding=ROUND_DOWN
                    )
                    profit = (sell_revenue - buy_cost).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
                    
                    # Record this as a completed trade (convert to float for compatibility)
                    completed_trades.append(float(profit))
                    
                    # Update quantities
                    sell_qty_remaining -= matched_qty
                    buy["quantity"] -= matched_qty
                    
                    # Remove buy from queue if fully consumed (with small tolerance for floating point)
                    if buy["quantity"] <= Decimal("0.00000001"):
                        buy_queue.pop(0)
        
        logger.debug(f"Symbol {symbol}: {buy_count} buys, {sell_count} sells")
    
    if not completed_trades:
        logger.debug(f"No completed trades found. Total trades: {len(trades)}")
        return None
    
    # Calculate win rate: percentage of trades with profit > threshold
    # Use a small threshold to handle floating point precision issues
    threshold = float(PROFIT_THRESHOLD)
    wins = len([p for p in completed_trades if p > threshold])
    total = len(completed_trades)
    
    win_rate = wins / total if total > 0 else None
    logger.debug(
        f"Win rate calculation: {wins} wins out of {total} completed trades = {win_rate:.4f} "
        f"(threshold: {threshold})"
    )
    
    return win_rate


def _aggregate_account_stats(db: Session, account: Account) -> Dict[str, Optional[float]]:
    """Aggregate trade and decision statistics for a given account."""
    # Get balance and positions from Binance in real-time (single API call)
    try:
        balance, positions_data = get_balance_and_positions(account)
        current_cash = float(balance) if balance is not None else 0.0

        # Calculate positions value
        positions_value = 0.0
        for pos in positions_data:
            try:
                price = _get_latest_price(pos["symbol"], "CRYPTO")
                if price:
                    positions_value += float(price) * float(pos["quantity"])
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Failed to fetch Binance data: {e}")
        current_cash = 0.0
        positions_value = 0.0

    # Initial capital is not tracked - since we're using Binance real accounts,
    # we can't calculate return percentage without knowing the initial capital
    # Set initial_capital to total_assets so return is 0 (no baseline to compare against)
    total_assets = positions_value + current_cash
    initial_capital = total_assets  # Set to total_assets so return is 0 (no initial capital tracked)
    total_pnl = total_assets - initial_capital  # Will be 0 since initial_capital = total_assets

    logger.debug(f"[TOTAL_RETURN] _aggregate_account_stats for account {account.id} ({account.name}):")
    logger.debug(f"[TOTAL_RETURN]   - initial_capital: ${initial_capital:.2f} (set to total_assets, no baseline)")
    logger.debug(f"[TOTAL_RETURN]   - current_cash: ${current_cash:.2f}")
    logger.debug(f"[TOTAL_RETURN]   - positions_value: ${positions_value:.2f}")
    logger.debug(f"[TOTAL_RETURN]   - total_assets: ${total_assets:.2f} (positions + cash)")
    logger.debug(f"[TOTAL_RETURN]   - total_pnl: ${total_pnl:.2f} (should be 0, no initial capital tracked)")
    logger.debug(f"[TOTAL_RETURN]   - db_url: {db.bind.url if hasattr(db, 'bind') else 'N/A'}")

    # Since initial capital is not tracked, return percentage should be 0
    total_return_pct = 0.0  # Return is 0 when no initial capital is tracked

    logger.debug(f"[TOTAL_RETURN]   - total_return_pct: 0.00% (no initial capital tracked)")

    trades: List[Trade] = db.query(Trade).filter(Trade.account_id == account.id).order_by(Trade.trade_time.asc()).all()
    trade_count = len(trades)
    total_fees = sum(float(trade.commission or 0) for trade in trades)
    total_volume = sum(abs(float(trade.price or 0) * float(trade.quantity or 0)) for trade in trades)
    first_trade_time = trades[0].trade_time.isoformat() if trades else None
    last_trade_time = trades[-1].trade_time.isoformat() if trades else None

    # Calculate win rate based on completed trades (historical positions only)
    # This excludes current open positions and only considers buy-sell pairs
    # Only trades after system startup are counted
    logger.debug(f"Calculating win rate for account {account.id} ({account.name}) with {trade_count} trades")
    win_rate = _calculate_win_rate_from_trades(trades, db=db)
    logger.debug(f"Win rate result: {win_rate}")
    
    # Calculate loss rate as complement of win rate
    loss_rate = (1.0 - win_rate) if win_rate is not None else None

    decisions: List[AIDecisionLog] = (
        db.query(AIDecisionLog)
        .filter(AIDecisionLog.account_id == account.id)
        .order_by(AIDecisionLog.decision_time.asc())
        .all()
    )
    balances = [float(dec.total_balance) for dec in decisions if dec.total_balance is not None]

    biggest_gain, biggest_loss, returns, balance_volatility = _analyze_balance_series(balances)
    sharpe_ratio = _compute_sharpe_ratio(returns)

    executed_decisions = len([d for d in decisions if d.executed == "true"])
    decision_execution_rate = executed_decisions / len(decisions) if decisions else None
    avg_target_portion = mean(float(d.target_portion or 0) for d in decisions) if decisions else None

    avg_decision_interval_minutes = None
    if len(decisions) > 1:
        intervals = []
        previous = decisions[0].decision_time
        for decision in decisions[1:]:
            if decision.decision_time and previous:
                delta = decision.decision_time - previous
                intervals.append(delta.total_seconds() / 60.0)
            previous = decision.decision_time
        avg_decision_interval_minutes = mean(intervals) if intervals else None

    return {
        "account_id": account.id,
        "account_name": account.name,
        "model": account.model,
        "initial_capital": initial_capital,
        "current_cash": current_cash,
        "positions_value": positions_value,
        "total_assets": total_assets,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "total_fees": total_fees,
        "trade_count": trade_count,
        "total_volume": total_volume,
        "first_trade_time": first_trade_time,
        "last_trade_time": last_trade_time,
        "biggest_gain": biggest_gain,
        "biggest_loss": biggest_loss,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "sharpe_ratio": sharpe_ratio,
        "balance_volatility": balance_volatility,
        "decision_count": len(decisions),
        "executed_decisions": executed_decisions,
        "decision_execution_rate": decision_execution_rate,
        "avg_target_portion": avg_target_portion,
        "avg_decision_interval_minutes": avg_decision_interval_minutes,
    }


@router.get("/trades")
def get_completed_trades(
    limit: int = Query(100, ge=1, le=500),
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return recent trades across all AI accounts.
    Trades are fetched dynamically from Binance API (real-time sync, not stored in database).
    """
    # Get account metadata from metadata database
    # Note: is_active is stored as string "true" or "false"
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").all()
    else:
        accounts = db.query(Account).filter(Account.is_active == "true").all()

    logger.info(f"[get_completed_trades] Found {len(accounts)} active accounts (account_id filter: {account_id})")
    
    if not accounts:
        logger.warning(f"[get_completed_trades] No active accounts found")
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": [],
            "trades": [],
        }

    all_trades: List[dict] = []
    accounts_meta = {}

    # Fetch trades from Binance for each account
    for account in accounts:
        try:
            # Get trade history from Binance
            binance_trades = get_binance_trade_history(account, symbol=None, limit=limit)
            
            logger.info(f"[get_completed_trades] Fetched {len(binance_trades)} trades from Binance for account {account.name}")
            
            for trade_data in binance_trades:
                quantity = trade_data.get("quantity", 0)
                price = trade_data.get("price", 0)
                notional = price * quantity
                
                trade_time = trade_data.get("trade_time")
                trade_time_str = trade_time.isoformat() if trade_time else None
                
                all_trades.append({
                    "trade_id": trade_data.get("trade_id", 0),  # Binance trade ID
                    "order_id": trade_data.get("order_id"),  # Binance order ID
                    "order_no": str(trade_data.get("order_id", "")),  # Use order_id as order_no
                    "account_id": account.id,
                    "account_name": account.name,
                    "model": account.model,
                    "side": trade_data.get("side", "BUY"),
                    "direction": "LONG" if (trade_data.get("side", "") or "").upper() == "BUY" else "SHORT",
                    "symbol": trade_data.get("symbol", ""),
                    "market": "CRYPTO",
                    "price": price,
                    "quantity": quantity,
                    "notional": notional,
                    "commission": trade_data.get("commission", 0),
                    "trade_time": trade_time_str,
                })
                
                if account.id not in accounts_meta:
                    accounts_meta[account.id] = {
                        "account_id": account.id,
                        "name": account.name,
                        "model": account.model,
                    }
                    
        except Exception as e:
            logger.error(f"[get_completed_trades] Failed to fetch trades from Binance for account {account.name}: {e}", exc_info=True)
            continue

    # Sort all trades by time and limit
    all_trades.sort(key=lambda x: x["trade_time"] if x["trade_time"] else "", reverse=True)
    trades = all_trades[:limit]
    
    logger.info(
        f"[get_completed_trades] Fetched {len(all_trades)} total trades from Binance, returning {len(trades)} (limit: {limit})"
    )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "accounts": list(accounts_meta.values()),
        "trades": trades,
    }


@router.get("/model-chat")
def get_model_chat(
    limit: int = Query(60, ge=1, le=200),
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return recent AI decision logs as chat-style summaries.
    Decision logs are stored in metadata database.
    """
    # Get account metadata from metadata database
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.is_active == "true").all()
    else:
        accounts = db.query(Account).filter(Account.is_active == "true").all()

    if not accounts:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "entries": [],
        }

    account_ids_list = [acc.id for acc in accounts]
    all_decision_rows: List[Tuple[AIDecisionLog, Account]] = []

    # Query decisions from metadata database
    query = (
        db.query(AIDecisionLog)
        .filter(AIDecisionLog.account_id.in_(account_ids_list))
        .order_by(desc(AIDecisionLog.decision_time))
        .limit(limit * 2)  # Get more, will limit later
    )

    decision_logs = query.all()

    # Create account map for joining
    account_map = {acc.id: acc for acc in accounts}
    for log in decision_logs:
        account = account_map.get(log.account_id)
        if account:
            all_decision_rows.append((log, account))

    # Sort and limit
    all_decision_rows.sort(key=lambda x: x[0].decision_time if x[0].decision_time else datetime.min, reverse=True)
    decision_rows = all_decision_rows[:limit]

    if not decision_rows:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "entries": [],
        }

    entries: List[dict] = []

    # Get strategy configs from metadata database
    account_ids = {account.id for _, account in decision_rows}
    strategy_map = {
        cfg.account_id: cfg
        for cfg in db.query(AccountStrategyConfig).filter(AccountStrategyConfig.account_id.in_(account_ids)).all()
    }

    for log, account in decision_rows:
        strategy = strategy_map.get(account.id)
        last_trigger_iso = None
        trigger_latency = None
        trigger_mode = None
        strategy_enabled = None

        if strategy:
            trigger_mode = strategy.trigger_mode
            strategy_enabled = strategy.enabled == "true"
            if strategy.last_trigger_at:
                last_dt = strategy.last_trigger_at
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                last_trigger_iso = last_dt.isoformat()

                log_dt = log.decision_time
                if log_dt:
                    if log_dt.tzinfo is None:
                        log_dt = log_dt.replace(tzinfo=timezone.utc)
                    try:
                        trigger_latency = abs((log_dt - last_dt).total_seconds())
                    except Exception:
                        trigger_latency = None

        entries.append(
            {
                "id": log.id,
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "operation": log.operation,
                "symbol": log.symbol,
                "reason": log.reason,
                "executed": log.executed == "true",
                "prev_portion": float(log.prev_portion or 0),
                "target_portion": float(log.target_portion or 0),
                "total_balance": float(log.total_balance or 0),
                "order_id": log.order_id,
                "decision_time": log.decision_time.isoformat() if log.decision_time else None,
                "trigger_mode": trigger_mode,
                "strategy_enabled": strategy_enabled,
                "last_trigger_at": last_trigger_iso,
                "trigger_latency_seconds": trigger_latency,
                "prompt_snapshot": log.prompt_snapshot,
                "reasoning_snapshot": log.reasoning_snapshot,
                "decision_snapshot": log.decision_snapshot,
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "entries": entries,
    }


@router.get("/positions")
def get_positions_snapshot(
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return consolidated positions and cash for active AI accounts.
    Positions and cash are read from database cache (synced by background task).
    """
    # Get account metadata from metadata database
    accounts_query = db.query(Account).filter(
        Account.account_type == "AI",
        Account.is_active == "true",
    )

    if account_id:
        accounts_query = accounts_query.filter(Account.id == account_id)

    accounts = accounts_query.all()

    snapshots: List[dict] = []

    for account in accounts:
        # Get balance from cache (updated by broker_data_sync task)
        from services.broker_data_sync import AccountBalanceCache
        balance = AccountBalanceCache.get_balance(account.id)
        current_cash = float(balance) if balance is not None else 0.0

        # Get positions from database (synced by broker_data_sync task)
        db_positions = db.query(Position).filter(
            Position.account_id == account.id,
            Position.market == "CRYPTO"
        ).all()

        position_items: List[dict] = []
        total_unrealized = 0.0

        for pos in db_positions:
            quantity = float(pos.quantity)
            if quantity <= 0:
                continue
                
            avg_cost = float(pos.avg_cost)
            base_notional = quantity * avg_cost

            last_price = _get_latest_price(pos.symbol, "CRYPTO")
            if last_price is None:
                last_price = avg_cost

            current_value = last_price * quantity
            unrealized = current_value - base_notional
            total_unrealized += unrealized

            position_items.append(
                {
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "name": pos.name,
                    "market": "CRYPTO",
                    "side": "LONG" if quantity >= 0 else "SHORT",
                    "quantity": quantity,
                    "avg_cost": avg_cost,
                    "current_price": last_price,
                    "notional": base_notional,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized,
                }
            )

        total_assets = sum(p["current_value"] for p in position_items) + current_cash

        # For return calculation, since initial capital is not tracked,
        # set return to 0 (no baseline to compare against)
        initial_capital_for_calc = total_assets  # Set to total_assets so return is 0
        total_return = 0.0  # Return is 0 when no initial capital is tracked

        snapshots.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "total_unrealized_pnl": total_unrealized,
                "available_cash": current_cash,
                "positions": position_items,
                "total_assets": total_assets,
                "initial_capital": initial_capital_for_calc,
                "total_return": total_return,
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "accounts": snapshots,
    }


@router.get("/analytics")
def get_aggregated_analytics(
    account_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return leaderboard-style analytics for AI accounts.
    Data fetched from Binance in real-time.
    """
    # Get account metadata from metadata database
    accounts_query = db.query(Account).filter(
        Account.account_type == "AI",
    )

    if account_id:
        accounts_query = accounts_query.filter(Account.id == account_id)

    accounts = accounts_query.all()

    if not accounts:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "accounts": [],
            "summary": {
                "total_assets": 0.0,
                "total_pnl": 0.0,
                "total_return_pct": None,
                "total_fees": 0.0,
                "total_volume": 0.0,
                "average_sharpe_ratio": None,
            },
        }

    analytics = []
    total_assets_all = 0.0
    total_initial = 0.0
    total_fees_all = 0.0
    total_volume_all = 0.0
    sharpe_values = []

    for account in accounts:
        # Stats are calculated from Binance real-time data
        stats = _aggregate_account_stats(db, account)
        analytics.append(stats)
        total_assets_all += stats.get("total_assets") or 0.0
        total_initial += stats.get("initial_capital") or 0.0
        total_fees_all += stats.get("total_fees") or 0.0
        total_volume_all += stats.get("total_volume") or 0.0
        if stats.get("sharpe_ratio") is not None:
            sharpe_values.append(stats["sharpe_ratio"])

    analytics.sort(
        key=lambda item: item.get("total_return_pct") if item.get("total_return_pct") is not None else float("-inf"),
        reverse=True,
    )

    average_sharpe = mean(sharpe_values) if sharpe_values else None
    total_pnl_all = total_assets_all - total_initial

    logger.debug("[TOTAL_RETURN] get_aggregated_analytics summary:")
    logger.debug(f"[TOTAL_RETURN]   - total_assets_all: ${total_assets_all:.2f}")
    logger.debug(f"[TOTAL_RETURN]   - total_initial: ${total_initial:.2f} (set to total_assets, no baseline)")
    logger.debug(f"[TOTAL_RETURN]   - total_pnl_all: ${total_pnl_all:.2f} (should be 0, no initial capital tracked)")

    # Since initial capital is not tracked, return percentage should be 0
    total_return_pct = 0.0  # Return is 0 when no initial capital is tracked

    logger.debug(f"[TOTAL_RETURN]   - total_return_pct: 0.00% (no initial capital tracked)")

    summary = {
        "total_assets": total_assets_all,
        "total_pnl": total_pnl_all,
        "total_return_pct": total_return_pct,
        "total_fees": total_fees_all,
        "total_volume": total_volume_all,
        "average_sharpe_ratio": average_sharpe,
    }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "accounts": analytics,
        "summary": summary,
    }

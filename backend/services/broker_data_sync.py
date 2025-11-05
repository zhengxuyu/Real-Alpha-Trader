"""
Broker Data Synchronization Service
Periodically syncs all broker data (balance, positions, orders, trades) from Binance to database.
Frontend reads from database cache instead of calling Binance API directly.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from database.connection import SessionLocal
from database.models import Account, Order, Position, Trade
from services.binance_sync import (
    calculate_avg_cost_from_trades,
    get_binance_trade_history,
)
from services.broker_adapter import get_balance_and_positions, get_open_orders
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AccountBalanceCache:
    """In-memory cache for account balance (updated by sync task)"""
    
    _cache: Dict[int, Dict] = {}
    _lock = None
    
    @classmethod
    def _get_lock(cls):
        import threading
        if cls._lock is None:
            cls._lock = threading.Lock()
        return cls._lock
    
    @classmethod
    def set_balance(cls, account_id: int, balance: Decimal, updated_at: datetime):
        """Cache account balance"""
        with cls._get_lock():
            cls._cache[account_id] = {
                "balance": balance,
                "updated_at": updated_at,
            }
    
    @classmethod
    def get_balance(cls, account_id: int) -> Optional[Decimal]:
        """Get cached account balance"""
        with cls._get_lock():
            if account_id in cls._cache:
                return cls._cache[account_id]["balance"]
            return None


def sync_account_balance_and_positions(account: Account, db: Session) -> bool:
    """
    Sync account balance and positions from Binance to database.
    
    Args:
        account: Account to sync
        db: Database session
        
    Returns:
        True if sync successful, False otherwise
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} has no Binance API keys, skipping sync")
        return False
    
    try:
        # Get balance and positions from Binance (single API call)
        balance, positions_data = get_balance_and_positions(account)
        
        # Cache balance
        if balance is not None:
            AccountBalanceCache.set_balance(account.id, balance, datetime.now(timezone.utc))
            logger.debug(f"Synced balance for account {account.name}: ${float(balance):.2f}")
        
        # Sync positions to database
        db_positions = db.query(Position).filter(
            Position.account_id == account.id,
            Position.market == "CRYPTO"
        ).all()
        db_positions_dict = {pos.symbol.upper(): pos for pos in db_positions}
        
        # Update or create positions
        for pos_data in positions_data:
            symbol = pos_data["symbol"].upper()
            quantity = float(pos_data.get("quantity", 0))
            
            if quantity <= 0:
                # Skip positions with zero quantity
                continue
            
            db_pos = db_positions_dict.get(symbol)
            
            if db_pos:
                # Update existing position
                db_pos.quantity = quantity
                db_pos.available_quantity = float(pos_data.get("available_quantity", quantity))
                
                # Update avg_cost if missing or zero
                if float(db_pos.avg_cost) <= 0:
                    try:
                        avg_cost = calculate_avg_cost_from_trades(account, symbol)
                        if avg_cost and avg_cost > 0:
                            db_pos.avg_cost = avg_cost
                            logger.info(
                                f"Recalculated avg_cost for {account.name} {symbol}: ${avg_cost:.6f}"
                            )
                    except Exception as calc_err:
                        logger.debug(f"Failed to recalculate avg_cost for {account.name} {symbol}: {calc_err}")
            else:
                # Create new position
                avg_cost = 0.0
                try:
                    calculated_avg_cost = calculate_avg_cost_from_trades(account, symbol)
                    if calculated_avg_cost and calculated_avg_cost > 0:
                        avg_cost = calculated_avg_cost
                except Exception as calc_err:
                    logger.debug(f"Failed to calculate avg_cost for new position {account.name} {symbol}: {calc_err}")
                
                db_pos = Position(
                    version="v1",
                    account_id=account.id,
                    symbol=symbol,
                    name=symbol,
                    market="CRYPTO",
                    quantity=quantity,
                    available_quantity=float(pos_data.get("available_quantity", quantity)),
                    avg_cost=avg_cost,
                )
                db.add(db_pos)
                logger.debug(f"Created new position for {account.name} {symbol}: qty={quantity}, avg_cost=${avg_cost:.6f}")
        
        # Remove positions that no longer exist on Binance
        binance_symbols = {pos["symbol"].upper() for pos in positions_data if float(pos.get("quantity", 0)) > 0}
        for db_pos in db_positions:
            if db_pos.symbol.upper() not in binance_symbols:
                logger.info(f"Removing position {db_pos.symbol} from DB (not found on Binance) for account {account.name}")
                db.delete(db_pos)
        
        db.commit()
        logger.info(f"Synced balance and positions for account {account.name}")
        return True
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to sync balance and positions for account {account.name}: {e}", exc_info=True)
        return False


def sync_account_orders(account: Account, db: Session) -> int:
    """
    Sync open orders from Binance to database.
    
    Args:
        account: Account to sync
        db: Database session
        
    Returns:
        Number of orders synced
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} has no Binance API keys, skipping order sync")
        return 0
    
    try:
        # Get open orders from Binance
        orders_data = get_open_orders(account)
        
        # Get existing orders from database
        db_orders = db.query(Order).filter(
            Order.account_id == account.id,
            Order.market == "CRYPTO",
            Order.status.in_(["PENDING", "PARTIALLY_FILLED", "NEW"])
        ).all()
        db_orders_dict = {str(order.order_no): order for order in db_orders}
        
        synced_count = 0
        
        # Update or create orders
        for order_data in orders_data:
            order_id = str(order_data.get("order_id", ""))
            if not order_id:
                continue
            
            db_order = db_orders_dict.get(order_id)
            
            if db_order:
                # Update existing order
                db_order.symbol = order_data.get("symbol", "")
                db_order.name = order_data.get("name", db_order.symbol)
                db_order.side = order_data.get("side", "")
                db_order.order_type = order_data.get("order_type", "")
                db_order.price = float(order_data.get("price", 0)) if order_data.get("price") else None
                db_order.quantity = float(order_data.get("quantity", 0))
                db_order.filled_quantity = float(order_data.get("filled_quantity", 0))
                db_order.status = order_data.get("status", "PENDING")
            else:
                # Create new order
                db_order = Order(
                    version="v1",
                    account_id=account.id,
                    order_no=order_id,
                    symbol=order_data.get("symbol", ""),
                    name=order_data.get("name", order_data.get("symbol", "")),
                    market="CRYPTO",
                    side=order_data.get("side", ""),
                    order_type=order_data.get("order_type", ""),
                    price=float(order_data.get("price", 0)) if order_data.get("price") else None,
                    quantity=float(order_data.get("quantity", 0)),
                    filled_quantity=float(order_data.get("filled_quantity", 0)),
                    status=order_data.get("status", "PENDING"),
                )
                db.add(db_order)
            
            synced_count += 1
        
        # Mark orders as CANCELLED if they're no longer open on Binance
        binance_order_ids = {str(order.get("order_id", "")) for order in orders_data}
        for db_order in db_orders:
            if str(db_order.order_no) not in binance_order_ids:
                db_order.status = "CANCELLED"
                synced_count += 1
        
        db.commit()
        logger.info(f"Synced {synced_count} orders for account {account.name}")
        return synced_count
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to sync orders for account {account.name}: {e}", exc_info=True)
        return 0


def sync_account_trades(account: Account, db: Session, limit: int = 100) -> int:
    """
    Sync recent trades from Binance to database.
    Note: Trades are stored temporarily for frontend display, not permanently.
    
    Args:
        account: Account to sync
        db: Database session
        limit: Maximum number of recent trades to sync
        
    Returns:
        Number of trades synced
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} has no Binance API keys, skipping trade sync")
        return 0
    
    try:
        # Get trade history from Binance
        trades_data = get_binance_trade_history(account, symbol=None, limit=limit)
        
        # Get existing recent trades from database (last 24 hours)
        from datetime import timedelta
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
        
        # Note: We don't store trades permanently, just for display
        # This is a lightweight sync - we'll fetch on-demand via REST API
        # But we can optionally cache recent trades here if needed
        
        logger.debug(f"Retrieved {len(trades_data)} trades from Binance for account {account.name}")
        return len(trades_data)
        
    except Exception as e:
        logger.error(f"Failed to sync trades for account {account.name}: {e}", exc_info=True)
        return 0


def sync_account_all_data(account: Account, db: Session) -> Dict[str, int]:
    """
    Sync all broker data for an account (balance, positions, orders, trades).
    
    Args:
        account: Account to sync
        db: Database session
        
    Returns:
        Dict with sync statistics
    """
    stats = {
        "balance_synced": 0,
        "positions_synced": 0,
        "orders_synced": 0,
        "trades_retrieved": 0,
    }
    
    # Sync balance and positions
    if sync_account_balance_and_positions(account, db):
        stats["balance_synced"] = 1
        # Count positions
        positions = db.query(Position).filter(
            Position.account_id == account.id,
            Position.market == "CRYPTO"
        ).all()
        stats["positions_synced"] = len([p for p in positions if float(p.quantity) > 0])
    
    # Sync orders
    stats["orders_synced"] = sync_account_orders(account, db)
    
    # Sync trades (lightweight - just retrieve count)
    stats["trades_retrieved"] = sync_account_trades(account, db, limit=100)
    
    return stats


def sync_all_accounts_broker_data() -> Dict[str, int]:
    """
    Sync broker data for all active accounts.
    This should be called periodically (e.g., every 30 seconds to 1 minute).
    
    Returns:
        Dict with total sync statistics
    """
    db = SessionLocal()
    try:
        # Get all active accounts
        accounts = db.query(Account).filter(
            Account.is_active == "true",
            Account.account_type == "AI"
        ).all()
        
        total_stats = {
            "balance_synced": 0,
            "positions_synced": 0,
            "orders_synced": 0,
            "trades_retrieved": 0,
            "accounts_processed": 0,
        }
        
        for account in accounts:
            try:
                stats = sync_account_all_data(account, db)
                total_stats["balance_synced"] += stats["balance_synced"]
                total_stats["positions_synced"] += stats["positions_synced"]
                total_stats["orders_synced"] += stats["orders_synced"]
                total_stats["trades_retrieved"] += stats["trades_retrieved"]
                total_stats["accounts_processed"] += 1
            except Exception as account_err:
                logger.error(f"Failed to sync broker data for account {account.name}: {account_err}", exc_info=True)
                continue
        
        logger.info(
            f"Broker data sync completed: "
            f"accounts={total_stats['accounts_processed']}, "
            f"balances={total_stats['balance_synced']}, "
            f"positions={total_stats['positions_synced']}, "
            f"orders={total_stats['orders_synced']}, "
            f"trades={total_stats['trades_retrieved']}"
        )
        
        return total_stats
        
    except Exception as e:
        logger.error(f"Failed to sync broker data for all accounts: {e}", exc_info=True)
        return {
            "balance_synced": 0,
            "positions_synced": 0,
            "orders_synced": 0,
            "trades_retrieved": 0,
            "accounts_processed": 0,
        }
    finally:
        db.close()


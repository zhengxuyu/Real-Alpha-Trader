"""
Position Synchronization Service
Periodically syncs database positions with Binance actual positions to ensure data consistency.
This addresses the audit report finding about potential inconsistency between DB and Binance.
"""

import logging
from typing import Dict, List

from database.connection import SessionLocal
from database.models import Account, Position
from services.broker_adapter import get_balance_and_positions
from services.trading_commands import POSITION_SYNC_THRESHOLD
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def sync_account_positions_with_binance(account: Account, db: Session) -> Dict[str, int]:
    """
    Sync database positions with Binance actual positions for a single account.

    Args:
        account: Account to sync
        db: Database session

    Returns:
        Dict with sync statistics: {"synced": count, "removed": count, "added": count}
    """
    if not account.binance_api_key or not account.binance_secret_key:
        logger.debug(f"Account {account.name} (ID: {account.id}) has no Binance API keys, skipping sync")
        return {"synced": 0, "removed": 0, "added": 0}

    try:
        # Get actual positions from Binance (single API call)
        _, binance_positions = get_balance_and_positions(account)

        # Create a dict keyed by symbol for easy lookup
        binance_positions_dict = {}
        for pos in binance_positions:
            symbol = pos.get("symbol", "").upper()
            if symbol:
                binance_positions_dict[symbol] = {
                    "quantity": float(pos.get("quantity", 0)),
                    "available_quantity": float(pos.get("available_quantity", 0)),
                    "avg_cost": float(pos.get("avg_cost", 0)),
                }

        # Get database positions for this account
        db_positions = db.query(Position).filter(Position.account_id == account.id, Position.market == "CRYPTO").all()

        synced_count = 0
        removed_count = 0
        added_count = 0

        # Update or remove existing database positions
        for db_pos in db_positions:
            symbol = db_pos.symbol.upper()

            if symbol in binance_positions_dict:
                # Position exists on Binance - sync it
                binance_pos = binance_positions_dict[symbol]

                # Only update if there's a significant difference (avoid unnecessary updates)
                qty_diff = abs(float(db_pos.quantity) - binance_pos["quantity"])
                if qty_diff > POSITION_SYNC_THRESHOLD:  # Use constant
                    db_pos.quantity = binance_pos["quantity"]
                    db_pos.available_quantity = binance_pos["available_quantity"]
                    # Update avg_cost if available (Binance may not always provide this)
                    if binance_pos.get("avg_cost", 0) > 0:
                        db_pos.avg_cost = binance_pos["avg_cost"]
                    synced_count += 1
                    logger.debug(
                        f"Synced position {symbol} for account {account.name}: "
                        f"DB={db_pos.quantity} -> Binance={binance_pos['quantity']}"
                    )
                else:
                    # Position is in sync
                    pass

                # Remove from dict to track which positions we've processed
                del binance_positions_dict[symbol]
            else:
                # Position exists in DB but not on Binance - remove it
                logger.info(f"Removing position {symbol} from DB (not found on Binance) for account {account.name}")
                db.delete(db_pos)
                removed_count += 1

        # Add new positions that exist on Binance but not in DB
        for symbol, binance_pos in binance_positions_dict.items():
            # Find position name (use symbol as fallback)
            position = Position(
                version="v1",
                account_id=account.id,
                symbol=symbol,
                name=symbol,  # Use symbol as name if we don't have mapping
                market="CRYPTO",
                quantity=binance_pos["quantity"],
                available_quantity=binance_pos["available_quantity"],
                avg_cost=binance_pos.get("avg_cost", 0),
            )
            db.add(position)
            added_count += 1
            logger.debug(
                f"Added new position {symbol} from Binance for account {account.name}: "
                f"quantity={binance_pos['quantity']}"
            )

        db.commit()

        logger.info(
            f"Position sync completed for account {account.name}: "
            f"synced={synced_count}, removed={removed_count}, added={added_count}"
        )

        return {
            "synced": synced_count,
            "removed": removed_count,
            "added": added_count,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to sync positions for account {account.name}: {e}", exc_info=True)
        return {"synced": 0, "removed": 0, "added": 0}


def sync_all_active_accounts_positions() -> Dict[str, int]:
    """
    Sync positions for all active accounts with Binance.
    This should be called periodically (e.g., every 5-10 minutes) to maintain data consistency.

    Returns:
        Dict with total sync statistics across all accounts
    """
    db = SessionLocal()
    try:
        # Get all active accounts
        accounts = db.query(Account).filter(Account.is_active == "true", Account.account_type == "AI").all()

        total_stats = {"synced": 0, "removed": 0, "added": 0}

        for account in accounts:
            try:
                stats = sync_account_positions_with_binance(account, db)
                total_stats["synced"] += stats["synced"]
                total_stats["removed"] += stats["removed"]
                total_stats["added"] += stats["added"]
            except Exception as account_err:
                logger.error(f"Failed to sync positions for account {account.name}: {account_err}", exc_info=True)
                # Continue with next account
                continue

        logger.info(
            f"Position sync completed for all accounts: "
            f"total synced={total_stats['synced']}, removed={total_stats['removed']}, added={total_stats['added']}"
        )

        return total_stats

    except Exception as e:
        logger.error(f"Failed to sync positions for all accounts: {e}", exc_info=True)
        return {"synced": 0, "removed": 0, "added": 0}
    finally:
        db.close()

"""
Record account asset snapshots on price updates.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

# Dynamic import to avoid circular dependency with api.ws
# Note: api.ws imports scheduler, scheduler may import services that import asset_snapshot_service
from database.connection import SessionLocal
from database.models import Account, AccountAssetSnapshot
from services.asset_curve_calculator import invalidate_asset_curve_cache
from services.broker_adapter import get_balance_and_positions
from services.market_data import get_last_price
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SNAPSHOT_RETENTION_HOURS = 24 * 30  # Keep 30 days of asset snapshots


def _get_active_accounts(db: Session) -> List[Account]:
    return db.query(Account).filter(Account.is_active == "true", Account.account_type == "AI").all()


def handle_price_update(event: Dict[str, Any]) -> None:
    """Persist account asset snapshots based on the latest price event."""
    session = SessionLocal()
    try:
        accounts = _get_active_accounts(session)
        if not accounts:
            return

        trigger_symbol = event.get("symbol")
        trigger_market = event.get("market", "CRYPTO")
        event_time: datetime = event.get("event_time") or datetime.now(tz=timezone.utc)

        snapshots: List[AccountAssetSnapshot] = []
        symbol_totals = defaultdict(float)
        accounts_payload: List[Dict[str, Any]] = []
        total_available_cash = 0.0
        total_frozen_cash = 0.0
        total_positions_value = 0.0
        price_cache: Dict[str, float] = {}

        for account in accounts:
            try:
                # Get balance and positions from Binance in real-time (single API call)
                # This ensures we use the actual current positions, not stale database records
                try:
                    balance, positions_data = get_balance_and_positions(account)
                    available_cash = float(balance) if balance is not None else 0.0
                except Exception:
                    available_cash = 0.0
                    positions_data = []

                frozen_cash = 0.0  # Not tracked - all data from Binance

                # Calculate positions value from Binance real-time data
                positions_value = 0.0
                for pos in positions_data:
                    symbol_key = (pos.get("symbol") or "").upper()
                    if not symbol_key:
                        continue
                    market_key = "CRYPTO"  # Binance positions are always CRYPTO
                    cache_key = f"{symbol_key}.{market_key}"

                    try:
                        if cache_key in price_cache:
                            price = price_cache[cache_key]
                        else:
                            price = float(get_last_price(symbol_key, market_key))
                            price_cache[cache_key] = price
                    except Exception as price_err:
                        logger.debug(
                            "Skipping valuation for %s.%s: %s",
                            symbol_key,
                            market_key,
                            price_err,
                        )
                        continue

                    quantity = float(pos.get("quantity", 0) or 0)
                    current_value = price * quantity
                    positions_value += current_value
                    symbol_totals[symbol_key] += current_value

                total_assets = positions_value + available_cash

                total_available_cash += available_cash
                total_frozen_cash += frozen_cash
                total_positions_value += positions_value

                accounts_payload.append(
                    {
                        "account_id": account.id,
                        "account_name": account.name,
                        "model": account.model,
                        "available_cash": round(available_cash, 2),
                        "frozen_cash": round(frozen_cash, 2),
                        "positions_value": round(positions_value, 2),
                        "total_assets": round(total_assets, 2),
                    }
                )

                snapshot = AccountAssetSnapshot(
                    account_id=account.id,
                    total_assets=total_assets,
                    cash=available_cash,
                    positions_value=positions_value,
                    trigger_symbol=trigger_symbol,
                    trigger_market=trigger_market,
                    event_time=event_time,
                )
                snapshots.append(snapshot)
            except Exception as account_err:
                logger.warning(
                    "Failed to compute snapshot for account %s: %s",
                    account.name,
                    account_err,
                )

        if snapshots:
            session.bulk_save_objects(snapshots)
            session.commit()
            invalidate_asset_curve_cache()

        # Use dynamic import to avoid circular dependency with api.ws
        try:
            from api.ws import broadcast_arena_asset_update, manager

            if manager.has_connections():
                update_payload = {
                    "generated_at": event_time.isoformat(),
                    "totals": {
                        "available_cash": round(total_available_cash, 2),
                        "frozen_cash": round(total_frozen_cash, 2),
                        "positions_value": round(total_positions_value, 2),
                        "total_assets": round(total_available_cash + total_frozen_cash + total_positions_value, 2),
                    },
                    "symbols": {symbol: round(value, 2) for symbol, value in symbol_totals.items()},
                    "accounts": accounts_payload,
                }
                try:
                    manager.schedule_task(broadcast_arena_asset_update(update_payload))
                except Exception as broadcast_err:
                    logger.debug("Failed to schedule arena asset broadcast: %s", broadcast_err)
        except ImportError:
            # If api.ws is not available, skip broadcast
            pass

        _purge_old_snapshots(session, cutoff_hours=SNAPSHOT_RETENTION_HOURS)
    except Exception as err:
        session.rollback()
        logger.error("Failed to record asset snapshots: %s", err)
    finally:
        session.close()


def _purge_old_snapshots(session: Session, cutoff_hours: int) -> None:
    """Remove snapshots older than retention window to control storage."""
    cutoff_time = datetime.now(tz=timezone.utc) - timedelta(hours=cutoff_hours)
    deleted = (
        session.query(AccountAssetSnapshot)
        .filter(AccountAssetSnapshot.event_time < cutoff_time)
        .delete(synchronize_session=False)
    )
    if deleted:
        session.commit()
        logger.debug("Purged %d old asset snapshots", deleted)

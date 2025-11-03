import logging
from datetime import datetime, timezone
from typing import List, Optional

from database.models import AccountStrategyConfig
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_strategy_by_account(db: Session, account_id: int) -> Optional[AccountStrategyConfig]:
    return (
        db.query(AccountStrategyConfig)
        .filter(AccountStrategyConfig.account_id == account_id)
        .first()
    )


def list_strategies(db: Session) -> List[AccountStrategyConfig]:
    return db.query(AccountStrategyConfig).all()


def upsert_strategy(
    db: Session,
    account_id: int,
    trigger_mode: str,
    interval_seconds: Optional[int] = None,
    tick_batch_size: Optional[int] = None,
    enabled: bool = True,
) -> AccountStrategyConfig:
    strategy = get_strategy_by_account(db, account_id)
    if strategy is None:
        logger.info(f"[STRATEGY_REPO] Creating new strategy config for account {account_id}")
        strategy = AccountStrategyConfig(account_id=account_id)
        db.add(strategy)
    else:
        logger.info(f"[STRATEGY_REPO] Updating existing strategy config for account {account_id}")

    old_trigger_mode = strategy.trigger_mode
    old_interval_seconds = strategy.interval_seconds
    old_tick_batch_size = strategy.tick_batch_size
    old_enabled = strategy.enabled

    strategy.trigger_mode = trigger_mode
    strategy.interval_seconds = interval_seconds
    strategy.tick_batch_size = tick_batch_size
    strategy.enabled = "true" if enabled else "false"

    logger.info(f"[STRATEGY_REPO] Strategy changes: trigger_mode={old_trigger_mode}->{trigger_mode}, interval_seconds={old_interval_seconds}->{interval_seconds}, tick_batch_size={old_tick_batch_size}->{tick_batch_size}, enabled={old_enabled}->{strategy.enabled}")

    db.commit()
    db.refresh(strategy)
    
    logger.info(f"[STRATEGY_REPO] Strategy committed and refreshed: trigger_mode={strategy.trigger_mode}, enabled={strategy.enabled}")
    return strategy


def set_last_trigger(db: Session, account_id: int, when) -> None:
    strategy = get_strategy_by_account(db, account_id)
    if not strategy:
        return
    when_to_store = when
    if isinstance(when, datetime) and when.tzinfo is not None:
        when_to_store = when.astimezone(timezone.utc).replace(tzinfo=None)
    strategy.last_trigger_at = when_to_store
    db.commit()

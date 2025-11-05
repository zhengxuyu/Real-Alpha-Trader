import uuid
from decimal import Decimal

from database.models import (
    US_COMMISSION_RATE,
    US_LOT_SIZE,
    US_MIN_COMMISSION,
    US_MIN_ORDER_QUANTITY,
    Account,
    Order,
    Position,
    User,
)
from sqlalchemy.orm import Session

from .market_data import get_last_price


def _calc_commission(notional: Decimal) -> Decimal:
    pct_fee = notional * Decimal(str(US_COMMISSION_RATE))
    min_fee = Decimal(str(US_MIN_COMMISSION))
    return max(pct_fee, min_fee)


def place_and_execute(
    db: Session,
    user: User,
    symbol: str,
    name: str,
    market: str,
    side: str,
    order_type: str,
    price: float | None,
    quantity: int,
) -> Order:
    # Only support US market
    if market != "US":
        raise ValueError("Only US market is supported")

    # Get user's active account (required for Order, Trade, Position models)
    account = (
        db.query(Account)
        .filter(Account.user_id == user.id, Account.is_active == "true")
        .first()
    )
    if not account:
        raise ValueError("Active trading account not found for user")

    # Adjust quantity to lot size
    if quantity % US_LOT_SIZE != 0:
        raise ValueError(f"quantity must be a multiple of lot_size={US_LOT_SIZE}")
    if quantity < US_MIN_ORDER_QUANTITY:
        raise ValueError(f"quantity must be >= min_order_quantity={US_MIN_ORDER_QUANTITY}")

    exec_price = Decimal(str(price if (order_type == "LIMIT" and price) else get_last_price(symbol, market)))

    order = Order(
        version="v1",
        account_id=account.id,  # Fixed: use account_id instead of user_id
        order_no=uuid.uuid4().hex[:16],
        symbol=symbol,
        name=name,
        market=market,
        side=side,
        order_type=order_type,
        price=float(exec_price),
        quantity=quantity,
        filled_quantity=0,
        status="PENDING",
    )
    db.add(order)
    db.flush()

    notional = exec_price * Decimal(quantity)
    commission = _calc_commission(notional)

    if side == "BUY":
        cash_needed = notional + commission
        if Decimal(str(user.current_cash)) < cash_needed:
            raise ValueError("Insufficient USDT")
        user.current_cash = float(Decimal(str(user.current_cash)) - cash_needed)

        # position update (avg cost)
        pos = (
            db.query(Position)
            .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
            .first()
        )
        if not pos:
            pos = Position(
                version="v1",
                account_id=account.id,  # Fixed: use account_id instead of user_id
                symbol=symbol,
                name=name,
                market=market,
                quantity=0,
                available_quantity=0,
                avg_cost=0,
            )
            db.add(pos)
            db.flush()
        new_qty = int(pos.quantity) + quantity
        new_cost = (Decimal(str(pos.avg_cost)) * Decimal(int(pos.quantity)) + notional) / Decimal(new_qty)
        pos.quantity = new_qty
        pos.available_quantity = int(pos.available_quantity) + quantity
        pos.avg_cost = float(new_cost)
    else:  # SELL
        pos = (
            db.query(Position)
            .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
            .first()
        )
        if not pos or int(pos.available_quantity) < quantity:
            raise ValueError("Insufficient position to sell")
        pos.quantity = int(pos.quantity) - quantity
        pos.available_quantity = int(pos.available_quantity) - quantity

        cash_gain = notional - commission
        user.current_cash = float(Decimal(str(user.current_cash)) + cash_gain)

    # Note: Trade records are now fetched dynamically from Binance API,
    # not stored in database (similar to positions). This ensures data consistency
    # and avoids duplicate storage.

    order.filled_quantity = quantity
    order.status = "FILLED"

    db.commit()
    db.refresh(order)
    return order

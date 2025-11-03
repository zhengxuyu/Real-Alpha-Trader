"""
Trading Commands Service - Handles order execution and trading logic
"""

import logging
import random
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from database.connection import SessionLocal
from database.models import CRYPTO_COMMISSION_RATE, CRYPTO_MIN_COMMISSION, Account, Position
from services.ai_decision_service import (
    SUPPORTED_SYMBOLS,
    _get_portfolio_data,
    call_ai_for_decision,
    get_active_ai_accounts,
    save_ai_decision,
)
from services.asset_calculator import calc_positions_value
from services.market_data import get_last_price
from services.broker_adapter import execute_order, get_balance_and_positions
from services.order_matching import check_and_execute_order, create_order
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

AI_TRADING_SYMBOLS: List[str] = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]

# Constants for trade verification and quantity calculation
SLIPPAGE_TOLERANCE = 0.95  # 5% slippage tolerance for trade verification
MIN_CRYPTO_QUANTITY = Decimal("0.000001")  # Minimum crypto quantity
POSITION_FULLY_SOLD_THRESHOLD = Decimal("0.000001")  # Threshold for considering position fully sold

# Constants for API rate limiting and caching
CACHE_TTL_SECONDS = 5.0  # Cache TTL for balance and positions (seconds)
RATE_LIMIT_INTERVAL_SECONDS = 10.0  # Minimum interval between Binance API calls (seconds)
POSITION_SYNC_THRESHOLD = 0.001  # Threshold for position quantity difference to trigger sync


def _execute_real_trade(account, symbol: str, side: str, quantity: float, price: float) -> Tuple[bool, Optional[str]]:
    """
    Execute real trade using broker interface.

    Args:
        account: Account object with broker API credentials
        symbol: Trading symbol
        side: Order side ("BUY" or "SELL")
        quantity: Order quantity
        price: Order price

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message or order_id)
    """
    success, result, _ = execute_order(
        account=account, symbol=symbol, side=side, quantity=quantity, price=price, ordertype="market"
    )
    return success, result


def _estimate_buy_cash_needed(price: float, quantity: float) -> Decimal:
    """Estimate cash required for a BUY including commission."""
    notional = Decimal(str(price)) * Decimal(str(quantity))
    commission = max(
        notional * Decimal(str(CRYPTO_COMMISSION_RATE)),
        Decimal(str(CRYPTO_MIN_COMMISSION)),
    )
    return notional + commission


def _get_market_prices(symbols: List[str]) -> Dict[str, float]:
    """Get latest prices for given symbols"""
    prices = {}
    for symbol in symbols:
        try:
            price = float(get_last_price(symbol, "CRYPTO"))
            if price > 0:
                prices[symbol] = price
        except Exception as err:
            logger.warning(f"Failed to get price for {symbol}: {err}")
    return prices


def get_account_balance_safe(account: Account, context: str = "") -> float:
    """
    Safely get account balance with error handling.
    Extracted to reduce code duplication (Duplicate Code smell fix).

    Args:
        account: Account object
        context: Context string for logging (e.g., "creating order", "executing trade")

    Returns:
        Account balance as float, or 0.0 if retrieval fails
    """
    try:
        balance, _ = get_balance_and_positions(account)
        current_cash = float(balance) if balance is not None else 0.0
    except (ConnectionError, TimeoutError, ValueError) as e:
        logger.warning(f"Failed to get balance for {account.name} {context}: {e}")
        current_cash = 0.0
    except Exception as e:
        logger.error(f"Unexpected error getting balance for {account.name} {context}: {e}", exc_info=True)
        current_cash = 0.0
    return current_cash


def find_position_by_symbol(positions: List[Dict], symbol: str) -> Optional[Dict]:
    """
    Find position by symbol (case-insensitive).
    Extracted to reduce code duplication (Duplicate Code smell fix).

    Args:
        positions: List of position dictionaries
        symbol: Symbol to search for

    Returns:
        Position dictionary if found, None otherwise
    """
    for pos in positions:
        symbol_key = pos.get("symbol", "").upper()
        if symbol_key == symbol.upper():
            return pos
    return None


def _validate_ai_decision(decision: Dict, account_name: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Validate AI decision structure and extract fields.
    Extracted from place_ai_driven_crypto_order to reduce method length (Long Method smell fix).

    Args:
        decision: AI decision dictionary
        account_name: Account name for logging

    Returns:
        Tuple of (operation, symbol, target_portion, reason) if valid, None otherwise
    """
    if not decision or not isinstance(decision, dict):
        logger.warning(f"Failed to get AI decision for {account_name}, skipping")
        return None

    operation = decision.get("operation", "").lower() if decision.get("operation") else ""
    symbol = decision.get("symbol", "").upper() if decision.get("symbol") else ""
    target_portion = (
        float(decision.get("target_portion_of_balance", 0))
        if decision.get("target_portion_of_balance") is not None
        else 0
    )
    reason = decision.get("reason", "No reason provided")

    if operation not in ["buy", "sell", "hold"]:
        logger.warning(f"Invalid operation '{operation}' from AI for {account_name}, skipping")
        return None

    if operation == "hold":
        logger.info(f"AI decided to HOLD for {account_name}")
        return ("hold", symbol, target_portion, reason)

    if symbol not in SUPPORTED_SYMBOLS:
        logger.warning(f"Invalid symbol '{symbol}' from AI for {account_name}, skipping")
        return None

    if target_portion <= 0 or target_portion > 1:
        logger.warning(f"Invalid target_portion {target_portion} from AI for {account_name}, skipping")
        return None

    return (operation, symbol, target_portion, reason)


def _calculate_buy_quantity(
    account: Account, symbol: str, price: float, target_portion: float, available_cash_dec: Decimal
) -> Optional[float]:
    """
    Calculate buy quantity based on available cash and target portion.
    Extracted from place_ai_driven_crypto_order to reduce method length (Long Method smell fix).

    Args:
        account: Account object
        symbol: Trading symbol
        price: Current price
        target_portion: Target portion of balance to use
        available_cash_dec: Available cash as Decimal

    Returns:
        Calculated quantity as float, or None if calculation fails
    """
    # Calculate quantity based on available cash and target portion
    # Keep calculations in Decimal for precision
    order_value = available_cash_dec * Decimal(str(target_portion))
    quantity_decimal = order_value / Decimal(str(price))
    # Convert to float for final use, round to 6 decimal places for crypto
    quantity = round(float(quantity_decimal), 6)
    # Ensure minimum quantity if original was positive
    if quantity <= 0 and quantity_decimal > 0:
        quantity = float(MIN_CRYPTO_QUANTITY)

    if quantity <= 0:
        logger.info(f"Calculated BUY quantity <= 0 for {symbol} for {account.name}, skipping")
        return None

    cash_needed = _estimate_buy_cash_needed(price, quantity)
    if available_cash_dec < cash_needed:
        logger.info(
            "Skipping BUY for %s due to insufficient cash after fees: need $%.2f, current cash $%.2f",
            account.name,
            float(cash_needed),
            float(available_cash_dec),
        )
        return None

    return quantity


def _calculate_sell_quantity(
    account: Account, symbol: str, positions: List[Dict], target_portion: float
) -> Optional[Tuple[float, float]]:
    """
    Calculate sell quantity based on available position and target portion.
    Extracted from place_ai_driven_crypto_order to reduce method length (Long Method smell fix).

    Args:
        account: Account object
        symbol: Trading symbol
        positions: List of position dictionaries
        target_portion: Target portion of position to sell

    Returns:
        Tuple of (quantity, available_quantity) if valid, None otherwise
    """
    # Find position for this symbol
    position = find_position_by_symbol(positions, symbol)

    if not position:
        logger.info(f"No position available to SELL for {symbol} for {account.name}, skipping")
        return None

    try:
        available_qty_value = position.get("available_quantity", 0)
        available_quantity = float(available_qty_value) if available_qty_value else 0.0
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid available_quantity type for position {symbol}: {e}")
        available_quantity = 0.0

    if available_quantity <= 0:
        logger.info(f"No position available to SELL for {symbol} for {account.name}, skipping")
        return None

    quantity = max(float(MIN_CRYPTO_QUANTITY), available_quantity * target_portion)

    if quantity > available_quantity:
        quantity = available_quantity

    return (quantity, available_quantity)


def _verify_trade_execution(
    account: Account,
    symbol: str,
    side: str,
    quantity: float,
    previous_quantity: Optional[float],
    order_id: Optional[str],
) -> None:
    """
    Verify trade execution by checking broker positions.
    Extracted to reduce code duplication (Duplicate Code smell fix).

    Args:
        account: Account object
        symbol: Trading symbol
        side: Order side ("BUY" or "SELL")
        quantity: Trade quantity
        previous_quantity: Previous position quantity (for SELL verification)
        order_id: Broker order ID for logging
    """
    try:
        _, positions_after = get_balance_and_positions(account)
        position = find_position_by_symbol(positions_after, symbol)

        if side == "BUY":
            # Verify: check if we actually have the position now
            if position:
                try:
                    pos_qty = float(position.get("quantity", 0))
                except (ValueError, TypeError):
                    logger.warning(f"Invalid quantity type in position verification for {symbol}")
                    pos_qty = 0

                if pos_qty >= quantity * SLIPPAGE_TOLERANCE:  # Allow 5% tolerance for slippage
                    logger.debug(f"Trade verified: position {symbol} quantity={pos_qty}")
                else:
                    logger.warning(
                        f"Trade verification failed: {symbol} position found but quantity={pos_qty} < expected={quantity * SLIPPAGE_TOLERANCE}. "
                        f"Order ID={order_id}. This may indicate a partial fill."
                    )
            else:
                logger.warning(
                    f"Trade verification failed: {symbol} position not found after BUY. "
                    f"Order ID={order_id}. This may indicate a partial fill or order failure."
                )
        elif side == "SELL":
            # Verify: check if position was reduced
            if position:
                try:
                    pos_qty = float(position.get("quantity", 0))
                except (ValueError, TypeError):
                    logger.warning(f"Invalid quantity type in position verification for {symbol}")
                    pos_qty = 0

                if previous_quantity is not None:
                    expected_qty = previous_quantity - quantity
                    # Allow some tolerance for rounding/slippage
                    if pos_qty > expected_qty + quantity * (1 - SLIPPAGE_TOLERANCE):
                        logger.warning(
                            f"Trade verification warning: {symbol} position={pos_qty}, "
                            f"expected={expected_qty}. Order ID={order_id}"
                        )
                    else:
                        logger.debug(f"Trade verified: position {symbol} reduced to {pos_qty}")
            else:
                # If position not found and we sold all, that's expected and successful
                if previous_quantity is not None:
                    remaining_after_sell = previous_quantity - quantity
                    if remaining_after_sell <= float(POSITION_FULLY_SOLD_THRESHOLD):
                        logger.debug(f"Position {symbol} fully sold, verification successful")
                    else:
                        logger.warning(
                            f"Position {symbol} not found after SELL but expected remaining={remaining_after_sell}. "
                            f"Order ID={order_id}"
                        )
    except Exception as verify_err:
        # Don't fail the trade if verification fails, but log it
        logger.warning(f"Failed to verify trade execution for {account.name} {side} {symbol}: {verify_err}")


def _select_side(db: Session, account: Account, symbol: str, max_value: float) -> Optional[Tuple[str, int]]:
    """Select random trading side and quantity for legacy random trading"""
    market = "CRYPTO"
    try:
        price = float(get_last_price(symbol, market))
    except Exception as err:
        logger.warning("Cannot get price for %s: %s", symbol, err)
        return None

    if price <= 0:
        logger.debug("%s returned non-positive price %s", symbol, price)
        return None

    max_quantity_by_value = int(Decimal(str(max_value)) // Decimal(str(price)))
    position = (
        db.query(Position)
        .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
        .first()
    )
    available_quantity = int(position.available_quantity) if position else 0

    choices = []

    # Get balance from broker in real-time (using extracted function)
    current_cash = get_account_balance_safe(account, "selecting side")

    if current_cash >= price and max_quantity_by_value >= 1:
        choices.append(("BUY", max_quantity_by_value))

    if available_quantity > 0:
        max_sell_quantity = min(
            available_quantity, max_quantity_by_value if max_quantity_by_value >= 1 else available_quantity
        )
        if max_sell_quantity >= 1:
            choices.append(("SELL", max_sell_quantity))

    if not choices:
        return None

    side, max_qty = random.choice(choices)
    quantity = random.randint(1, max_qty)
    return side, quantity


def place_ai_driven_crypto_order(max_ratio: float = 0.2, account_ids: Optional[Iterable[int]] = None) -> None:
    """Place crypto order based on AI model decision. Only real trading is supported.

    Args:
        max_ratio: maximum portion of portfolio to allocate per trade.
        account_ids: optional iterable of account IDs to process (defaults to all active accounts).
    """
    # Accounts are stored in metadata database
    db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping AI trading")
            return

        if account_ids is not None:
            id_set = {int(acc_id) for acc_id in account_ids}
            accounts = [acc for acc in accounts if acc.id in id_set]
            if not accounts:
                logger.debug("No matching accounts for provided IDs: %s", account_ids)
                return

        # Get latest market prices once for all accounts
        prices = _get_market_prices(AI_TRADING_SYMBOLS)
        if not prices:
            logger.warning("Failed to fetch market prices, skipping AI trading")
            return

        # Iterate through all active accounts
        for account in accounts:
            logger.info(f"Processing AI trading for account: {account.name}")

            # Use metadata database (trading data is fetched from Binance in real-time)
            account_db = db
            try:
                # Get portfolio data for this account (from account's database)
                portfolio = _get_portfolio_data(account_db, account)

                if portfolio["total_assets"] <= 0:
                    logger.debug(f"Account {account.name} has non-positive total assets, skipping")
                    continue

                # Call AI for trading decision (uses db for account metadata)
                decision = call_ai_for_decision(db, account, portfolio, prices)

                # Validate and extract decision fields (extracted method)
                decision_result = _validate_ai_decision(decision, account.name)
                if decision_result is None:
                    # Only save decision if it's not None (None means AI API call failed)
                    if decision is not None:
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue

                operation, symbol, target_portion, reason = decision_result
                logger.info(
                    f"AI decision for {account.name}: {operation} {symbol} (portion: {target_portion:.2%}) - {reason}"
                )

                if operation == "hold":
                    # Save hold decision (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=True)
                    continue

                # Get current price
                price = prices.get(symbol)
                if not price or price <= 0:
                    logger.warning(f"Invalid price for {symbol} for {account.name}, skipping")
                    # Save decision with execution failure (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue

                # Calculate quantity based on operation (extracted methods)
                quantity = None
                side = None
                available_quantity = None

                if operation == "buy":
                    # Get current cash from broker in real-time (single API call)
                    balance, _ = get_balance_and_positions(account)
                    if balance is None:
                        logger.warning(f"Failed to get balance from Binance for {account.name}, skipping")
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue

                    # Calculate buy quantity (extracted method)
                    quantity = _calculate_buy_quantity(account, symbol, price, target_portion, balance)
                    if quantity is None:
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue

                    side = "BUY"

                elif operation == "sell":
                    # Get positions from broker in real-time (already fetched with balance)
                    _, positions = get_balance_and_positions(account)

                    # Calculate sell quantity (extracted method)
                    result = _calculate_sell_quantity(account, symbol, positions, target_portion)
                    if result is None:
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue

                    quantity, available_quantity = result
                    side = "SELL"
                else:
                    continue

                # Execute real trade directly on Binance (no database order creation needed)
                # All trading data is fetched from Binance in real-time
                executed = False
                order_id = None

                # Execute real trade on Binance
                if not account.binance_api_key or not account.binance_secret_key:
                    logger.warning(f"Account {account.name} does not have Binance API keys configured, skipping trade")
                    executed = False
                    order_id = "Missing API keys"
                else:
                    logger.info(f"Executing REAL trade for {account.name}: {side} {quantity} {symbol} @ {price}")
                    executed, order_id = _execute_real_trade(
                        account=account, symbol=symbol, side=side, quantity=quantity, price=price
                    )

                if executed:
                    logger.info(
                        f"REAL trade executed on Binance: account={account.name} {side} {symbol} "
                        f"quantity={quantity} order_id={order_id}"
                    )

                    # Verify trade execution (extracted method)
                    _verify_trade_execution(account, symbol, side, quantity, available_quantity, order_id)

                    # Save successful decision
                    save_ai_decision(account_db, account, decision, portfolio, executed=True)
                else:
                    logger.warning(
                        f"REAL trade failed on Binance: account={account.name} {side} {symbol} "
                        f"quantity={quantity} error={order_id}"
                    )
                    # Save failed decision
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)

            except Exception as account_err:
                logger.error(
                    f"AI-driven order placement failed for account {account.name}: {account_err}", exc_info=True
                )
                # Continue with next account even if one fails
                continue

    except Exception as err:
        logger.error(f"AI-driven order placement failed: {err}", exc_info=True)
    finally:
        db.close()


def place_random_crypto_order(max_ratio: float = 0.2) -> None:
    """Legacy random order placement (kept for backward compatibility)"""
    db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping auto order placement")
            return

        # For legacy compatibility, just pick a random account from the list
        account = random.choice(accounts)

        positions_value = calc_positions_value(db, account.id)
        # Get balance from broker in real-time (using extracted function)
        current_cash = get_account_balance_safe(account, "placing random order")
        total_assets = positions_value + current_cash

        if total_assets <= 0:
            logger.debug("Account %s total assets non-positive, skipping auto order placement", account.name)
            return

        max_order_value = total_assets * max_ratio
        if max_order_value <= 0:
            logger.debug("Account %s maximum order amount is 0, skipping", account.name)
            return

        symbol = random.choice(list(SUPPORTED_SYMBOLS.keys()))
        side_info = _select_side(db, account, symbol, max_order_value)
        if not side_info:
            logger.debug("Account %s has no executable direction for %s, skipping", account.name, symbol)
            return

        side, quantity = side_info
        name = SUPPORTED_SYMBOLS[symbol]

        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=name,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
        )

        db.commit()
        db.refresh(order)

        executed = check_and_execute_order(db, order)
        if executed:
            db.refresh(order)
            logger.info(
                "Auto order executed: account=%s %s %s %s quantity=%s",
                account.name,
                side,
                symbol,
                order.order_no,
                quantity,
            )
        else:
            logger.info(
                "Auto order created: account=%s %s %s quantity=%s order_id=%s",
                account.name,
                side,
                symbol,
                quantity,
                order.order_no,
            )

    except Exception as err:
        logger.error("Auto order placement failed: %s", err)
        db.rollback()
    finally:
        db.close()


AUTO_TRADE_JOB_ID = "auto_crypto_trade"
AI_TRADE_JOB_ID = "ai_crypto_trade"

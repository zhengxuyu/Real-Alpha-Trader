#!/usr/bin/env python3
"""
Test script to buy 5 USDT worth of BTC
"""
import sys
import os

# Change to backend directory
backend_dir = os.path.join(os.path.dirname(__file__), "backend")
os.chdir(backend_dir)
sys.path.insert(0, backend_dir)

from database.connection import SessionLocal
from database.models import User, Account
from services.market_data import get_last_price
from services.broker_adapter import get_balance_and_positions
from services.order_matching import create_order


def test_buy_btc():
    """Test buying 5 USDT worth of BTC"""
    db = SessionLocal()

    try:
        # Get default user (usually user_id=1)
        user = db.query(User).filter(User.is_active == "true").first()
        if not user:
            print("❌ No active user found")
            return

        print(f"✅ Found user: {user.username} (ID: {user.id})")

        # Get active account
        account = db.query(Account).filter(Account.user_id == user.id, Account.is_active == "true").first()

        if not account:
            print("❌ No active account found for user")
            return

        print(f"✅ Found account: {account.name} (ID: {account.id})")

        # Check if Binance API keys are configured
        if not account.binance_api_key or not account.binance_secret_key:
            print("❌ Binance API keys not configured for this account")
            print("   Please configure Binance API keys in the settings")
            return

        # Get current BTC price
        try:
            btc_price = get_last_price("BTC", "CRYPTO")
            print(f"✅ Current BTC price: ${btc_price:,.2f}")
        except Exception as e:
            print(f"❌ Failed to get BTC price: {e}")
            return

        # Binance minimum order value requirement (NOTIONAL filter)
        min_order_value = 10.0  # Binance typically requires minimum 10 USDT

        # Calculate BTC quantity for target amount
        target_usdt = 10.0  # Test buying 10 USDT worth of BTC
        if target_usdt < min_order_value:
            print(f"⚠️  Warning: Target amount {target_usdt} USDT is below Binance minimum {min_order_value} USDT")
            print(f"   Adjusting to minimum {min_order_value} USDT")
            target_usdt = min_order_value

        btc_quantity = target_usdt / btc_price
        print(f"✅ Target buy amount: {target_usdt} USDT")
        print(f"✅ BTC quantity to buy: {btc_quantity:.8f} BTC")

        # Get current balance from Binance
        try:
            balance, positions_data = get_balance_and_positions(account)
            current_cash = float(balance) if balance is not None else 0.0
            print(f"✅ Current balance: {current_cash:.8f} USDT")

            # Check if we have enough cash
            # Add commission estimate (typically 0.1% for Binance, plus minimum commission)
            commission_rate = 0.001
            min_commission = 0.01  # Minimum commission in USDT
            estimated_commission = max(target_usdt * commission_rate, min_commission)
            total_needed = target_usdt + estimated_commission

            if current_cash < total_needed:
                print(f"⚠️  Warning: Insufficient balance")
                print(f"   Need: {total_needed:.2f} USDT (including estimated commission)")
                print(f"   Available: {current_cash:.8f} USDT")
                if current_cash >= min_order_value:
                    # Adjust quantity to available balance, leaving room for commission
                    # Reserve about 1% for commission and safety margin
                    max_affordable = (current_cash / (1 + commission_rate + 0.01)) * 0.99  # 99% to be safe
                    # Ensure we meet minimum order value
                    if max_affordable < min_order_value:
                        print(f"   ⚠️  Available balance is too low for minimum order value")
                        print(f"   Cannot proceed with buy order (minimum: {min_order_value} USDT)")
                        return
                    target_usdt = max_affordable
                    btc_quantity = target_usdt / btc_price
                    print(f"   ✅ Adjusting to buy {target_usdt:.2f} USDT worth of BTC")
                else:
                    print(
                        f"   ⚠️  Available balance {current_cash:.2f} USDT is below minimum order value {min_order_value} USDT"
                    )
                    print("   Cannot proceed with buy order")
                    return

        except Exception as e:
            print(f"❌ Failed to get balance from Binance: {e}")
            return

        # Create buy order
        print(f"\n📝 Creating BUY order: {btc_quantity:.8f} BTC @ MARKET price")

        try:
            order = create_order(
                db=db,
                account=account,
                symbol="BTC",
                name="BTC/USDT",
                side="BUY",
                order_type="MARKET",
                price=None,  # Market order doesn't need price
                quantity=btc_quantity,
            )

            db.commit()
            db.refresh(order)

            print(f"✅ Order created successfully!")
            print(f"   Order ID: {order.id}")
            print(f"   Order No: {order.order_no}")
            print(f"   Status: {order.status}")
            print(f"   Symbol: {order.symbol}")
            print(f"   Side: {order.side}")
            print(f"   Type: {order.order_type}")
            print(f"   Quantity: {order.quantity:.8f}")

            # Execute the order
            print(f"\n⚡ Executing order...")
            from services.order_matching import check_and_execute_order

            executed = check_and_execute_order(db, order)

            db.commit()
            db.refresh(order)

            if executed:
                print(f"✅ Order executed successfully!")
                print(f"   Status: {order.status}")
                print(f"   Filled Quantity: {order.filled_quantity:.8f}")

                # Check updated balance and positions
                try:
                    new_balance, new_positions = get_balance_and_positions(account)
                    print(f"\n📊 Updated Account Status:")
                    print(f"   Balance: {new_balance} USDT")
                    btc_position = None
                    for pos in new_positions:
                        if pos.get("symbol", "").upper() == "BTC":
                            btc_position = pos
                            break
                    if btc_position:
                        btc_qty = float(btc_position.get("quantity", 0) or 0)
                        print(f"   BTC Holdings: {btc_qty:.8f} BTC")
                except Exception as e:
                    print(f"⚠️  Could not fetch updated balance: {e}")
            else:
                print(f"⚠️  Order not executed yet (status: {order.status})")
                print(f"   The order will be processed by the order scheduler")

        except ValueError as e:
            db.rollback()
            print(f"❌ Order creation failed: {e}")
        except Exception as e:
            db.rollback()
            print(f"❌ Unexpected error: {e}")
            import traceback

            traceback.print_exc()

    finally:
        db.close()


if __name__ == "__main__":
    test_buy_btc()

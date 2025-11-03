#!/usr/bin/env python3
"""
Test script to sell 10 USDT worth of BTC
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


def test_sell_btc():
    """Test selling 10 USDT worth of BTC"""
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

        # Calculate BTC quantity for target USDT amount
        target_usdt = 50.0  # Test selling 50 USDT worth of BTC
        btc_quantity = target_usdt / btc_price
        print(f"✅ Target sell amount: {target_usdt} USDT")
        print(f"✅ BTC quantity to sell: {btc_quantity:.8f} BTC")

        # Get current positions from Binance
        try:
            balance, positions_data = get_balance_and_positions(account)
            print(f"✅ Current balance: {balance} USDT")

            # Check BTC position
            btc_position = None
            for pos in positions_data:
                if pos.get("symbol", "").upper() == "BTC":
                    btc_position = pos
                    break

            if not btc_position:
                print("❌ No BTC position found in account")
                print("   Please ensure you have BTC holdings to sell")
                return

            available_btc = float(btc_position.get("quantity", 0) or 0)
            print(f"✅ Available BTC: {available_btc:.8f} BTC")

            if available_btc < btc_quantity:
                print(f"⚠️  Warning: Available BTC ({available_btc:.8f}) is less than required ({btc_quantity:.8f})")
                print(f"   Will sell all available BTC: {available_btc:.8f} BTC")
                btc_quantity = available_btc

        except Exception as e:
            print(f"❌ Failed to get positions from Binance: {e}")
            return

        # Create sell order
        print(f"\n📝 Creating SELL order: {btc_quantity:.8f} BTC @ MARKET price")

        try:
            order = create_order(
                db=db,
                account=account,
                symbol="BTC",
                name="BTC/USDT",
                side="SELL",
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
    test_sell_btc()

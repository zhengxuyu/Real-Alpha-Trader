#!/usr/bin/env python3
"""
Direct script to query account balance and positions (without HTTP API)
"""
import sys
import os
from decimal import Decimal
from typing import Optional

# Add backend to path
backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
sys.path.insert(0, backend_dir)
sys.path.insert(0, os.path.dirname(__file__))

from database.connection import SessionLocal
from database.models import Account
from services.broker_adapter import get_balance_and_positions, get_open_orders
from services.binance_sync import clear_balance_cache


def format_currency(value: float, decimals: int = 2) -> str:
    """Format currency value"""
    return f"${value:,.{decimals}f}"


def format_number(value: float, decimals: int = 8) -> str:
    """Format number with specified decimals"""
    return f"{value:,.{decimals}f}".rstrip('0').rstrip('.')


def query_account_balance_and_positions(account_id: Optional[int] = None, refresh: bool = False):
    """Query account balance and positions directly from database and Binance"""
    db = SessionLocal()
    try:
        # Get account(s)
        if account_id:
            accounts = db.query(Account).filter(
                Account.id == account_id,
                Account.is_active == "true"
            ).all()
        else:
            accounts = db.query(Account).filter(Account.is_active == "true").all()
        
        if not accounts:
            print("❌ No active accounts found")
            return
        
        print("\n" + "="*80)
        print("💰 ACCOUNT BALANCE AND POSITIONS")
        print("="*80)
        
        for account in accounts:
            print(f"\n🔹 Account ID: {account.id}")
            print(f"   Name: {account.name}")
            print(f"   Type: {account.account_type}")
            
            # Refresh cache if requested
            if refresh:
                print(f"\n🔄 Refreshing balance cache...")
                clear_balance_cache(account)
            
            # Get balance and positions from Binance
            try:
                print(f"\n📡 Fetching data from Binance...")
                balance, positions = get_balance_and_positions(account)
                current_cash = float(balance) if balance is not None else 0.0
                
                print(f"\n💵 Balance:")
                print(f"   Available: {format_currency(current_cash)} USDT")
                print(f"   Frozen: $0.00 USDT (not tracked)")
                
                # Get open orders
                open_orders = get_open_orders(account)
                pending_count = len(open_orders)
                
                print(f"\n📊 Portfolio Summary:")
                print(f"   Total Cash: {format_currency(current_cash)} USDT")
                print(f"   Positions Count: {len(positions)}")
                print(f"   Pending Orders: {pending_count}")
                
                if positions:
                    print(f"\n📈 Positions:")
                    total_positions_value = 0.0
                    for i, pos in enumerate(positions, 1):
                        symbol = pos.get('symbol', 'N/A')
                        quantity = float(pos.get('quantity', 0))
                        available_qty = float(pos.get('available_quantity', 0))
                        avg_cost = float(pos.get('avg_cost', 0))
                        
                        print(f"   {i}. {symbol}")
                        print(f"      Total Quantity: {format_number(quantity)}")
                        print(f"      Available Quantity: {format_number(available_qty)}")
                        if avg_cost > 0:
                            print(f"      Avg Cost: {format_currency(avg_cost)}")
                            position_value = quantity * avg_cost
                            total_positions_value += position_value
                            print(f"      Position Value: {format_currency(position_value)}")
                    
                    if total_positions_value > 0:
                        print(f"\n   Total Positions Value: {format_currency(total_positions_value)}")
                        print(f"   Total Assets: {format_currency(current_cash + total_positions_value)}")
                else:
                    print(f"\n📈 Positions: None")
                
                if open_orders:
                    print(f"\n📋 Open Orders ({pending_count}):")
                    for i, order in enumerate(open_orders[:10], 1):  # Show first 10
                        symbol = order.get('symbol', 'N/A')
                        side = order.get('side', 'N/A')
                        order_type = order.get('order_type', 'N/A')
                        quantity = order.get('quantity', 0)
                        price = order.get('price')
                        order_id = order.get('order_id', 'N/A')
                        
                        print(f"   {i}. {side} {format_number(quantity)} {symbol}")
                        if price:
                            print(f"      Price: {format_currency(price)}")
                        print(f"      Type: {order_type}, ID: {order_id}")
                    if pending_count > 10:
                        print(f"   ... and {pending_count - 10} more orders")
                else:
                    print(f"\n📋 Open Orders: None")
                
            except Exception as e:
                print(f"\n❌ Error fetching data from Binance: {e}")
                import traceback
                traceback.print_exc()
        
        print("\n" + "="*80)
        print("✅ Query completed")
        print("="*80)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Query account balance and positions (direct)")
    parser.add_argument(
        "--account-id",
        type=int,
        help="Specific account ID to query (optional, queries all if not specified)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh balance cache before querying"
    )
    
    args = parser.parse_args()
    
    query_account_balance_and_positions(
        account_id=args.account_id,
        refresh=args.refresh
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


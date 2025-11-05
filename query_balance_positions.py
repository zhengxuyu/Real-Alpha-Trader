#!/usr/bin/env python3
"""
Script to query account balance and positions via API
"""
import sys
import json
import requests
from typing import Optional, Dict, List
from decimal import Decimal


# API configuration
API_BASE_URL = "http://localhost:8802"
# Alternative: use environment variable or config file
# API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8802")


def format_currency(value: float, decimals: int = 2) -> str:
    """Format currency value"""
    return f"${value:,.{decimals}f}"


def format_number(value: float, decimals: int = 8) -> str:
    """Format number with specified decimals"""
    return f"{value:,.{decimals}f}".rstrip('0').rstrip('.')


def query_account_list(api_base_url: str = API_BASE_URL) -> List[Dict]:
    """Query all accounts"""
    try:
        url = f"{api_base_url}/api/account/list"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error querying account list: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return []


def query_account_overview(account_id: Optional[int] = None, api_base_url: str = API_BASE_URL) -> Optional[Dict]:
    """Query account overview (balance and positions)"""
    try:
        if account_id:
            url = f"{api_base_url}/api/account/{account_id}/overview"
        else:
            url = f"{api_base_url}/api/account/overview"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error querying account overview: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return None


def query_positions(account_id: Optional[int] = None, api_base_url: str = API_BASE_URL) -> List[Dict]:
    """Query positions snapshot"""
    try:
        url = f"{api_base_url}/api/arena/positions"
        params = {}
        if account_id:
            params["account_id"] = account_id
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error querying positions: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return []


def refresh_balance(account_id: int, api_base_url: str = API_BASE_URL) -> Optional[Dict]:
    """Force refresh account balance"""
    try:
        url = f"{api_base_url}/api/account/{account_id}/refresh-balance"
        response = requests.post(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error refreshing balance: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return None


def print_account_summary(accounts: List[Dict]):
    """Print summary of all accounts"""
    if not accounts:
        print("❌ No accounts found")
        return
    
    print("\n" + "="*80)
    print("📊 ACCOUNT SUMMARY")
    print("="*80)
    
    for acc in accounts:
        print(f"\n🔹 Account ID: {acc['id']}")
        print(f"   Name: {acc['name']}")
        print(f"   Type: {acc['account_type']}")
        print(f"   Balance: {format_currency(acc['current_cash'])} USDT")
        print(f"   Status: {'✅ Active' if acc['is_active'] else '❌ Inactive'}")
        print(f"   Auto Trading: {'✅ Enabled' if acc['auto_trading_enabled'] else '❌ Disabled'}")


def print_account_details(overview: Dict):
    """Print detailed account information"""
    if not overview:
        print("❌ No account data available")
        return
    
    account = overview.get("account", {})
    portfolio = overview.get("portfolio", {})
    
    print("\n" + "="*80)
    print("💰 ACCOUNT DETAILS")
    print("="*80)
    
    print(f"\n📌 Account Information:")
    print(f"   ID: {account.get('id', 'N/A')}")
    print(f"   Name: {account.get('name', 'N/A')}")
    print(f"   Type: {account.get('account_type', 'N/A')}")
    
    print(f"\n💵 Balance:")
    cash = account.get('current_cash', 0)
    frozen = account.get('frozen_cash', 0)
    print(f"   Available: {format_currency(cash)} USDT")
    print(f"   Frozen: {format_currency(frozen)} USDT")
    
    if portfolio:
        positions = portfolio.get('positions', [])
        positions_count = portfolio.get('positions_count', 0)
        total_assets = portfolio.get('total_assets', cash)
        pending_orders = portfolio.get('pending_orders', 0)
        
        print(f"\n📊 Portfolio:")
        print(f"   Total Assets: {format_currency(total_assets)} USDT")
        print(f"   Positions Count: {positions_count}")
        print(f"   Pending Orders: {pending_orders}")
        
        if positions:
            print(f"\n📈 Positions:")
            for i, pos in enumerate(positions, 1):
                symbol = pos.get('symbol', 'N/A')
                quantity = pos.get('quantity', 0)
                avg_cost = pos.get('avg_cost', 0)
                print(f"   {i}. {symbol}")
                print(f"      Quantity: {format_number(quantity)}")
                if avg_cost > 0:
                    print(f"      Avg Cost: {format_currency(avg_cost)}")
        else:
            print(f"\n📈 Positions: None")


def print_positions_snapshot(positions_data: List[Dict]):
    """Print positions snapshot"""
    if not positions_data:
        print("\n❌ No positions data available")
        return
    
    print("\n" + "="*80)
    print("📊 POSITIONS SNAPSHOT")
    print("="*80)
    
    for snapshot in positions_data:
        account_name = snapshot.get('account_name', 'N/A')
        account_id = snapshot.get('account_id', 'N/A')
        cash = snapshot.get('cash', 0)
        positions = snapshot.get('positions', [])
        
        print(f"\n🔹 Account: {account_name} (ID: {account_id})")
        print(f"   Cash: {format_currency(cash)} USDT")
        
        if positions:
            print(f"   Positions ({len(positions)}):")
            for pos in positions:
                symbol = pos.get('symbol', 'N/A')
                quantity = pos.get('quantity', 0)
                avg_cost = pos.get('avg_cost', 0)
                current_price = pos.get('current_price', 0)
                unrealized_pnl = pos.get('unrealized_pnl', 0)
                
                print(f"      • {symbol}")
                print(f"        Quantity: {format_number(quantity)}")
                if avg_cost > 0:
                    print(f"        Avg Cost: {format_currency(avg_cost)}")
                if current_price > 0:
                    print(f"        Current Price: {format_currency(current_price)}")
                if unrealized_pnl != 0:
                    pnl_sign = "✅" if unrealized_pnl > 0 else "❌"
                    print(f"        Unrealized P&L: {pnl_sign} {format_currency(unrealized_pnl)}")
        else:
            print(f"   Positions: None")


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Query account balance and positions")
    parser.add_argument(
        "--account-id",
        type=int,
        help="Specific account ID to query (optional)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh balance before querying"
    )
    parser.add_argument(
        "--positions-only",
        action="store_true",
        help="Only show positions snapshot"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=API_BASE_URL,
        help=f"API base URL (default: {API_BASE_URL})"
    )
    
    args = parser.parse_args()
    
    # Override API URL if provided
    api_base_url = args.api_url
    
    print(f"🔗 Connecting to API: {api_base_url}")
    
    # Check API health
    try:
        health_url = f"{api_base_url}/api/health"
        response = requests.get(health_url, timeout=5)
        if response.status_code == 200:
            print("✅ API is healthy")
        else:
            print(f"⚠️  API health check returned status {response.status_code}")
    except Exception as e:
        print(f"⚠️  Could not connect to API: {e}")
        print(f"   Make sure the server is running at {api_base_url}")
        sys.exit(1)
    
    # Refresh balance if requested
    if args.refresh:
        if args.account_id:
            print(f"\n🔄 Refreshing balance for account {args.account_id}...")
            result = refresh_balance(args.account_id, api_base_url)
            if result:
                print(f"✅ Balance refreshed: {format_currency(result.get('current_cash', 0))} USDT")
        else:
            print("\n⚠️  --refresh requires --account-id")
    
    # Query positions only
    if args.positions_only:
        positions_data = query_positions(args.account_id, api_base_url)
        print_positions_snapshot(positions_data)
        return
    
    # Query account list
    accounts = query_account_list(api_base_url)
    
    if not accounts:
        print("❌ No accounts found")
        return
    
    # Print account summary
    print_account_summary(accounts)
    
    # Query detailed overview
    if args.account_id:
        # Query specific account
        overview = query_account_overview(args.account_id, api_base_url)
        print_account_details(overview)
    else:
        # Query default account
        overview = query_account_overview(None, api_base_url)
        if overview:
            print_account_details(overview)
        
        # Also show positions snapshot
        positions_data = query_positions(None, api_base_url)
        if positions_data:
            print_positions_snapshot(positions_data)
    
    print("\n" + "="*80)
    print("✅ Query completed")
    print("="*80)


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


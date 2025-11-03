#!/usr/bin/env python3
"""
Test script to fetch balance and positions from Binance API
"""
import sys
import os
import urllib.request
import json
from decimal import Decimal
from typing import Optional, List, Dict

# Add backend to path
backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
sys.path.insert(0, backend_dir)
sys.path.insert(0, os.path.dirname(__file__))


def load_api_config(config_path: str = ".api.yaml") -> Dict[str, str]:
    """Load API keys from YAML config file"""
    try:
        with open(config_path, 'r') as f:
            config = {}
            for line in f:
                line = line.strip()
                if ':' in line and line and not line.startswith('#'):
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    config[key] = value
            return config
    except FileNotFoundError:
        print(f"Error: Config file {config_path} not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)


def create_test_account(api_key: str, secret_key: str):
    """Create a test Account object with Binance credentials"""
    # Create a mock Account object
    class MockAccount:
        def __init__(self, api_key: str, secret_key: str):
            self.id = 999
            self.name = "Test Binance Account"
            self.binance_api_key = api_key
            self.binance_secret_key = secret_key
            self.broker_type = "Binance"
    
    return MockAccount(api_key, secret_key)


def format_decimal(value: Optional[Decimal], precision: int = 2) -> str:
    """Format Decimal value with precision"""
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}"


def get_public_ip() -> Optional[str]:
    """Get the public IP address of the current server"""
    try:
        # Try multiple IP detection services
        services = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/ip",
            "https://api.ip.sb/ip",
            "https://checkip.amazonaws.com",
        ]
        
        for service_url in services:
            try:
                with urllib.request.urlopen(service_url, timeout=5) as response:
                    if 'json' in service_url:
                        data = json.loads(response.read().decode('utf-8'))
                        return data.get('ip', None)
                    else:
                        return response.read().decode('utf-8').strip()
            except:
                continue
        
        return None
    except Exception as e:
        print(f"Warning: Could not get public IP: {e}")
        return None


def main():
    print("=" * 60)
    print("Binance Balance and Positions Test")
    print("=" * 60)
    
    # Load API keys from .api.yaml
    print("\n[1] Loading API keys from .api.yaml...")
    # Try to find .api.yaml in project root (parent of backend or current dir)
    config_path = ".api.yaml"
    if os.path.exists(config_path):
        pass  # Found in current directory
    elif os.path.exists(os.path.join("..", ".api.yaml")):
        config_path = os.path.join("..", ".api.yaml")
    elif os.path.exists(os.path.join(os.path.dirname(__file__), ".api.yaml")):
        config_path = os.path.join(os.path.dirname(__file__), ".api.yaml")
    
    config = load_api_config(config_path)
    
    api_key = config.get("BINANCE_API_KEY", "").strip()
    secret_key = config.get("BINANCE_SECRET_KEY", "").strip()
    
    if not api_key or not secret_key:
        print("ERROR: BINANCE_API_KEY or BINANCE_SECRET_KEY not found in .api.yaml")
        sys.exit(1)
    
    print(f"✓ API Key loaded: {api_key[:8]}...{api_key[-8:]}")
    print(f"✓ Secret Key loaded: {secret_key[:8]}...{secret_key[-8:]}")
    
    # Get and print current IP address
    print("\n[1.5] Checking server IP address...")
    public_ip = get_public_ip()
    if public_ip:
        print(f"✓ Current Public IP: {public_ip}")
        print(f"⚠ Note: Binance may restrict access from this IP/location")
    else:
        print("⚠ Could not determine public IP address")
    
    # Create test account
    print("\n[2] Creating test account...")
    account = create_test_account(api_key, secret_key)
    print(f"✓ Test account created: {account.name}")
    
    # Import after path setup
    try:
        from backend.services.binance_sync import get_binance_balance_and_positions
    except ImportError as e:
        print(f"\n❌ Import error: {e}")
        print("\nPlease ensure you're running from the project root directory")
        print("and that all dependencies are installed:")
        print("  cd backend && uv sync")
        sys.exit(1)
    
    # Fetch balance and positions
    print("\n[3] Fetching balance and positions from Binance...")
    print("-" * 60)
    
    try:
        balance, positions = get_binance_balance_and_positions(account)
        
        # Display results
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        
        if balance is not None:
            print(f"\n💰 Balance (USDT): {format_decimal(balance, 2)}")
        else:
            print("\n❌ Balance: Unable to fetch")
        
        print(f"\n📊 Positions: {len(positions)}")
        
        if positions:
            print("\n" + "-" * 60)
            print(f"{'Symbol':<10} {'Quantity':<20} {'Available':<20}")
            print("-" * 60)
            
            for pos in positions:
                symbol = pos.get("symbol", "N/A")
                quantity = pos.get("quantity", Decimal('0'))
                available = pos.get("available_quantity", Decimal('0'))
                
                print(f"{symbol:<10} {format_decimal(quantity, 8):<20} {format_decimal(available, 8):<20}")
        else:
            print("\n  No open positions")
        
        print("\n" + "=" * 60)
        print("✓ Test completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ERROR: Failed to fetch balance and positions")
        print(f"   {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()


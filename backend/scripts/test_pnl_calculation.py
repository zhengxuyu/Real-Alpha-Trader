#!/usr/bin/env python3
"""
测试 P&L 计算是否正确
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import SessionLocal
from database.models import Position, Account
from services.broker_adapter import get_balance_and_positions
from services.market_data import get_last_price

db = SessionLocal()
try:
    account = db.query(Account).filter(Account.id == 1).first()
    if not account:
        print("Account not found")
        sys.exit(1)
    
    print(f"=== Testing P&L Calculation for Account: {account.name} ===\n")
    
    # Get positions from Binance
    balance, binance_positions = get_balance_and_positions(account)
    print(f"Binance Positions: {len(binance_positions)}")
    
    # Get avg_cost from database
    db_positions = db.query(Position).filter(
        Position.account_id == account.id,
        Position.market == "CRYPTO"
    ).all()
    
    db_avg_cost_map = {pos.symbol: float(pos.avg_cost) for pos in db_positions if float(pos.avg_cost) > 0}
    print(f"Database avg_cost map: {db_avg_cost_map}\n")
    
    # Calculate P&L for each position
    print("Position Details:")
    print("-" * 80)
    total_unrealized_pnl = 0
    
    for pos in binance_positions:
        symbol = pos["symbol"]
        quantity = float(pos["quantity"])
        binance_avg_cost = float(pos.get("avg_cost", 0))
        db_avg_cost = db_avg_cost_map.get(symbol, 0)
        
        # Use database avg_cost if available
        avg_cost = db_avg_cost if db_avg_cost > 0 else binance_avg_cost
        
        try:
            current_price = get_last_price(symbol, "CRYPTO")
            market_value = current_price * quantity
            cost_basis = avg_cost * quantity
            unrealized_pnl = market_value - cost_basis
            total_unrealized_pnl += unrealized_pnl
            
            print(f"{symbol}:")
            print(f"  Quantity: {quantity:.8f}")
            print(f"  Current Price: ${current_price:.2f}")
            print(f"  Binance avg_cost: ${binance_avg_cost:.2f}")
            print(f"  Database avg_cost: ${db_avg_cost:.2f}")
            print(f"  Using avg_cost: ${avg_cost:.2f}")
            print(f"  Market Value: ${market_value:.2f}")
            print(f"  Cost Basis: ${cost_basis:.2f}")
            print(f"  Unrealized P&L: ${unrealized_pnl:.2f}")
            print(f"  {'✅ CORRECT' if unrealized_pnl != market_value else '❌ WRONG (equals market value)'}")
            print()
        except Exception as e:
            print(f"{symbol}: Error getting price - {e}\n")
    
    print("-" * 80)
    print(f"Total Unrealized P&L: ${total_unrealized_pnl:.2f}")
    
finally:
    db.close()


"""
Binance Broker Implementation
Concrete implementation of BrokerInterface for Binance exchange
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from database.models import Account

from .broker_interface import BrokerInterface
from .binance_sync import (
    cancel_binance_order,
    execute_binance_order,
    get_binance_balance_and_positions,
    get_binance_closed_orders,
    get_binance_open_orders,
    map_symbol_to_binance_pair,
)


class BinanceBroker(BrokerInterface):
    """Binance broker implementation"""
    
    def get_balance_and_positions(self, account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
        """Get both balance and positions from Binance in a single API call"""
        return get_binance_balance_and_positions(account)
    
    def get_open_orders(self, account: Account) -> List[Dict]:
        """Get open orders from Binance"""
        return get_binance_open_orders(account)
    
    def get_closed_orders(self, account: Account, limit: int = 100) -> List[Dict]:
        """Get closed orders from Binance"""
        return get_binance_closed_orders(account, limit)
    
    def execute_order(
        self,
        account: Account,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        ordertype: str = "market"
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Execute an order on Binance"""
        # Use unified api_key and secret_key variable names
        api_key = account.binance_api_key
        secret_key = account.binance_secret_key
        
        if not api_key or not secret_key:
            return False, "Binance API keys not configured", None
        
        return execute_binance_order(
            api_key=api_key,
            secret_key=secret_key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            ordertype=ordertype
        )
    
    def cancel_order(self, account: Account, order_id: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Cancel an order on Binance"""
        # Use unified api_key and secret_key variable names
        api_key = account.binance_api_key
        secret_key = account.binance_secret_key
        
        if not api_key or not secret_key:
            return False, "Binance API keys not configured", None
        
        # For Binance, we need the symbol to cancel the order
        # Try to find it from open orders first
        open_orders = self.get_open_orders(account)
        symbol = None
        for order in open_orders:
            if str(order.get("order_id")) == str(order_id):
                symbol = order.get("symbol")
                break
        
        # If not found in open orders, try closed orders (though those can't be cancelled)
        if not symbol:
            closed_orders = self.get_closed_orders(account, limit=100)
            for order in closed_orders:
                if str(order.get("order_id")) == str(order_id):
                    symbol = order.get("symbol")
                    break
        
        if not symbol:
            return False, f"Cannot find symbol for order {order_id}. The order may not exist or may already be cancelled.", None
        
        return cancel_binance_order(
            api_key=api_key,
            secret_key=secret_key,
            order_id=order_id,
            symbol=symbol
        )
    
    def map_symbol_to_pair(self, symbol: str) -> str:
        """Map internal symbol to Binance trading pair"""
        return map_symbol_to_binance_pair(symbol)
    
    def get_broker_name(self) -> str:
        """Get broker name"""
        return "Binance"


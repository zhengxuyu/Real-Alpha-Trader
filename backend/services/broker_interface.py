"""
Broker Interface Abstraction
Abstract base class for broker integrations to support multiple brokers
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from database.models import Account


class BrokerInterface(ABC):
    """
    Abstract interface for broker integrations.
    All broker implementations must implement these methods.
    """

    @abstractmethod
    def get_balance_and_positions(self, account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
        """
        Get both balance and positions in a single API call when possible.
        This is more efficient than calling get_balance and get_positions separately.

        Args:
            account: Account object with broker API credentials

        Returns:
            Tuple of (balance: Optional[Decimal], positions: List[Dict])
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get_open_orders(self, account: Account) -> List[Dict]:
        """
        Get open/pending orders for the account.

        Args:
            account: Account object with broker API credentials

        Returns:
            List of order dictionaries, each containing:
            - order_id: str (broker's order ID)
            - symbol: str
            - side: str ("BUY" or "SELL")
            - order_type: str ("MARKET", "LIMIT", etc.)
            - quantity: float
            - price: float (optional for market orders)
            - status: str ("OPEN", "PENDING", etc.)
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get_closed_orders(self, account: Account, limit: int = 100) -> List[Dict]:
        """
        Get closed/completed orders for the account.

        Args:
            account: Account object with broker API credentials
            limit: Maximum number of orders to retrieve

        Returns:
            List of completed order dictionaries, each containing:
            - order_id: str (broker's order ID/txid)
            - symbol: str
            - side: str ("BUY" or "SELL")
            - price: float
            - quantity: float
            - cost: float
            - fee: float
            - status: str ("FILLED", "CANCELLED", etc.)
            - close_time: int (timestamp)
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def execute_order(
        self, account: Account, symbol: str, side: str, quantity: float, price: float, ordertype: str = "market"
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Execute an order on the broker.

        Args:
            account: Account object with broker API credentials
            symbol: Trading symbol (e.g., "BTC", "ETH")
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity
            price: Order price (reference price for market orders)
            ordertype: Order type ("market", "limit", etc.)

        Returns:
            Tuple of (success: bool, error_message_or_order_id: Optional[str], result: Optional[Dict])
            - success: True if order was placed successfully
            - error_message_or_order_id: Order ID if successful, error message if failed
            - result: Full response from broker API (optional)
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def cancel_order(self, account: Account, order_id: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Cancel an order on the broker.

        Args:
            account: Account object with broker API credentials
            order_id: Broker's order ID/txid to cancel

        Returns:
            Tuple of (success: bool, error_message: Optional[str], result: Optional[Dict])
            - success: True if order was cancelled successfully
            - error_message: Error message if cancellation failed
            - result: Full response from broker API (optional)
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def map_symbol_to_pair(self, symbol: str) -> str:
        """
        Map internal symbol to broker's trading pair format.

        Args:
            symbol: Internal trading symbol (e.g., "BTC", "ETH")

        Returns:
            Broker's trading pair format (e.g., "XBTUSD", "ETHUSD")
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get_broker_name(self) -> str:
        """
        Get the name of this broker implementation.

        Returns:
            Broker name (e.g., "Binance", "Coinbase", etc.)
        """
        raise NotImplementedError("Not implemented")

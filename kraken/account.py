# Get all asset balances.

from kraken.kraken_request import request


def get_balance(api_key: str, private_key: str):
    """
    Get account balance from Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
    """
    response = request(
        method="POST",
        path="/0/private/Balance",
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com",
    )
    return response.read().decode()


def get_trade_balance(api_key: str, private_key: str):
    """
    Get trade balance from Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
    """
    response = request(
        method="POST",
        path="/0/private/TradeBalance",
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com",
    )
    return response.read().decode()


def get_open_orders(api_key: str, private_key: str):
    """
    Get open orders from Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
    """
    response = request(
        method="POST",
        path="/0/private/OpenOrders",
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com",
    )
    return response.read().decode()


def get_closed_orders(api_key: str, private_key: str, limit: int = None):
    """
    Get closed orders from Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
        limit: Optional limit for number of closed orders to retrieve
    """
    body = {}
    if limit is not None:
        body["limit"] = limit
    
    response = request(
        method="POST",
        path="/0/private/ClosedOrders",
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com",
        body=body if body else None,
    )
    return response.read().decode()
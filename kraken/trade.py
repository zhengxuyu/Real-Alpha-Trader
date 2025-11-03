import json

from kraken.account import get_open_orders
from kraken.kraken_request import request
from kraken.market import get_ticker_information
from kraken.token_map import map_token


def add_order(api_key: str, private_key: str, pair: str, type: str, ordertype: str, volume: float, price: float):
    """
    Add a new order to Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
        pair: Trading pair (e.g., "BTCUSD")
        type: Order type ("buy" or "sell")
        ordertype: Order type ("market", "limit", etc.)
        volume: Order volume
        price: Order price
    """
    pair = map_token(pair)
    body = {
        "pair": pair,
        "type": type,
        "ordertype": ordertype,
        "volume": volume,
        "price": price,
        "timeinforce": "GTD",
        "expiretm": "+5"

    }
    response = request(
        method="POST",
        path="/0/private/AddOrder",
        body=body,
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com"
    )
    return json.loads(response.read().decode())


def cancel_order(api_key: str, private_key: str, txid: str):
    """
    Cancel an order on Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
        txid: Transaction ID of the order to cancel
    """
    body = {"txid": txid}
    response = request(
        method="POST",
        path="/0/private/CancelOrder",
        body=body,
        public_key=api_key,
        private_key=private_key,
        environment="https://api.kraken.com"
    )
    return json.loads(response.read().decode())


if __name__ == "__main__":
    # This is a test script - requires API keys
    from kraken.auth import get_auth
    
    api_key, private_key = get_auth()
    
    # Get current ask price for XBTUSD
    ticker_info = get_ticker_information("XBTUSD")
    ask_price = ticker_info['result']['XXBTZUSD']['a'][0]
    print(f"Current ask price for XBTUSD: {ask_price}")

    print("Open Orders:")
    print(get_open_orders(api_key, private_key))

    print("\nCreating a new BTCUSD order:")
    result = add_order(api_key, private_key, pair="BTCUSD", type="buy", ordertype="limit", volume=1, price=ask_price)
    print(result)

    # print("\nCancelling the order:")
    # result = cancel_order(api_key, private_key, txid="1234567890")
    # print(result)
import json

from kraken.kraken_request import request

# Default Kraken API environment
DEFAULT_ENVIRONMENT = "https://api.kraken.com"


def get_server_time():
    response = request(
        method="GET",
        path="/0/public/Time",
        environment=DEFAULT_ENVIRONMENT
    )
    return json.loads(response.read().decode())


def get_system_status():
    response = request(
        method="GET",
        path="/0/public/SystemStatus",
        environment=DEFAULT_ENVIRONMENT
    )
    return json.loads(response.read().decode())


def get_asset_info(asset: str = "ETH"):
    response = request(
        method="GET",
        path="/0/public/Assets?asset=" + asset,
        environment=DEFAULT_ENVIRONMENT
    )
    return json.loads(response.read().decode())


def get_ticker_information(pair: str = "XBTUSD"):
    response = request(
        method="GET",
        path="/0/public/Ticker?pair=" + pair,
        environment=DEFAULT_ENVIRONMENT
    )
    return json.loads(response.read().decode())


def get_tradable_asset_pairs():
    response = request(
        method="GET",
        path="/0/public/AssetPairs",
        environment=DEFAULT_ENVIRONMENT
    )
    return json.loads(response.read().decode())["result"].keys()


if __name__ == "__main__":
    print(get_ticker_information("XBTUSD"))
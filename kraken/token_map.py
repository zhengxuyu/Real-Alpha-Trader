from typing import Dict

# Map internal symbols to Kraken trading pairs
INTERNAL_TO_KRAKEN_MAP: Dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "BNB": "BNBUSD",
    "XRP": "XRPUSD",
    "DOGE": "XDGUSD",
}

# Reverse mapping: Kraken asset symbols to internal symbols
KRAKEN_TO_INTERNAL_MAP: Dict[str, str] = {
    "XBT": "BTC",
    "ZUSD": "USD",
    "ETH": "ETH",
    "SOL": "SOL",
    "BNB": "BNB",
    "XRP": "XRP",
    "XDG": "DOGE",
}


def map_token(token: str) -> str:
    """Map internal symbol to Kraken trading pair"""
    return INTERNAL_TO_KRAKEN_MAP.get(token.upper(), f"{token}USD")


def map_kraken_asset_to_internal(kraken_asset: str) -> str:
    """Map Kraken asset symbol to internal symbol"""
    # Try direct mapping first
    mapped = KRAKEN_TO_INTERNAL_MAP.get(kraken_asset)
    if mapped:
        return mapped
    
    # Remove 'Z' prefix for currencies (e.g., ZUSD -> USD)
    asset = kraken_asset.lstrip('Z')
    mapped = KRAKEN_TO_INTERNAL_MAP.get(asset)
    if mapped:
        return mapped
    
    # If no mapping found, return asset as-is (uppercase)
    return asset
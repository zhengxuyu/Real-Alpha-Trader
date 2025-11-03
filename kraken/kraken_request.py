import base64
import hashlib
import hmac
import http.client
import json
import threading
import time
import urllib.parse
import urllib.request

# Thread-safe nonce counter to ensure uniqueness and strict monotonicity
# Kraken requires nonce to be strictly increasing and unique
_nonce_counter = 0
_nonce_lock = threading.Lock()
_last_timestamp = 0  # Track last timestamp to handle time rollback


def request(method: str = "GET", path: str = "", query: dict | None = None, body: dict | None = None, public_key: str = "", private_key: str = "", environment: str = "") -> http.client.HTTPResponse:
   url = environment + path
   query_str = ""
   if query is not None and len(query) > 0:
      query_str = urllib.parse.urlencode(query)
      url += "?" + query_str
   nonce = ""
   if len(public_key) > 0:
      if body is None:
         body = {}
      nonce = body.get("nonce")
      if nonce is None:
         nonce = get_nonce()
         body["nonce"] = nonce
   headers = {}
   body_str = ""
   if body is not None and len(body) > 0:
      body_str = json.dumps(body)
      headers["Content-Type"] = "application/json"
   if len(public_key) > 0:
      headers["API-Key"] = public_key
      headers["API-Sign"] = get_signature(private_key, query_str+body_str, nonce, path)
   req = urllib.request.Request(
      method=method,
      url=url,
      data=body_str.encode(),
      headers=headers,
   )
   return urllib.request.urlopen(req)

def get_nonce() -> str:
   """
   Generate a unique and strictly increasing nonce for Kraken API requests.
   Kraken requires nonce to be strictly increasing - each nonce must be larger than the previous one.
   Uses timestamp in milliseconds + thread-safe counter to ensure uniqueness and monotonicity.
   """
   global _nonce_counter, _last_timestamp
   with _nonce_lock:
      current_millis = int(time.time() * 1000)
      
      # Ensure nonce is strictly increasing
      if current_millis > _last_timestamp:
         # New timestamp - reset counter and use new timestamp
         _nonce_counter = 0
         _last_timestamp = current_millis
      elif current_millis == _last_timestamp:
         # Same timestamp - increment counter to ensure uniqueness
         _nonce_counter += 1
      else:
         # Time went backwards (rare but possible with clock adjustments)
         # Increment timestamp to ensure nonce is always increasing
         _last_timestamp += 1
         _nonce_counter = 0
         current_millis = _last_timestamp
      
      # Generate nonce: timestamp_ms * 10000 + counter (allows up to 10000 requests per ms)
      # This ensures strict monotonicity and uniqueness
      nonce_value = current_millis * 10000 + _nonce_counter
      return str(nonce_value)

def get_signature(private_key: str, data: str, nonce: str, path: str) -> str:
   return sign(
      private_key=private_key,
      message=path.encode() + hashlib.sha256(
            (nonce + data)
         .encode()
      ).digest()
   )

def sign(private_key: str, message: bytes) -> str:
   return base64.b64encode(
      hmac.new(
         key=base64.b64decode(private_key),
         msg=message,
         digestmod=hashlib.sha512,
      ).digest()
   ).decode()
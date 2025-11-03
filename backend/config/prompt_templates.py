"""
Default and Pro prompt templates for Hyper Alpha Arena.
"""

# Baseline prompt (current behaviour)
DEFAULT_PROMPT_TEMPLATE = """You are a cryptocurrency trading AI. Use the data below to determine your next action.

=== PORTFOLIO DATA ===
{account_state}

=== CURRENT MARKET PRICES (USDT) ===
{prices_json}

=== LATEST CRYPTO NEWS SNIPPET ===
{news_section}

Follow these rules:
- operation must be "buy", "sell", "hold", or "close"
- For "buy": target_portion_of_balance is the % of available cash to deploy (0.0-1.0). Remember to account for trading fees (~0.1% commission) - you need slightly more cash than the purchase amount.
- For "sell" or "close": target_portion_of_balance is the % of the current position to exit (0.0-1.0). Remember you will receive slightly less due to trading fees (~0.1% commission).
- For "hold": keep target_portion_of_balance at 0
- Never invent trades for symbols that are not in the market data
- Keep reasoning concise and focused on measurable signals
- Always consider trading fees when calculating trade sizes - see Trading Fees in Account State

Respond with ONLY a JSON object using this schema:
{output_format}
"""

# Structured prompt inspired by Alpha Arena research
PRO_PROMPT_TEMPLATE = """=== SESSION CONTEXT ===
{session_context}

=== MARKET SNAPSHOT ===
{market_snapshot}

=== ACCOUNT STATE ===
{account_state}

=== DECISION TASK ===
{decision_task}

=== OUTPUT FORMAT ===
{output_format}
"""

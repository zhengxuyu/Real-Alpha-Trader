"""
Auto Trading Service - Main entry point for automated crypto trading
This file maintains backward compatibility while delegating to split services
"""
import logging

# Import from the new split services
from services.ai_decision_service import (SUPPORTED_SYMBOLS,
                                          _get_portfolio_data,
                                          _is_default_api_key)
from services.ai_decision_service import \
    call_ai_for_decision as _call_ai_for_decision
from services.ai_decision_service import \
    get_active_ai_accounts as _choose_account
from services.ai_decision_service import save_ai_decision as _save_ai_decision
from services.trading_commands import (AI_TRADE_JOB_ID, AI_TRADING_SYMBOLS,
                                       AUTO_TRADE_JOB_ID, _get_market_prices,
                                       _select_side,
                                       place_ai_driven_crypto_order,
                                       place_random_crypto_order)

logger = logging.getLogger(__name__)


# Backward compatibility - re-export main functions
# All the actual implementation is now in the split service files

# These constants are kept for backward compatibility
AUTO_TRADE_JOB_ID = AI_TRADE_JOB_ID
AI_TRADE_JOB_ID = AI_TRADE_JOB_ID

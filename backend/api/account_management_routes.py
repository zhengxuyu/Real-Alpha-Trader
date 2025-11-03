"""
Account Management API Routes - Handle CRUD operations for trading accounts
"""

import logging
from typing import List

from database.connection import SessionLocal
from database.models import Account
from fastapi import APIRouter, Depends, HTTPException
from repositories.account_repo import (
    create_account,
    deactivate_account,
    get_account,
    get_accounts_by_user,
    get_or_create_default_account,
    update_account,
    update_account_cash,
)
from repositories.user_repo import get_user, verify_auth_session
from schemas.account import AccountCreate, AccountOut, AccountOverview, AccountUpdate
from services.broker_adapter import get_balance_and_positions
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user_id(session_token: str, db: Session = Depends(get_db)) -> int:
    """Get current user ID from session token"""
    user_id = verify_auth_session(db, session_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user_id


@router.get("/", response_model=List[AccountOut])
async def list_user_accounts(session_token: str, db: Session = Depends(get_db)):
    """Get all trading accounts for the current user"""
    try:
        user_id = await get_current_user_id(session_token, db)
        accounts = get_accounts_by_user(db, user_id, active_only=True)

        # Get balance from Binance for each account
        result = []
        for account in accounts:
            try:
                balance, _ = get_balance_and_positions(account)
                current_cash = float(balance) if balance is not None else 0.0
            except Exception:
                current_cash = 0.0

            result.append(
                AccountOut(
                    id=account.id,
                    user_id=account.user_id,
                    name=account.name,
                    model=account.model,
                    base_url=account.base_url,
                    api_key="****" + account.api_key[-4:] if account.api_key else "",
                    binance_api_key="****" + account.binance_api_key[-4:] if account.binance_api_key else "",
                    binance_secret_key="****" + account.binance_secret_key[-4:] if account.binance_secret_key else "",
                    initial_capital=current_cash,
                    current_cash=current_cash,
                    frozen_cash=0.0,
                    account_type=account.account_type,
                    is_active=account.is_active == "true",
                )
            )
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get account list: {str(e)}")


@router.post("/", response_model=AccountOut)
async def create_trading_account(session_token: str, account_data: AccountCreate, db: Session = Depends(get_db)):
    """Create a new trading account"""
    try:
        user_id = await get_current_user_id(session_token, db)

        # Check if account name exists for this user
        existing_accounts = get_accounts_by_user(db, user_id, active_only=True)
        for acc in existing_accounts:
            if acc.name == account_data.name:
                raise HTTPException(status_code=400, detail="Account name already exists")

        account = create_account(
            db=db,
            user_id=user_id,
            name=account_data.name,
            account_type=account_data.account_type,
            model=account_data.model,
            base_url=account_data.base_url,
            api_key=account_data.api_key,
            binance_api_key=account_data.binance_api_key,
            binance_secret_key=account_data.binance_secret_key,
        )

        # Get balance from Binance in real-time (single API call)
        try:
            balance, _ = get_balance_and_positions(account)
            current_cash = float(balance) if balance is not None else 0.0
        except Exception:
            current_cash = 0.0

        return AccountOut(
            id=account.id,
            user_id=account.user_id,
            name=account.name,
            model=account.model,
            base_url=account.base_url,
            api_key="****" + account.api_key[-4:] if account.api_key else "",
            binance_api_key="****" + account.binance_api_key[-4:] if account.binance_api_key else "",
            binance_secret_key="****" + account.binance_secret_key[-4:] if account.binance_secret_key else "",
            initial_capital=current_cash,
            current_cash=current_cash,
            frozen_cash=0.0,
            account_type=account.account_type,
            is_active=account.is_active == "true",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create account: {str(e)}")


@router.get("/{account_id}", response_model=AccountOut)
async def get_account_details(account_id: int, session_token: str, db: Session = Depends(get_db)):
    """Get account details"""
    try:
        user_id = await get_current_user_id(session_token, db)
        account = get_account(db, account_id)

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        if account.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get balance from Binance in real-time (single API call)
        try:
            balance, _ = get_balance_and_positions(account)
            current_cash = float(balance) if balance is not None else 0.0
        except Exception:
            current_cash = 0.0

        return AccountOut(
            id=account.id,
            user_id=account.user_id,
            name=account.name,
            model=account.model,
            base_url=account.base_url,
            api_key="****" + account.api_key[-4:] if account.api_key else "",
            binance_api_key="****" + account.binance_api_key[-4:] if account.binance_api_key else "",
            binance_secret_key="****" + account.binance_secret_key[-4:] if account.binance_secret_key else "",
            initial_capital=current_cash,
            current_cash=current_cash,
            frozen_cash=0.0,
            account_type=account.account_type,
            is_active=account.is_active == "true",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get account details: {str(e)}")


@router.put("/{account_id}", response_model=AccountOut)
async def update_trading_account(
    account_id: int, session_token: str, account_data: AccountUpdate, db: Session = Depends(get_db)
):
    """Update trading account"""
    try:
        user_id = await get_current_user_id(session_token, db)
        account = get_account(db, account_id)

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        if account.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if new name conflicts with existing accounts
        if account_data.name:
            existing_accounts = get_accounts_by_user(db, user_id, active_only=True)
            for acc in existing_accounts:
                if acc.name == account_data.name and acc.id != account_id:
                    raise HTTPException(status_code=400, detail="Account name already exists")

        updated_account = update_account(
            db=db,
            account_id=account_id,
            name=account_data.name,
            model=account_data.model,
            base_url=account_data.base_url,
            api_key=account_data.api_key,
        )

        # Get balance from Binance in real-time (single API call)
        try:
            balance, _ = get_balance_and_positions(updated_account)
            current_cash = float(balance) if balance is not None else 0.0
        except Exception:
            current_cash = 0.0

        return AccountOut(
            id=updated_account.id,
            user_id=updated_account.user_id,
            name=updated_account.name,
            model=updated_account.model,
            base_url=updated_account.base_url,
            api_key="****" + updated_account.api_key[-4:] if updated_account.api_key else "",
            initial_capital=current_cash,
            current_cash=current_cash,
            frozen_cash=0.0,
            account_type=updated_account.account_type,
            is_active=updated_account.is_active == "true",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update account: {str(e)}")


@router.delete("/{account_id}")
async def delete_trading_account(account_id: int, session_token: str, db: Session = Depends(get_db)):
    """Delete trading account (soft delete)"""
    try:
        user_id = await get_current_user_id(session_token, db)
        account = get_account(db, account_id)

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        if account.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        deactivate_account(db, account_id)
        return {"message": f"Account {account.name} deactivated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete account: {str(e)}")


@router.get("/{account_id}/default")
async def get_or_create_default(session_token: str, db: Session = Depends(get_db)):
    """Get or create default account (for backward compatibility)"""
    try:
        user_id = await get_current_user_id(session_token, db)
        account = get_or_create_default_account(db, user_id)

        if not account:
            raise HTTPException(status_code=404, detail="No accounts found. Please create an account first.")

        # Get balance from Binance in real-time (single API call)
        try:
            balance, _ = get_balance_and_positions(account)
            current_cash = float(balance) if balance is not None else 0.0
        except Exception:
            current_cash = 0.0

        return AccountOut(
            id=account.id,
            user_id=account.user_id,
            name=account.name,
            model=account.model,
            base_url=account.base_url,
            api_key="****" + account.api_key[-4:] if account.api_key else "",
            binance_api_key="****" + account.binance_api_key[-4:] if account.binance_api_key else "",
            binance_secret_key="****" + account.binance_secret_key[-4:] if account.binance_secret_key else "",
            initial_capital=current_cash,
            current_cash=current_cash,
            frozen_cash=0.0,
            account_type=account.account_type,
            is_active=account.is_active == "true",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get default account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get default account: {str(e)}")

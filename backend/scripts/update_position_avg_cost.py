#!/usr/bin/env python3
"""
更新持仓平均成本的脚本

功能：
1. 尝试从 Binance 交易历史计算平均持仓成本
2. 如果没有交易历史，使用当前市场价格作为开仓成本
3. 更新数据库中的 Position 表的 avg_cost 字段
"""

import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import SessionLocal
from database.models import Account, Position
from services.binance_sync import _make_signed_request, get_binance_balance_and_positions
from services.market_data import get_last_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _get_trading_pair_symbol(symbol: str) -> str:
    """将资产符号转换为 Binance 交易对符号 (如 BTC -> BTCUSDT)"""
    return f"{symbol.upper()}USDT"


def calculate_avg_cost_from_trades(account: Account, symbol: str) -> Optional[float]:
    """
    尝试从 Binance 交易历史计算平均持仓成本
    
    Args:
        account: 账户对象
        symbol: 资产符号 (如 BTC, ETH)
    
    Returns:
        平均成本，如果无法计算则返回 None
    """
    if not account.binance_api_key or not account.binance_secret_key:
        return None
    
    try:
        trading_pair = _get_trading_pair_symbol(symbol)
        
        # 获取交易历史
        endpoint = "/api/v3/myTrades"
        params = {"symbol": trading_pair, "limit": 1000}  # 获取最近1000笔交易
        
        trades = _make_signed_request(
            api_key=account.binance_api_key,
            secret_key=account.binance_secret_key,
            endpoint=endpoint,
            params=params,
        )
        
        if not trades or len(trades) == 0:
            logger.debug(f"账户 {account.name} 的 {symbol} 没有交易历史")
            return None
        
        # 计算加权平均成本
        # 只考虑买入交易（isBuyer = True）
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        
        for trade in trades:
            is_buyer = trade.get("isBuyer", True)
            if is_buyer:  # 只计算买入
                price = Decimal(str(trade.get("price", "0")))
                qty = Decimal(str(trade.get("qty", "0")))
                total_cost += price * qty
                total_qty += qty
        
        if total_qty > 0:
            avg_cost = float(total_cost / total_qty)
            logger.info(f"从交易历史计算 {account.name} 的 {symbol} 平均成本: ${avg_cost:.6f}")
            return avg_cost
        else:
            logger.debug(f"账户 {account.name} 的 {symbol} 没有买入交易记录")
            return None
            
    except Exception as e:
        logger.warning(f"无法从交易历史计算 {account.name} 的 {symbol} 平均成本: {e}")
        return None


def update_positions_avg_cost():
    """更新所有持仓的平均成本"""
    db = SessionLocal()
    try:
        # 获取所有有 Binance API 密钥的账户
        accounts = db.query(Account).filter(
            Account.binance_api_key.isnot(None),
            Account.binance_secret_key.isnot(None),
        ).all()
        
        if not accounts:
            logger.info("没有找到配置了 Binance API 密钥的账户")
            return
        
        logger.info(f"找到 {len(accounts)} 个账户，开始更新持仓平均成本...")
        
        total_updated = 0
        total_used_current_price = 0
        total_used_trade_history = 0
        
        for account in accounts:
            logger.info(f"\n处理账户: {account.name} (ID: {account.id})")
            
            try:
                # 从 Binance 获取当前持仓
                balance, positions = get_binance_balance_and_positions(account)
                
                if not positions:
                    logger.info(f"  账户 {account.name} 没有持仓")
                    continue
                
                logger.info(f"  找到 {len(positions)} 个持仓")
                
                for binance_pos in positions:
                    symbol = binance_pos["symbol"]
                    quantity = float(binance_pos["quantity"])
                    
                    if quantity <= 0:
                        continue
                    
                    logger.info(f"  处理持仓: {symbol} (数量: {quantity})")
                    
                    # 尝试从交易历史计算平均成本
                    avg_cost = calculate_avg_cost_from_trades(account, symbol)
                    
                    if avg_cost and avg_cost > 0:
                        total_used_trade_history += 1
                        logger.info(f"    ✓ 使用交易历史计算的平均成本: ${avg_cost:.6f}")
                    else:
                        # 使用当前市场价格作为开仓成本
                        try:
                            current_price = get_last_price(symbol, "CRYPTO")
                            if current_price and current_price > 0:
                                avg_cost = current_price
                                total_used_current_price += 1
                                logger.info(f"    ✓ 使用当前市场价格作为开仓成本: ${avg_cost:.6f}")
                            else:
                                logger.warning(f"    ✗ 无法获取 {symbol} 的当前价格，跳过")
                                continue
                        except Exception as e:
                            logger.warning(f"    ✗ 获取 {symbol} 当前价格失败: {e}，跳过")
                            continue
                    
                    # 更新数据库中的 Position
                    db_position = (
                        db.query(Position)
                        .filter(
                            Position.account_id == account.id,
                            Position.symbol == symbol,
                            Position.market == "CRYPTO",
                        )
                        .first()
                    )
                    
                    if db_position:
                        # 更新现有持仓
                        old_avg_cost = float(db_position.avg_cost)
                        db_position.avg_cost = avg_cost
                        logger.info(
                            f"    ✓ 更新数据库持仓 {symbol} 的平均成本: "
                            f"${old_avg_cost:.6f} -> ${avg_cost:.6f}"
                        )
                        total_updated += 1
                    else:
                        # 创建新的持仓记录（如果不存在）
                        logger.info(f"    + 创建新的持仓记录 {symbol}，平均成本: ${avg_cost:.6f}")
                        db_position = Position(
                            version="v1",
                            account_id=account.id,
                            symbol=symbol,
                            name=symbol,
                            market="CRYPTO",
                            quantity=Decimal(str(quantity)),
                            available_quantity=Decimal(str(binance_pos.get("available_quantity", quantity))),
                            avg_cost=Decimal(str(avg_cost)),
                        )
                        db.add(db_position)
                        total_updated += 1
                
                # 提交这个账户的更改
                db.commit()
                logger.info(f"  ✓ 账户 {account.name} 的持仓更新完成")
                
            except Exception as e:
                logger.error(f"  ✗ 处理账户 {account.name} 时出错: {e}", exc_info=True)
                db.rollback()
                continue
        
        logger.info("\n" + "=" * 60)
        logger.info("更新完成！")
        logger.info(f"  总更新持仓数: {total_updated}")
        logger.info(f"  使用交易历史计算: {total_used_trade_history}")
        logger.info(f"  使用当前市场价格: {total_used_current_price}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"更新持仓平均成本时出错: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("持仓平均成本更新脚本")
    print("=" * 60)
    print()
    print("此脚本将：")
    print("1. 尝试从 Binance 交易历史计算平均持仓成本")
    print("2. 如果没有交易历史，使用当前市场价格作为开仓成本")
    print("3. 更新数据库中的 Position 表的 avg_cost 字段")
    print()
    
    response = input("是否继续？(y/n): ")
    if response.lower() != "y":
        print("已取消")
        sys.exit(0)
    
    print()
    update_positions_avg_cost()


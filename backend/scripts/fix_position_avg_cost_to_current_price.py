#!/usr/bin/env python3
"""
修正持仓平均成本到当前市场价格

功能：
1. 通过 Binance API 获取所有账户的实际持仓
2. 获取每个持仓的当前市场价格
3. 将数据库中持仓的 avg_cost 更新为当前市场价格
4. 这样可以修正 Unreal P&L 的计算
"""

import logging
import sys
import time
from decimal import Decimal
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import SessionLocal
from database.models import Account, Position
from services.binance_sync import get_binance_balance_and_positions
from services.market_data import get_last_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def fix_positions_avg_cost_to_current_price():
    """通过Binance API获取持仓，并将平均成本更新为当前市场价格"""
    db = SessionLocal()
    try:
        # 获取所有配置了Binance API密钥的账户
        accounts = db.query(Account).filter(
            Account.binance_api_key.isnot(None),
            Account.binance_secret_key.isnot(None),
        ).all()
        
        if not accounts:
            logger.info("没有找到配置了 Binance API 密钥的账户")
            return
        
        logger.info(f"找到 {len(accounts)} 个账户，开始从Binance获取持仓并更新平均成本...")
        
        total_updated = 0
        total_failed = 0
        total_skipped = 0
        
        for account in accounts:
            logger.info(f"\n{'='*60}")
            logger.info(f"处理账户: {account.name} (ID: {account.id})")
            
            try:
                # 从Binance获取实际持仓
                balance, binance_positions = get_binance_balance_and_positions(account)
                
                if not binance_positions:
                    logger.info(f"  账户 {account.name} 在Binance上没有持仓")
                    continue
                
                logger.info(f"  从Binance获取到 {len(binance_positions)} 个持仓")
                
                for binance_pos in binance_positions:
                    symbol = binance_pos.get("symbol", "").upper()
                    quantity = float(binance_pos.get("quantity", 0))
                    
                    if not symbol or quantity <= 0:
                        total_skipped += 1
                        continue
                    
                    try:
                        # 获取数据库中对应的持仓记录
                        db_position = (
                            db.query(Position)
                            .filter(
                                Position.account_id == account.id,
                                Position.symbol == symbol,
                                Position.market == "CRYPTO",
                            )
                            .first()
                        )
                        
                        old_avg_cost = float(db_position.avg_cost) if db_position else 0
                        
                        logger.info(f"\n  处理持仓: {symbol} (数量: {quantity:.8f}, 当前avg_cost: ${old_avg_cost:.6f})")
                        
                        # 获取当前市场价格
                        current_price = get_last_price(symbol, "CRYPTO")
                        
                        if current_price and current_price > 0:
                            # 更新或创建数据库持仓记录
                            if db_position:
                                # 更新现有持仓
                                db_position.avg_cost = Decimal(str(current_price))
                                db_position.quantity = Decimal(str(quantity))
                                db_position.available_quantity = Decimal(str(binance_pos.get("available_quantity", quantity)))
                                logger.info(
                                    f"    ✓ 更新 {symbol} 的平均成本: ${old_avg_cost:.6f} -> ${current_price:.6f}"
                                )
                            else:
                                # 创建新持仓记录
                                db_position = Position(
                                    version="v1",
                                    account_id=account.id,
                                    symbol=symbol,
                                    name=symbol,
                                    market="CRYPTO",
                                    quantity=Decimal(str(quantity)),
                                    available_quantity=Decimal(str(binance_pos.get("available_quantity", quantity))),
                                    avg_cost=Decimal(str(current_price)),
                                )
                                db.add(db_position)
                                logger.info(
                                    f"    + 创建新持仓 {symbol}，平均成本: ${current_price:.6f}"
                                )
                            
                            total_updated += 1
                        else:
                            logger.warning(f"    ✗ 无法获取 {symbol} 的当前价格，跳过")
                            total_failed += 1
                            continue
                        
                        # 添加小延迟避免API限流
                        time.sleep(0.2)
                        
                    except Exception as e:
                        logger.error(f"    ✗ 处理持仓 {symbol} 时出错: {e}")
                        total_failed += 1
                        continue
                
                # 提交这个账户的更改
                db.commit()
                logger.info(f"  ✓ 账户 {account.name} 的持仓更新完成")
                
            except Exception as e:
                logger.error(f"  ✗ 处理账户 {account.name} 时出错: {e}", exc_info=True)
                db.rollback()
                continue
        
        logger.info("\n" + "=" * 60)
        logger.info("更新完成！")
        logger.info(f"  处理账户数: {len(accounts)}")
        logger.info(f"  成功更新: {total_updated}")
        logger.info(f"  跳过: {total_skipped}")
        logger.info(f"  失败: {total_failed}")
        logger.info("=" * 60)
        logger.info("\n注意: 所有持仓的 avg_cost 已更新为当前市场价格")
        logger.info("Unreal P&L 现在将基于当前价格计算，显示为 0（因为成本=当前价）")
        
    except Exception as e:
        logger.error(f"更新持仓平均成本时出错: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("修正持仓平均成本到当前市场价格")
    print("=" * 60)
    print()
    print("此脚本将：")
    print("1. 通过 Binance API 获取所有账户的实际持仓")
    print("2. 获取每个持仓的当前市场价格")
    print("3. 将数据库中持仓的 avg_cost 更新为当前市场价格")
    print("4. 修正 Unreal P&L 的计算（更新后 Unreal P&L 将为 0）")
    print()
    print("⚠️  警告: 此操作会将所有持仓的开仓成本重置为当前价格")
    print("   这意味着 Unreal P&L 将显示为 0（因为成本=当前价）")
    print()
    
    response = input("是否继续？(y/n): ")
    if response.lower() != "y":
        print("已取消")
        sys.exit(0)
    
    print()
    fix_positions_avg_cost_to_current_price()


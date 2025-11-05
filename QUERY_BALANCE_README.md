# 余额和持仓查询脚本使用说明

## 脚本功能

`query_balance_positions.py` 是一个命令行工具，用于通过API查询账户余额和持仓信息。

## 使用方法

### 基本用法

```bash
# 查询所有账户概览和默认账户详情
python3 query_balance_positions.py

# 查询指定账户的详情
python3 query_balance_positions.py --account-id 1

# 只显示持仓快照
python3 query_balance_positions.py --positions-only

# 刷新余额后查询（清除缓存，获取最新数据）
python3 query_balance_positions.py --account-id 1 --refresh

# 使用自定义API地址
python3 query_balance_positions.py --api-url http://localhost:8802
```

### 参数说明

- `--account-id`: 指定要查询的账户ID（可选）
- `--refresh`: 在查询前强制刷新余额（清除缓存，获取最新Binance数据）
- `--positions-only`: 只显示持仓快照，不显示账户详情
- `--api-url`: 指定API服务器地址（默认: http://localhost:8802）

## 输出示例

### 账户概览
```
📊 ACCOUNT SUMMARY
================================================================================

🔹 Account ID: 1
   Name: My Trading Account
   Type: AI
   Balance: $1,000.00 USDT
   Status: ✅ Active
   Auto Trading: ✅ Enabled
```

### 账户详情
```
💰 ACCOUNT DETAILS
================================================================================

📌 Account Information:
   ID: 1
   Name: My Trading Account
   Type: AI

💵 Balance:
   Available: $1,000.00 USDT
   Frozen: $0.00 USDT

📊 Portfolio:
   Total Assets: $1,000.00 USDT
   Positions Count: 2
   Pending Orders: 0

📈 Positions:
   1. BTC
      Quantity: 0.001
      Avg Cost: $50,000.00
   2. ETH
      Quantity: 0.1
      Avg Cost: $3,000.00
```

### 持仓快照
```
📊 POSITIONS SNAPSHOT
================================================================================

🔹 Account: My Trading Account (ID: 1)
   Cash: $1,000.00 USDT
   Positions (2):
      • BTC
        Quantity: 0.001
        Avg Cost: $50,000.00
        Current Price: $51,000.00
        Unrealized P&L: ✅ $1.00
      • ETH
        Quantity: 0.1
        Avg Cost: $3,000.00
        Current Price: $3,100.00
        Unrealized P&L: ✅ $10.00
```

## 注意事项

1. **API服务器**: 确保API服务器正在运行（默认端口8802）
2. **网络连接**: 脚本需要能够访问API服务器
3. **刷新余额**: 使用 `--refresh` 参数会清除缓存，强制从Binance获取最新数据
4. **缓存机制**: 正常查询可能使用5秒缓存，如需最新数据请使用 `--refresh`

## 故障排除

### 无法连接到API
```
⚠️  Could not connect to API: Connection refused
   Make sure the server is running at http://localhost:8802
```

**解决方案**: 检查API服务器是否正在运行，或使用 `--api-url` 指定正确的地址。

### 账户不存在
```
❌ Error querying account overview: 404 Client Error: Not Found
```

**解决方案**: 检查账户ID是否正确，使用 `python3 query_balance_positions.py` 查看所有可用账户。

## API端点说明

脚本使用以下API端点：

- `GET /api/account/list` - 获取所有账户列表
- `GET /api/account/overview` - 获取默认账户概览
- `GET /api/account/{account_id}/overview` - 获取指定账户概览
- `GET /api/arena/positions` - 获取持仓快照
- `POST /api/account/{account_id}/refresh-balance` - 刷新账户余额


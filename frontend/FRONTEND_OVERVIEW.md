# Hyper Alpha Arena 前端（frontend）快速上手与架构说明

本指南面向 React 初学者，帮助你从 0 上手本项目的前端。内容包括目录结构、每个组件/模块的作用、整体架构与开发运行方式。

## 技术栈与运行时
- 构建工具：Vite 4（React + TypeScript）
- UI 与样式：Tailwind CSS、Radix UI、shadcn 风格的 UI 包装组件（在 components/ui 下）
- 图表：Recharts（主用）、Chart.js、lightweight-charts（部分使用/可扩展）
- 通信：WebSocket（/ws，实时）+ REST API（/api，经 Vite 代理到后端 5611）
- 通知：react-hot-toast
- 端口：前端开发服务默认 8802

## 顶层目录（frontend/）
- app/
  - main.tsx：React 应用入口。负责：
    - 建立单例 WebSocket 连接（避免 StrictMode 重复连接）
    - 发送 bootstrap 并拉取 snapshot、监听实时消息（trades/positions/model_chat 等）
    - 维护页面视图（简单的 hash 方式）与全局数据状态
  - index.css：全局样式（含 Tailwind）
  - images.d.ts：静态资源类型声明
  - lib/
    - api.ts：REST API 封装（统一走 /api，适配 dev 代理与生产）
    - utils.ts：工具函数（例如 className 合并 cn）
  - components/（见下文详细说明）
- public/
  - 静态资源根目录（例如 logo_app.ico、logo_app.png）。注意：通过“根路径”直接访问，如 /logo_app.ico。
- index.html：HTML 模板。通过 <script type="module" src="/app/main.tsx"> 引入入口脚本。
- vite.config.ts：
  - server.port = 8802
  - 代理：/api → http://127.0.0.1:5611，/ws → ws://127.0.0.1:5611
  - 路径别名：@ → ./app
- tailwind.config.js、postcss.config.js、tsconfig.json：样式与 TS 配置
- components.json：UI 组件生成/约定的配置（shadcn 风格）

## 应用架构与数据流
- 入口与页面：
  - index.html → app/main.tsx → <App />
  - App 内部维护 currentPage（'portfolio'｜'comprehensive'｜'system-logs'｜'prompt-management'｜'trader-management'），并通过 Sidebar 切换
  - Header 展示页面标题，主体区按页渲染对应的复合组件
- 实时通信（WebSocket /ws）：
  - 连接后发送 bootstrap；服务端返回 bootstrap_ok 带回 user/account
  - 常见消息：snapshot、trades、order_filled、order_pending、user_switched、account_switched、model_chat_update、asset_curve_update、position_update、error
  - 通过 wsRef.send(...) 主动触发服务端行为（get_snapshot、place_order、switch_user、switch_account 等）
- REST API（/api，经 Vite 代理）：
  - app/lib/api.ts 中统一封装，如 getAccounts()/getArenaTrades()/getArenaPositions()/getArenaAnalytics() 等
  - 约定 JSON 响应；错误时提取 detail/message
- 状态管理：以组件内 useState/useEffect 为主，数据在 App 组织并下发给各子组件

## 组件总览（frontend/app/components/）
按业务域分目录，复用基础 UI（components/ui）。以下为每个组件的用途简述：

### common/
- StockViewer.tsx：通用股票/资产查看器（展示行情与基础信息）
- StockViewerDrawer.tsx：抽屉式的资产查看器容器，便于随时展开/关闭

### crypto/
- CryptoSelector.tsx：加密货币选择器（供下单/过滤等场景使用）
- PriceTicker.tsx：价格滚动/跳动显示（实时或高频刷新）

### layout/
- Header.tsx：页面顶部栏（标题、主题切换，显示项目 Logo）
- Sidebar.tsx：左侧导航（在不同页面间切换、触发刷新与账户相关动作）
- AccountSelector.tsx：账户选择/切换控件（与后端账户列表交互）
- SettingsDialog.tsx：设置弹窗（如 API/模型等配置项）
- SystemLogs.tsx：系统日志/事件流展示（调试/观察实时状态）

### portfolio/
- Portfolio.tsx：Crypto Trading 页面主容器，聚合账户概览、持仓、订单、交易、实时价格等
- ComprehensiveView.tsx：Hyper Alpha Arena 综合视图（账户/交易/AI 决策/曲线等一屏呈现）
- AccountDataView.tsx：账户数据（总资产、持仓、订单、交易）细化展示
- StrategyPanel.tsx：策略配置与触发模式设置（realtime/interval/tick_batch 等）
- AssetCurveWithData.tsx：资产曲线（Recharts 折线/面积图，支持多资产或多序列）
- AlphaArenaFeed.tsx：Arena 交易 Feed（聚合多账户的最新成交）
- ArenaAnalyticsFeed.tsx：Arena 汇总分析（收益、波动、Sharpe、费用、成交量等）
- RealtimePrice.tsx：实时价格卡片
- FlipNumber.tsx / AnimatedNumber.tsx：数值动画/翻转效果
- HighlightWrapper.tsx：高亮包装（强调重要数字/卡片）
- logoAssets.ts：模型与图表 Logo 映射（与 components/ui/public 下的资源对应）

### prompt/
- PromptManager.tsx：提示词模板与账户绑定管理（读取/更新模板、维护绑定关系）

### trader/
- TraderManagement.tsx：AI Trader 管理页（创建/编辑交易账号、模型与密钥等）

### trading/
- AuthDialog.tsx：认证/登录等授权对话框
- OrderForm.tsx：下单表单（价格/数量/方向等参数）
- TradeButtons.tsx：快捷交易按钮（买/卖/市价/限价等操作）

### ui/
- badge.tsx、button.tsx、card.tsx、dialog.tsx、drawer.tsx、input.tsx、scroll-area.tsx、select.tsx、table.tsx、tabs.tsx：
  - 统一的基础 UI 组件封装（Tailwind + Radix）。建议优先复用，保持一致性
- public/：UI 相关静态资源（logo、图标、SVG 等）

## 开发运行与常见注意
- 运行前端（仅前端）：
  - cd frontend && pnpm install && pnpm dev → http://localhost:8802/
- 同时启动前后端：
  - 在仓库根目录：pnpm install && pnpm dev（用 concurrently 启动后端 + 前端）
- 静态资源路径：
  - 放在 frontend/public/ 下，访问用根路径（例：/logo_app.ico）。避免写 /static/...（会 404）
- 路径别名：@ 表示 ./app（例：import Header from '@/components/layout/Header'）

## 入门建议（从哪看、怎么改）
1) 从 app/main.tsx 入手：看 App 如何连 WebSocket、如何切换页面、如何把数据传给子组件
2) 按页面查看：
   - 综合页：components/portfolio/ComprehensiveView.tsx
   - 交易页：components/portfolio/Portfolio.tsx
   - 日志页：components/layout/SystemLogs.tsx
   - 提示词页：components/prompt/PromptManager.tsx
   - 管理页：components/trader/TraderManagement.tsx
3) 新增组件：放在对应业务目录；复用 components/ui 下的基础组件；接口统一走 lib/api.ts；实时用 wsRef
4) 图标/图片：放 public/ 或 components/ui/public/，引用时用根路径或相对导入（Vite 会处理）


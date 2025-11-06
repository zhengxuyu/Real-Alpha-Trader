# 更新记录（frontend/update.md）

- 时间：2025-11-06
- 目的：修复左上角 Logo 在开发环境的 404，并兼容生产环境；补充 TS 对 Vite 环境变量的类型支持。

## 变更摘要
- Header 组件（app/components/layout/Header.tsx）
  - 新增按环境切换的路径变量：`const logoSrc = import.meta.env.PROD ? "/static/logo_app.png" : "/logo_app.png"`
  - 使用该变量渲染：`<img src={logoSrc} ... />`
- TypeScript 配置（tsconfig.json）
  - 在 `compilerOptions` 中增加：`"types": ["vite/client"]`，以支持 `import.meta.env` 类型
- HTML 模板（index.html）
  - 将 favicon 路径从 `/static/logo_app.ico` 改为 `/logo_app.ico`，以适配 Vite 开发服务器的 public 目录

## 原因与效果
- 开发环境（Vite dev）从 `frontend/public` 以根路径 `/` 提供静态资源，直接使用 `/logo_app.png`、`/logo_app.ico` 可避免 404。
- 生产环境由后端 FastAPI 挂载 `/static` 提供构建产物，`import.meta.env.PROD` 分支使用 `/static/logo_app.png` 保持兼容。

## 验证
- 开发环境：http://localhost:8802/，刷新后不再出现 404，左上角 Logo 与标签页 favicon 正常显示。
- 生产环境：构建并部署后，资源由 `/static` 提供，Logo 显示正常。


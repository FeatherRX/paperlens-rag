# PaperLens RAG Frontend

React + TypeScript + Vite 前端。开发时统一请求 `/api`，由 Vite 代理到本地 FastAPI；前端不直接访问 OpenAlex，也不包含任何 OpenAlex API Key。

```powershell
D:\Node\npm.cmd install
D:\Node\npm.cmd run dev
```

验证：

```powershell
D:\Node\npm.cmd test -- --run
D:\Node\npm.cmd run lint
D:\Node\npm.cmd run build
D:\Node\npm.cmd run format:check
```

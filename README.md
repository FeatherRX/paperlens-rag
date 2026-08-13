# PaperLens RAG

PaperLens RAG 是一个以“输入研究主题”为入口的论文研究助手。当前 MVP 由 FastAPI 后端与 React 前端组成：后端通过 OpenAlex 检索论文并准备统一来源状态，前端支持用户搜索并自行选择 3～5 篇论文。

当前阶段不实现 PDF 上传、LLM、向量数据库、全文解析或 Docker。OpenAlex 返回的 Abstract 是论文原始摘要元数据，不是系统生成的全文总结。

前后端分离开发、同仓库维护：FastAPI 位于仓库根目录，唯一前端工程位于 `frontend/`。前端采用 React + TypeScript + Vite、React Router、TanStack Query、CSS Modules，并且只调用 FastAPI HTTP/JSON API，绝不直接访问 OpenAlex 或读取 `OPENALEX_API_KEY`。

完整且冻结的产品方向见 [`PROJECT_SPEC.md`](PROJECT_SPEC.md)。

## 环境要求

- Python 3.13
- Node.js 24 与 npm 11（前端开发）
- OpenAlex API key（真实检索时使用，可在 OpenAlex 设置页免费获取）

## 创建并激活虚拟环境

在 Windows PowerShell 中运行：

```powershell
D:\Python313\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
```

也可以不激活虚拟环境，直接使用 `.\.venv\Scripts\python.exe` 执行后续命令。

## 安装依赖

安装运行和测试所需依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

只安装运行时依赖：

```powershell
python -m pip install -r requirements.txt
```

## 配置 OpenAlex

复制示例配置文件：

```powershell
Copy-Item .env.example .env
```

然后在本地 `.env` 中手动填写 `OPENALEX_API_KEY`。`.env` 和 `.env.*` 已被 Git 忽略，`.env.example` 会保留在仓库中；不要把真实 key 写入 `.env.example` 或其他可提交文件。

程序启动时会自动读取项目根目录的 `.env`。已有的进程环境变量优先，不会被 `.env` 覆盖。未配置或配置为空时，OpenAlex 客户端不会发送 `api_key` 参数，并使用匿名请求。

## 启动 FastAPI

```powershell
python -m uvicorn app.main:app --reload
```

启动后可访问：

- 健康检查：`http://127.0.0.1:8000/health`
- API 文档：`http://127.0.0.1:8000/docs`

## 启动前端

前端位于 `frontend/`。先安装锁定文件记录的依赖，再启动 Vite 开发服务器：

```powershell
Set-Location frontend
D:\Node\npm.cmd install
D:\Node\npm.cmd run dev
```

默认访问 `http://127.0.0.1:5173/`。开发服务器把所有 `/api` 请求代理到本地 FastAPI（默认 `http://127.0.0.1:8000`），并在转发时移除 `/api` 前缀。因此需要同时运行后端与前端；前端不会直接访问 OpenAlex，也不需要任何 API Key。

页面流程为：输入研究主题并搜索，用户自行勾选 3～5 篇论文，然后点击“准备分析”。准备结果当前只说明开放全文候选、仅原始摘要或暂无可用语料；不会下载、解析或总结全文。

## 搜索论文

`GET /papers/search` 接收：

- `query`：必填、非空的研究主题。
- `limit`：可选，范围为 1～10，默认 10。

PowerShell 调用示例：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/papers/search?query=retrieval%20augmented%20generation&limit=10"
```

响应包含查询主题、结果数以及标准化论文列表。每篇论文包括 OpenAlex ID、标题、作者、机构、年份、原始摘要、DOI、论文落地页、开放获取状态和引用数。缺失的单值字段返回 `null`，缺失的作者或机构返回空列表。

OpenAlex 超时返回 HTTP 504；OpenAlex 请求失败或响应无效返回 HTTP 502。

## 准备选中的论文来源

论文选择必须由用户完成：

1. 用户先调用 `GET /papers/search` 检索相关论文。
2. 用户在搜索结果中自行选择 3～5 篇论文。
3. 前端把选中的 OpenAlex ID 发送给 `POST /papers/prepare`。

系统不会自动替用户选择论文。短格式 ID 和完整 OpenAlex URL 可以混合使用，但规范化后不得重复：

```powershell
$body = @{
    paper_ids = @(
        "W2741809807"
        "https://openalex.org/W4389984066"
        "W3038568908"
    )
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/papers/prepare" `
    -ContentType "application/json" `
    -Body $body
```

响应包含成功准备的论文数量、现有论文元数据及以下来源准备信息：

```json
{
  "count": 3,
  "papers": [
    {
      "source_status": "fulltext_candidate",
      "fulltext_url": "https://example.org/paper.pdf",
      "fulltext_license": "cc-by",
      "openalex_content": {
        "pdf_available": true,
        "grobid_xml_available": true,
        "content_url": "https://content.openalex.org/example.xml"
      }
    }
  ]
}
```

`source_status` 只允许：

- `fulltext_candidate`：`best_oa_location` 明确为开放获取且提供非空 PDF URL；只表示开放全文候选，不表示已经完成版权复核或全文分析。
- `abstract_only`：没有合格的开放 PDF 候选，但存在原始 Abstract。
- `unavailable`：开放 PDF 候选和 Abstract 均不存在。

`has_content.pdf` 和 `has_content.grobid_xml` 只表示 OpenAlex 缓存格式的可用性，不能证明全文具有开放授权，也不会令论文自动成为 `fulltext_candidate`。当前接口不会下载 PDF、XML 或其他全文，不会消耗 OpenAlex 内容下载额度，也不会解析或总结全文。

单篇论文不存在返回 HTTP 404；OpenAlex 超时返回 HTTP 504；其他 OpenAlex 请求或响应错误返回 HTTP 502。

## 运行测试

```powershell
python -m pytest -q
```

搜索和准备接口测试使用 mock HTTP transport，不依赖真实 OpenAlex 网络。

前端测试、代码检查、构建与格式检查：

```powershell
Set-Location frontend
D:\Node\npm.cmd test -- --run
D:\Node\npm.cmd run lint
D:\Node\npm.cmd run build
D:\Node\npm.cmd run format:check
```

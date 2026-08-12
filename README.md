# PaperLens RAG

PaperLens RAG 是一个以“输入研究主题”为入口的论文研究助手后端。当前 MVP 通过 OpenAlex 检索相关论文，并返回统一的论文元数据，为后续的论文选择、合法开放全文处理、检索和带来源回答奠定基础。

当前阶段不实现 PDF 上传、LLM、向量数据库、全文解析、前端或 Docker。OpenAlex 返回的 Abstract 是论文原始摘要元数据，不是系统生成的全文总结。

完整且冻结的产品方向见 [`PROJECT_SPEC.md`](PROJECT_SPEC.md)。

## 环境要求

- Python 3.13
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

## 运行测试

```powershell
python -m pytest -q
```

搜索接口测试使用 mock HTTP transport，不依赖真实 OpenAlex 网络。

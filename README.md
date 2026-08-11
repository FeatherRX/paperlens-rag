# PaperLens RAG

PaperLens RAG 是一个面向论文检索增强生成（RAG）场景的后端项目。当前版本提供基于 FastAPI 的最小可运行、可测试 API 骨架，暂未接入 LLM、向量数据库、业务数据库或 PDF 解析。

## 环境要求

- Python 3.13

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

## 启动 FastAPI

```powershell
python -m uvicorn app.main:app --reload
```

启动后可访问健康检查接口：`http://127.0.0.1:8000/health`。

## 运行测试

```powershell
python -m pytest -q
```

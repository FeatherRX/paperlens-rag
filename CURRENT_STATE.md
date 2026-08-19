# PaperLens 当前状态

本文是 PaperLens 求职 Demo 当前实现与部署状态的简明事实基线，记录日期为 2026-08-20。`PROJECT_SPEC.md` 继续约束产品方向；当前已经实现的功能以本文件和实际代码为准。

## 产品范围

- 入口始终是“输入研究主题”，论文来源为 OpenAlex。
- 用户必须从搜索结果中自行选择 3～5 篇论文，系统不自动代选。
- 系统只获取原始 Abstract 和许可证明确允许的 OpenAlex canonical 开放全文。
- PDF 上传不是当前入口，也未实现。
- 原始 Abstract 始终按论文原始摘要展示，不能冒充系统生成的全文总结。
- 当前 Demo 已覆盖搜索、来源准备、受控摄取、RAG 问答和来源引用。

## 最终技术栈

- 后端：Python 3.13、FastAPI 0.141.1、Uvicorn 0.52.1、Pydantic、httpx2、python-dotenv。
- 内容处理：OpenAlex、defusedxml、pypdf；优先 GROBID XML，其次 PDF，必要时使用 Abstract fallback。
- Chunking：项目自有的 structure-aware character chunking。
- Embedding：FastEmbed 0.8.0、ONNX Runtime 1.28.0、`BAAI/bge-small-en-v1.5`、384 维、L2 normalization。
- Retrieval：项目自有的 in-memory exact Top-K；单位向量点积等价 cosine similarity。
- Answer：百炼 OpenAI-compatible Chat Completions，当前配置模型为 `qwen3.7-flash`，关闭 thinking；回答受 evidence prompt 约束。
- 前端：React 19、TypeScript 6、Vite 8、React Router 7、TanStack Query 5、CSS Modules。
- 测试：pytest、Vitest、React Testing Library。
- 部署：Alibaba Cloud ECS Ubuntu 22.04、Docker Compose、容器 Nginx、宿主机 Nginx、Let's Encrypt HTTPS。
- 不使用 Qdrant、pgvector、FAISS、Pinecone、Milvus 或其他 Vector DB。

## 端到端数据流

```text
research topic
  -> GET /papers/search -> OpenAlex metadata
  -> user selects 3-5 papers
  -> POST /papers/prepare -> source availability review
  -> explicit user confirmation
  -> POST /papers/ingest
       -> authoritative OpenAlex metadata and license check
       -> GROBID XML / PDF / original Abstract fallback
       -> normalized document JSON in data/ingested/
  -> user asks a question
  -> POST /rag/answer
       -> load only requested normalized documents
       -> per-paper corpus embedding cache lookup
       -> cache miss: chunk -> FastEmbed corpus embeddings -> atomic cache save
       -> cache hit: restore EmbeddedChunk[]
       -> query embedding -> exact Top-K retrieval
       -> Qwen evidence-grounded answer
       -> numbered citations with paper/chunk/page/section evidence
  -> React answer and citation UI
```

Prepare 不会自动触发 ingest；ingest 完成后仍需用户明确发起问答。前端只调用 FastAPI 的同源 `/api`，不会直接调用 OpenAlex，也不会读取 API Key。

## 当前 API

| Method | Path | 当前职责 |
| --- | --- | --- |
| GET | `/health` | 返回服务健康状态。 |
| GET | `/papers/search` | 接收非空 `query` 和 `limit`（默认/最大 10），返回规范化 OpenAlex 搜索结果。 |
| POST | `/papers/prepare` | 接收用户选择的 3～5 个 Work ID，重新获取元数据并报告全文候选、Abstract-only 或 unavailable。 |
| POST | `/papers/ingest` | 接收同一 3～5 个 Work ID，执行许可证校验、受控内容获取、解析和 normalized document 持久化。 |
| POST | `/rag/answer` | 接收 `query`、3～5 个已摄取 `paper_ids` 和可选 `top_k`（默认 5），返回 `answer` 与 citations。 |

实际 QA endpoint 是 `POST /rag/answer`，不存在 `/papers/qa`。Citation 包含编号、论文 ID/标题、chunk index、页码、章节、evidence excerpt 和 retrieval score。

## Chunking、Embedding 与 Retrieval

- 默认 `max_characters=1200`、`overlap_characters=150`；overlap 会按可用文本边界调整，因此是约 150 字符。
- 保持 normalized segments 原始顺序，尽量不跨 section。
- 分割优先使用 paragraph boundary，其次 whitespace，最后才 hard split。
- Chunk 保留 `paper_id`、连续 `chunk_index`、正文、`section_title` 和可用的 `page_numbers`。
- Corpus 和 query embeddings 均验证为 384 维有限数值并执行 L2 normalization。
- Retrieval 对所选论文的全部 chunks 做 normalized dot product，按 score 降序并使用稳定 tie-break 返回 Top-K。
- Retrieval 与 Citation 只使用请求中的论文，不会把其他已摄取文件加入 corpus。

## Corpus embedding cache

- 每篇论文单独缓存于 `data/ingested/.corpus-embeddings/{paper_id}.json`。
- Cache envelope 记录 schema、paper ID、normalized document fingerprint、document schema、chunking config/algorithm signature、embedding model/dimension/pipeline/normalization、chunks fingerprint 和 `EmbeddedChunk[]`。
- Fingerprint 覆盖 segment text、顺序、section、page number、document schema、paper ID 和 source type。
- Cache hit 完全跳过该论文的 corpus chunking 与 corpus embedding；多篇论文 partial hit 时仍按请求中的 `paper_ids` 顺序组合 corpus。
- Cache miss 使用现有 chunking 和 FastEmbed，随后通过临时文件、`fsync`、`os.replace` 原子写入 JSON。
- 损坏 JSON、非法字段、fingerprint/signature 不匹配、错误维度、NaN/Infinity 或非单位向量均按 miss 重建，不改变 API 行为。
- Docker 中该目录位于现有 `ingested_data` volume 内，因此 backend restart 和 force-recreate 后仍保留。
- FastEmbed 模型文件另存于 `fastembed_cache` volume；FastEmbed 模型对象目前仍在每次 `/rag/answer` 请求中初始化。

## 当前部署状态

- Server project：`/opt/paperlens-rag`。
- Alibaba Cloud ECS，Ubuntu 22.04，Docker Compose。
- 当前公网入口：`https://47.238.193.115`。
- 宿主机 Nginx：`:80` 普通请求重定向到 HTTPS，ACME challenge 保留 HTTP webroot；`:443` TLS termination 后代理到 `127.0.0.1:8080`。
- 安全组：公网仅开放 `80/443`；`22` 仅当前 VPN 出口 IP `/32`；`8080` 不对公网开放。
- HTTPS：Let's Encrypt 免费公网 IP short-lived certificate；Certbot 5.7.0；renewal dry-run 已通过；deploy hook 会在续期后 reload Nginx。
- ECS economical stop mode 可能改变公网 IP；变化后必须重新处理证书、Nginx IP 配置和对外入口。
- SSH 使用 `paperlens-demo-key.pem`；切换 VPN 节点前必须先更新安全组 SSH 来源 IP。

详细恢复和安全操作见 `DEPLOYMENT.md`。

## 实测性能

以下为当前 ECS 环境中的观察值，不是 SLA 或严格 benchmark：

- 早期未缓存 QA 可能约 1～2 分钟，FastEmbed/ONNX CPU 峰值约 189%。
- 当前首次 corpus cache miss 约 30 秒。
- Corpus cache hit 约 2～4 秒。
- Cache hit 消除了重复 corpus chunking 和 corpus embedding；query embedding、exact retrieval、Qwen 调用以及每请求 FastEmbed 初始化仍会执行。

## Git 与发布基线

- 本文档分支从已同步的 `main` 创建。
- 创建时 `main` 与 `origin/main` 均指向 `7ff3bc349a27a628f391bb02a71feabf846be900`：`Merge pull request #11 from FeatherRX/feat/corpus-embedding-cache`。
- 对应性能提交为 Git 已确认的 `22024688658526c5221be9dcf6537561af70e5e5`：`perf: cache corpus embeddings`。
- 当前部署已经使用 Docker Compose、宿主机 Nginx 和 HTTPS 完成公开访问；服务器实际 checkout 应在每次发布时用 `git rev-parse HEAD` 单独确认，不能仅凭本文推断。

## 已知限制与后续改进

- Top-K 可能集中命中同一论文的多个 chunks；尚无 reranker 或跨论文 diversification。
- 没有 Vector DB，当前 corpus 在请求内执行 in-memory exact retrieval。
- 部分数学 Markdown/LaTeX 尚未在前端渲染。
- Cache pipeline/algorithm 失效部分依赖手动维护版本常量。
- Cache JSON 没有显式文件大小上限。
- 首次未缓存 QA 仍较慢；FastEmbed 模型仍按请求初始化。
- 当前公网 IP HTTPS 与 IP 绑定；ECS 公网 IP 变化需要人工恢复。

后续优先候选包括：检索结果 diversification/reranking、数学公式渲染、FastEmbed 进程级生命周期评估、缓存大小防护与更自动化的 pipeline revision 标识。上述改进不得改变用户手动选择 3～5 篇论文、合法内容获取边界或当前 Citation 契约。

## 文档一致性

`README.md` 和 `PROJECT_SPEC.md` 已与当前 RAG 实现完成同步，不再保留“Embedding、向量检索、LLM 或引用回答尚未实现”的过时状态描述。`PROJECT_SPEC.md` 继续作为产品方向约束；当前实现和部署状态以本文件、实际代码及 Git 为准。

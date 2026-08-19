# PaperLens RAG 产品规格

## 产品目标

PaperLens 允许用户输入研究主题，系统自动检索高相关论文，理解并总结论文内容；后续根据用户意图，从选定论文中检索关键论据并生成带来源引用的回答。

## 冻结主链路

1. 用户输入研究主题。
2. 通过 OpenAlex 检索前 10 篇相关论文。
3. 展示作者、机构、年份、摘要、论文链接和开放获取状态。
4. 用户选择 3～5 篇论文。
5. 获取论文摘要及合法开放全文。
6. 对内容进行解析、分块、Embedding 和向量索引。
7. 生成论文内容的结构化总结。
8. 按用户意图检索关键论据。
9. 生成带来源引用的综合回答。

## 当前 MVP 范围

- 产品入口固定为“输入研究主题”。
- 使用 OpenAlex 检索相关论文，默认返回前 10 篇。
- 返回稳定的内部论文数据模型，包括作者、机构、年份、摘要、链接、开放获取状态和引用数等元数据。
- 用户从搜索结果中明确选择 3～5 篇论文后，系统重新获取权威记录并准备来源状态；系统不得自动替用户选择论文。
- 来源准备阶段只标记开放 PDF 候选、Abstract-only 或不可用状态，不下载或分析全文。
- 全文摄取阶段只处理用户已选择的 3～5 篇论文：重新校验 OpenAlex 元数据和许可证，仅从 OpenAlex canonical 内容接口获取允许授权的 GROBID XML 或 PDF，提取带章节或页码定位的规范化文本并保存本地缓存。
- 全文摄取允许的许可证至少包括 `cc-by`、`cc-by-sa`、`cc0` 和 `public-domain`；缺失、未知或不在允许列表中的许可证不得触发全文下载。
- 全文内容请求、格式校验或解析失败后产生的 Abstract 回退必须缓存非敏感失败分类、尝试时间和 OpenAlex 来源版本；同来源版本在 6 小时内不得因重复调用再次产生内容下载费用。缺少 Key 的回退以及许可证或来源版本发生变化的记录允许重新评估。
- 只获取摘要以及合法、明确开放获取的全文。
- 已摄取文档使用固定的 structure-aware character chunking，并通过 FastEmbed 0.8.0、ONNX Runtime 1.28.0 和 `BAAI/bge-small-en-v1.5` 生成 384 维 L2-normalized embeddings。
- 当前检索使用自定义 in-memory exact Top-K 和 normalized dot product，不引入 Qdrant、pgvector、FAISS 或其他 Vector DB。
- 当前问答使用 Qwen 对检索 evidence 生成受证据约束的回答，并返回与 evidence 编号对应的 citations。
- 每篇论文的 corpus embeddings 以 JSON 持久化在 `data/ingested/.corpus-embeddings/`，通过 normalized document fingerprint 以及 chunking/embedding signatures 控制失效和重建。
- 保留 FastAPI 后端和自动化测试。
- 支持使用 Docker Compose 在本地一次启动 FastAPI 后端与 React 静态前端；部署方式不得改变现有产品入口、内容授权边界或用户选择规则。

## 前端架构（冻结）

- 前端固定采用 React、TypeScript 和 Vite。
- 前后端分离开发，但在同一个 Git 仓库维护；`frontend/` 是唯一前端目录。
- 使用 React Router 管理前端路由，使用 TanStack Query 管理 API 服务器状态。
- 使用 CSS Modules 与全局 CSS 设计变量构建界面。
- 前端只通过 FastAPI HTTP/JSON API 访问数据，不得直接调用 OpenAlex，也不得读取、传递或打包 `OPENALEX_API_KEY`。
- 用户必须从搜索结果中自行选择 3～5 篇论文，系统和前端不得自动替用户选择。
- PDF 上传不是当前产品入口。
- 前端展示的 Abstract 必须明确标注为论文原始摘要，不得表示为系统生成的全文总结。

## 明确排除项

- 本阶段不实现 PDF 上传；PDF 上传仅是未来增强能力，不能作为当前入口。
- 不实现简历或 JD 匹配。
- 不把 OpenAlex 原始 Abstract 标记或展示为系统生成的全文总结。
- 不获取付费论文或无授权全文。
- 当前不实现语义分块、Vector DB 或独立的论文结构化总结；现有实现使用固定 character chunking、本地 FastEmbed embeddings、自定义 exact retrieval，以及 Qwen 带 citations 的 RAG 回答。

最终实现状态见 [`CURRENT_STATE.md`](CURRENT_STATE.md)；部署运维见 [`DEPLOYMENT.md`](DEPLOYMENT.md)。

## 产品方向变更规则

以上产品入口、主链路、MVP 范围和排除项均为冻结要求。未经用户明确确认，不得改变产品入口、开发方向或合法内容获取边界。

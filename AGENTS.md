# PaperLens RAG 开发约束

在分析需求、制定方案或修改代码之前，必须完整阅读根目录的 `PROJECT_SPEC.md`。

后续开发必须遵守以下规则：

- 不得擅自把产品入口从“输入研究主题”改为 PDF 上传或其他入口。
- 不得偏离 `PROJECT_SPEC.md` 中冻结的产品目标、主链路和 MVP 范围。
- 不得实现简历或 JD 匹配方向。
- 不得把原始 Abstract 冒充系统生成的全文总结。
- 不得获取付费或无授权论文全文。
- 如需求可能改变产品方向、技术边界或合法内容获取边界，必须先获得用户明确确认。
- 保留并维护 `GET /health` 及其测试。
- 前端技术栈固定为 React + TypeScript + Vite，且只能位于根目录的 `frontend/`。
- 前端必须通过 FastAPI API 访问数据，不得直接调用 OpenAlex，不得读取或打包 `OPENALEX_API_KEY`。
- 前端论文选择必须由用户明确操作，数量限制为 3～5 篇，不得自动选择。
- 前端不得使用 Jinja2、Vue、Next.js、Redux、Zustand 或大型 UI 组件库。

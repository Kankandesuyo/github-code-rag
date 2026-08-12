# GitHub Code RAG Architecture

本文档说明当前项目的文件组织和核心运行链路，用于后续维护、答辩或继续扩展。

## 目录分层

```text
app/
  main.py                 FastAPI 路由入口
  config.py               环境变量和运行配置
  schemas/                API 请求/响应结构
  agents/                 Agent 角色封装
  analyzers/              静态分析器
  graph/                  LangGraph 编排
  services/               核心业务服务
  static/                 前端聊天页面
  utils/                  文件过滤等通用工具
tests/                    后端测试
docs/                     架构和说明文档
repos/                    运行时索引元数据目录，不保存仓库源码，不提交
chroma_db/                运行时向量库目录，不提交
dist/                     交付包目录，不提交
```

## 核心流程

```text
默认联网问答：
GitHub URL -> 安全远程读取 -> 内存切块 -> 内存 BM25/关键词检索
  -> Reranker -> LLM/本地回退回答 -> 释放请求数据

深度分析：
GitHub URL 或 ZIP -> Repository Loader -> File Parser -> Symbol-level Chunks
  -> 本地快照与 Chroma Vector Store -> Retriever / Reranker
  -> LLM Answer Generator -> Answer + Sources + Agent Logs
```

两条链路由独立 API 保证数据边界。`POST /chat/online` 没有客户端可控制的 `persist` 参数，并且不调用 `load_repository()`、Chroma 或索引 manifest；`POST /repository/load` 才负责持久化。联网模式仍会在请求期间把过滤后的源文件读入服务端内存，配置外部模型时还会发送最终选中的脱敏证据片段。

## 主要模块

### API Layer

- `app/main.py`
  - `GET /health`
  - `GET /repositories`
  - `POST /repository/load`
  - `POST /chat/online`
  - `POST /chat`
  - `GET /repository/report/{repository_id}`
  - `POST /repository/generate-readme`
  - `POST /debug/retrieval`

### Services

- `repo_loader.py`
  - 生成稳定 `repository_id`
  - 通过 GitHub API 远程遍历默认分支文件树
  - 按过滤规则读取受支持的远程文件内容并生成 chunks
  - 通过 `persist_manifest=False` 提供不写远程 manifest 的请求级读取入口
- `online_search.py`
  - 组合临时 GitHub 读取、内存 BM25/关键词召回、RRF 融合和 rerank
  - 不创建或查询 Chroma collection
- `file_parser.py`
  - 多编码文本读取
  - PDF/DOCX/XLSX 可选读取
  - Python AST 切分
  - JS/TS/Java 等语言结构化切分
- `vector_store.py`
  - Chroma 持久化
  - 文件级增量索引 manifest
  - 向量检索、BM25、关键词召回、RRF 融合
- `llm_service.py`
  - query expansion
  - HyDE
  - local rerank / optional LLM rerank
  - DeepSeek 兼容 OpenAI SDK 的回答生成
- `report_service.py`
  - 项目报告生成
  - README 草稿生成
  - 模块依赖和 Python 函数调用关系整合
  - 分析结果 manifest 缓存

### Agents

- `SupervisorAgent`
  - 决定报告或 README 生成路线。
- `RepositoryAgent`
  - 扫描目录结构、语言、入口文件和核心模块。
- `TechStackAgent`
  - 调用技术栈分析器输出 Backend、Frontend、Database、DevOps、AI。
- `RAGAgent`
  - 检索相关代码符号，日志中以 `CodeRetriever` 展示。
- `WriterAgent`
  - 生成项目报告和 README 文档内容。

### Analyzers

- `TechStackAnalyzer`
  - 从依赖文件、Docker、README 等识别技术栈。
- `APIAnalyzer`
  - 识别 FastAPI、Flask、Express 路由。
- `DatabaseAnalyzer`
  - 识别 SQLAlchemy、Django ORM、Prisma、Mongoose。
- `EntrypointAnalyzer`
  - 识别启动入口、应用入口、配置入口。
- `CallGraphAnalyzer`
  - 使用 Python AST 静态识别同文件、类方法和显式导入函数调用。
  - 不导入、不执行被分析仓库代码；动态分派和运行时反射不做猜测。

## 运行时产物

以下目录和文件属于运行产物，已通过 `.gitignore` 排除：

- `.venv/`
- `.pytest_cache/`
- `__pycache__/`
- `repos/`
- `chroma_db/`
- `dist/`
- `*.log`

## 当前架构边界

当前项目已经把 API、Agent、Analyzer、Service、Schema、Static UI 分开。后续新增功能时优先按以下规则放置：

- 新接口放在 `app/main.py`。
- 新请求/响应结构放在 `app/schemas/`。
- 新业务流程放在 `app/services/`。
- 新静态识别规则放在 `app/analyzers/`。
- 新 Agent 角色放在 `app/agents/`。
- 前端交互放在 `app/static/`。

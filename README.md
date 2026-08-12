# GitHub Code RAG

可本地运行和容器部署的 GitHub 代码问答应用，默认支持不建立本地索引的联网问答，也保留可重复使用的深度分析知识库。

项目更新记录见：[UPDATE_README.md](./UPDATE_README.md)

功能：

- 默认“联网问答”：输入公开 GitHub 仓库 URL 后，在单次请求内临时读取、切块和检索，不 clone、不下载完整 archive，也不保存源码快照、manifest 或 Chroma 索引
- 可选“深度分析”：显式导入后保存经过安全过滤的文本快照与向量索引，支持连续问答、报告和 README 生成
- 读取常见代码和文档文件
- 过滤依赖目录、构建产物、锁文件、二进制文件和 `.env`
- 使用 LangChain 切分文本
- 使用 ChromaDB 保存向量索引到 `chroma_db/`
- 默认使用本地开源 `sentence-transformers` embedding 模型生成语义向量
- 支持 BM25 + 向量检索 + RRF 混合召回
- 支持 Multi-query、HyDE、相邻 chunk 扩展、本地 reranker 二次排序和结构化来源引用
- 支持 LangGraph 编排的 V2 Codebase Agent
- 支持项目分析报告和 README 自动生成
- 项目报告支持 Python 函数级调用关系，展示调用方、被调用函数、文件和行号
- 支持 Agent 执行日志、耗时和缓存命中状态返回
- 使用 DeepSeek Chat API 基于检索片段回答问题
- 支持可选的单管理员登录、签名 Session Cookie、CSRF 防护和 API Key 自动化访问

## 项目结构

```text
github-code-rag/
├─ app/
│  ├─ main.py
│  ├─ config.py
│  ├─ schemas/
│  │  ├─ __init__.py
│  │  └─ report_schema.py
│  ├─ agents/
│  │  ├─ supervisor_agent.py
│  │  ├─ repository_agent.py
│  │  ├─ techstack_agent.py
│  │  ├─ rag_agent.py
│  │  └─ writer_agent.py
│  ├─ analyzers/
│  │  ├─ techstack_analyzer.py
│  │  ├─ api_analyzer.py
│  │  ├─ database_analyzer.py
│  │  ├─ entrypoint_analyzer.py
│  │  └─ call_graph_analyzer.py
│  ├─ graph/
│  │  └─ workflow.py
│  ├─ services/
│  │  ├─ repo_loader.py
│  │  ├─ online_search.py
│  │  ├─ file_parser.py
│  │  ├─ vector_store.py
│  │  ├─ llm_service.py
│  │  ├─ manifest_service.py
│  │  └─ report_service.py
│  ├─ static/
│  │  ├─ index.html
│  │  ├─ styles.css
│  │  └─ app.js
│  └─ utils/
│     └─ file_utils.py
├─ docs/
│  └─ ARCHITECTURE.md
├─ tests/
│  └─ test_v2_agents.py
├─ repos/
├─ chroma_db/
├─ requirements.txt
├─ .env.example
└─ README.md
```

架构说明见：[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

## 环境要求

- Python 3.12
- Git
- Windows PowerShell 或其他终端

确认 Git 可用：

```powershell
git --version
```

## 安装

```powershell
cd E:\github-code-rag
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果网络不稳定，建议使用国内 PyPI 镜像安装。

## 配置

复制环境变量示例文件：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

当前默认使用本地开源语义 embedding：

```env
EMBEDDING_PROVIDER=sentence_transformers
EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5
```

如果本地模型安装或下载失败，可以临时回退：

```env
EMBEDDING_PROVIDER=hash
HASH_EMBEDDING_DIMENSIONS=768
```

检索增强相关开关：

```env
ENABLE_QUERY_EXPANSION=true
ENABLE_HYDE=true
ENABLE_LOCAL_RERANK=true
ENABLE_LLM_RERANK=false
RETRIEVAL_K=10
RETRIEVAL_CANDIDATE_K=30
CONTEXT_EXPANSION_WINDOW=1
MAX_FINAL_CONTEXT_CHUNKS=10
```

GitHub 远程遍历相关配置：

```env
GITHUB_API_TIMEOUT_SECONDS=30
GITHUB_TOKEN=
MAX_CONCURRENT_ONLINE_CHATS=1
```

说明：

- `GITHUB_API_TIMEOUT_SECONDS`：访问 GitHub API 的单次请求超时时间。
- `GITHUB_TOKEN`：可选，只放在后端 `.env` 中，用于提高 GitHub API 限额；不要写进前端代码。
- `MAX_CONCURRENT_ONLINE_CHATS`：同时执行的联网问答数量；默认 1，避免多个大仓库同时占用过多内存和 GitHub 请求配额。
- 当前导入路径优先读取 GitHub API；如果匿名 API 被 403 限流，会自动回退到 GitHub 网页目录浏览 + raw 文件读取。
- 两种路径都只读取默认分支的远程文件树和单文件内容，不执行 `git clone`，也不下载 zip/tar archive。
- 通过过滤的文本文件会写入 `repos/<repository_id>/source_snapshot/`，供项目报告和 README analyzer 使用；`.env`、私钥、凭证、二进制和超大文件不会进入快照。

说明：

- `RETRIEVAL_CANDIDATE_K` 控制第一阶段召回候选池大小，默认 30。
- `ENABLE_LOCAL_RERANK=true` 会对候选片段做本地二次排序，不额外调用模型。
- `ENABLE_LLM_RERANK=true` 会在本地 rerank 后再调用一次 DeepSeek 对证据打分，质量可能更好，但延迟和调用成本更高。
- `MAX_FINAL_CONTEXT_CHUNKS` 控制最终送入回答模型的片段数量，默认 10。

## 管理员登录与安全配置

本地个人开发可以保持以下三项为空，此时页面无需登录：

```env
ADMIN_USERNAME=
ADMIN_PASSWORD_HASH=
AUTH_SESSION_SECRET=
```

对局域网或公网提供服务前，建议启用单管理员登录。先在不会记录明文密码的交互提示中生成 scrypt 密码哈希：

```powershell
.\.venv\Scripts\python.exe -c "from getpass import getpass; from app.security.auth import hash_password; print(hash_password(getpass('Admin password: ')))"
```

生成 Session 签名密钥：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

把两个命令的输出写入服务器 `.env`：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=粘贴生成的scrypt哈希
AUTH_SESSION_SECRET=粘贴生成的随机密钥
AUTH_SESSION_TTL_SECONDS=28800
AUTH_COOKIE_SECURE=false
LOGIN_RATE_LIMIT_WINDOW_SECONDS=300
LOGIN_RATE_LIMIT_MAX_ATTEMPTS=5
```

生产 HTTPS 环境必须设置 `AUTH_COOKIE_SECURE=true` 和 `FORCE_HTTPS=true`。浏览器使用 `HttpOnly + SameSite=Strict` 签名 Cookie，写操作还需要 Session 中的 CSRF token。`APP_API_KEY` 可继续供脚本或自动化客户端通过 `X-API-Key` 使用。

### 部署威胁模型与生产基线

**本地威胁模型**：默认 `DEPLOYMENT_MODE=local` 只面向可信开发者的本机回环地址，允许不配置登录，方便调试。它不承诺抵御同一局域网内的恶意访问者，也不应直接绑定公网地址；Compose 默认只发布 `127.0.0.1:8000`。

**生产威胁模型**：假设公网请求、伪造 `Host`、口令猜测、跨站请求、恶意仓库内容和资源耗尽都会发生。生产环境使用 `DEPLOYMENT_MODE=production`，应用会在认证或传输安全配置不完整时拒绝启动。至少配置：

```env
DEPLOYMENT_MODE=production
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=生成的scrypt哈希
AUTH_SESSION_SECRET=至少32字符的随机密钥
AUTH_COOKIE_SECURE=true
ALLOWED_HOSTS=rag.example.com
PUBLIC_BASE_URL=https://rag.example.com
FORCE_HTTPS=true
# 只有 HTTPS 在可信反向代理终止时才改为 true
TLS_TERMINATED_BY_PROXY=false
```

也可以使用至少 32 字符的 `APP_API_KEY` 作为生产 API 认证。不要把 `ALLOWED_HOSTS` 设为 `*`；`PUBLIC_BASE_URL` 必须是用户实际访问的 HTTPS 地址。

反向代理必须限制请求体，避免超大 JSON 在进入应用前消耗内存。Nginx 示例：

```nginx
server {
    client_max_body_size 1m;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_pass http://127.0.0.1:8000;
}
```

仓库导入由 `MAX_REPOSITORY_DIRECTORIES`、`MAX_REPOSITORY_REQUESTS`、`REPOSITORY_IMPORT_TIMEOUT_SECONDS` 和 `MAX_CONCURRENT_IMPORTS=1` 共同限制目录数、外部请求数、总时长和并发。当前 staging 与向量更新按单进程设计，因此不要把导入并发调高；扩展到多实例时应使用共享队列和仓库级分布式锁。

登录、仓库导入和删除的成功/失败事件写入 `logs/security_audit.jsonl`。记录只包含时间、事件、结果、客户端摘要和仓库 ID 的 SHA-256 指纹，不记录原始仓库 ID、密码、Token、问题或源码。该文件应接入日志轮转与受限读取权限。

JSONL 写入锁只保护当前 Python 进程内的线程；多个 worker 或容器同时写同一文件时，记录可能交错，不能把本地文件当作可靠的多进程审计存储。多进程部署必须把审计事件汇聚到集中日志系统或其他支持并发写入的服务。

当前登录与业务限流桶、导入信号量都在单个 Python 进程内。多 worker 或多容器会各自计数，不能形成全局保护；生产横向扩展应使用 Redis 等共享限流存储，并通过共享队列统一调度导入任务。

### Chroma 临时安全豁免

`CVE-2026-45829`（`PYSEC-2026-311`，CVSS 9.3）影响 Chroma 的远端模型加载路径。当前项目只创建本地 `PersistentClient`，不启动 Chroma FastAPI `/api/v2` 服务、不使用 `HttpClient`，而且集合创建和获取都显式传入项目自有 `embedding_function`，因此公告描述的远端恶意模型入口在当前架构中不可达。

CI 只对该公告设置到 `2026-08-13` 前有效的精准豁免；到期当天自动移除豁免并恢复 audit 失败。若 Chroma 提前发布修复版本，应升级并立即移除豁免；如果项目开始暴露 Chroma 服务、使用 `HttpClient`、接受外部集合配置或不再显式传入 embedding function，也必须立即移除豁免并重新评估。上游公告：[GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c)。

## 启动

```powershell
uvicorn app.main:app --reload
```

默认地址：

```text
http://127.0.0.1:8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

聊天界面：

```text
http://127.0.0.1:8000/
```

如果你使用其他端口，例如 `8200`，则打开：

```text
http://127.0.0.1:8200/
```

## Docker 启动

准备好 `.env` 后运行：

```powershell
docker compose up --build -d
docker compose ps
```

访问 `http://127.0.0.1:8000/`。`repos` 和 `chroma_db` 使用 Docker named volumes 持久化；镜像内部以非 root 用户运行，并通过 `/health` 执行健康检查。

## 产品工作台怎么使用

页面提供两个边界明确的模式，新手不需要先阅读 API 文档：

1. 默认“联网问答”：粘贴公开 GitHub 仓库地址后直接提问。后端按安全过滤和资源预算读取有限文件，在内存中执行 BM25/关键词检索与排序；请求结束后不会留下源码快照、manifest 或 Chroma 集合。
2. 需要多轮复用、项目报告、README 或调用图时，再手动切换到“深度分析”，点击“导入并建立索引”。只有这一步会把过滤后的快照和索引写入本机。
3. 深度导入成功后，知识库列表会保存项目入口。浏览器只记住最后选择的 `repository_id`，不会保存联网仓库 URL、密码、Session、CSRF token 或 API Key。
4. 报告和 README 可以复制或下载为 Markdown 文件。
5. “删除知识库”只删除本机的分析快照、manifest 和对应 Chroma 集合，不会修改 GitHub 原仓库。浏览器 Session 删除操作必须通过 CSRF 校验。

联网模式的准确隐私边界是“不持久化源码和索引”，不是“完全不传输数据”：服务端仍需把通过过滤的源码字节临时读入请求内存；若配置了 DeepSeek，问题和最终选中的脱敏代码片段可能发送给该模型服务。需要处理私有或高度敏感代码时，应先评估 GitHub 与模型服务的数据政策，或保持 `DEEPSEEK_API_KEY` 为空使用本地回退回答。

`GET /repositories` 为旧客户端保留 `repositories: string[]`，同时新增结构化 `items`；`GET /repositories/{repository_id}` 返回项目摘要，`DELETE /repositories/{repository_id}` 执行受控删除。响应中的 `owner_id` 当前固定为 `null`，只用于保留未来 SaaS 数据结构兼容性，**不表示当前已经实现多用户或租户隔离**。

停止服务：

```powershell
docker compose down
```

如需同时删除本地 Docker 数据卷，必须明确执行 `docker compose down -v`；该命令会删除已导入仓库的分析快照和向量索引。

## 测试

当前项目测试位于 `tests/`。`pytest.ini` 已把默认测试根目录限制为 `tests/`，并排除 `repos/`、`chroma_db/`、`dist/` 和虚拟环境，因此以下两种命令都不会误扫描已导入仓库：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

V2 后端测试可单独运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v2_agents.py -q
```

## 测试接口

### 1. 健康检查

```powershell
curl.exe http://127.0.0.1:8000/health
```

返回：

```json
{
  "status": "ok"
}
```

### 2. 联网问答（默认，不建立本地索引）

```powershell
curl.exe -X POST http://127.0.0.1:8000/chat/online `
  -H "Content-Type: application/json" `
  -d '{"github_url":"https://github.com/tiangolo/fastapi","question":"这个项目怎么启动？"}'
```

响应中的 `repository_saved` 固定为 `false`，并包含本次临时读取的文件数、切分片段数、来源和 Agent 日志。该接口不会调用持久化导入、Chroma 建库或知识库目录写入；请求失败时也不会留下半成品。

### 3. 导入 GitHub 仓库（深度分析）

```powershell
curl.exe -X POST http://127.0.0.1:8000/repository/load `
  -H "Content-Type: application/json" `
  -d "{\"github_url\":\"https://github.com/tiangolo/fastapi\"}"
```

返回示例：

```json
{
  "repository_id": "tiangolo-fastapi-a1b2c3d4e5",
  "message": "repository loaded successfully",
  "files_indexed": 120,
  "chunks_indexed": 560,
  "chunks_written": 48,
  "index_cached": false,
  "changed_files_count": 8,
  "removed_files_count": 0
}
```

导入后，本地会生成：

- `repos/<repository_id>/.codebase_agent/remote_repository_manifest.json`
- `chroma_db/`
- `repos/<repository_id>/.codebase_agent/vector_index_manifest.json`

再次导入同一个仓库时，会根据文件级 chunk hash 判断索引是否变化：

- `index_cached=true`：仓库内容未变化，复用现有 Chroma 索引。
- `chunks_written`：本次实际写入 Chroma 的 chunk 数。
- `changed_files_count`：本次发生变化并重新写入的文件数。
- `removed_files_count`：本次从索引中删除的文件数。

当前导入使用 GitHub API 做远程浏览式遍历，不把仓库源码克隆或打包下载到本地。流程相当于：

```text
GitHub API 获取默认分支 -> GitHub API 递归读取文件树 -> GitHub API 读取单个受支持文件 -> 切分 chunk -> 写入 Chroma
```

`repos/<repository_id>/` 保存 `.codebase_agent` 下的索引 manifest、远程遍历元数据，以及经过安全过滤的 `source_snapshot/` 文本分析快照。它不是完整 Git 仓库，不包含 `.git` 历史、敏感文件、二进制或被过滤的超大文件。

如果 GitHub 匿名 API 被限流，系统会自动尝试：

```text
GitHub 网页目录 -> 页面内嵌文件树数据 -> raw.githubusercontent.com 读取单个受支持文件 -> 安全文本快照 -> 切分 chunk -> 写入 Chroma
```

### 4. 已导入知识库问答

将上一步返回的 `repository_id` 填入请求：

```powershell
curl.exe -X POST http://127.0.0.1:8000/chat `
  -H "Content-Type: application/json" `
  -d "{\"repository_id\":\"tiangolo-fastapi-a1b2c3d4e5\",\"question\":\"这个项目怎么启动？\"}"
```

### 5. 检索调试

用于查看某个问题实际检索到了哪些片段、扩展了哪些 query、命中的行号和符号信息：

```powershell
curl.exe -X POST http://127.0.0.1:8200/debug/retrieval `
  -H "Content-Type: application/json" `
  -d "{\"repository_id\":\"你的repository_id\",\"question\":\"这个项目的整体用途和功能是什么？\"}"
```

返回内容包括候选池数量、最终上下文数量、`rerank_score` 和可选的 `llm_rerank_score`，可用于排查片段排序是否合理。

注意：切换 `EMBEDDING_PROVIDER` 或 `EMBEDDING_MODEL_NAME` 后，需要重新调用 `/repository/load` 重建该仓库索引。

返回示例：

```json
{
  "answer": "根据检索到的内容，项目启动方式是... 来源：README.md",
  "sources": [
    {
      "file_path": "README.md",
      "chunk_index": 0
    }
  ]
}
```

### 6. 项目分析报告

```powershell
curl.exe http://127.0.0.1:8200/repository/report/你的repository_id
```

返回内容包括：

- Project Overview
- Technology Stack
- Startup Guide
- Directory Structure
- Core Modules
- API Analysis
- Database Analysis
- Agent 执行日志

### 7. README 自动生成

```powershell
curl.exe -X POST http://127.0.0.1:8200/repository/generate-readme `
  -H "Content-Type: application/json" `
  -d "{\"repository_id\":\"你的repository_id\"}"
```

返回 Markdown 字符串，可直接作为项目 README 初稿。

## V2 Agent 架构

当前 V2 使用 LangGraph `StateGraph` 编排：

```text
SupervisorAgent -> RepositoryAgent -> TechStackAgent -> WriterAgent
```

Agent 职责：

- `SupervisorAgent`：判断任务类型并规划流程。
- `RepositoryAgent`：扫描仓库，统计文件、目录、语言、入口文件、核心模块和目录树。
- `TechStackAgent`：分析 `requirements.txt`、`package.json`、`Dockerfile`、`docker-compose.yml`、`pyproject.toml` 等文件，识别技术栈。
- `RAGAgent`：复用现有 RAG 检索、候选召回和 reranker。
- `WriterAgent`：生成报告、README 和问答最终文本。

报告和 README 生成会使用 manifest 缓存：

```text
repos/<repository_id>/.codebase_agent/repository_manifest.json
```

仓库未变化时会优先读取缓存，返回日志中会包含：

```json
{
  "agent": "ManifestCache",
  "action": "Loaded repository_manifest.json",
  "duration_ms": 30.07,
  "cached": true
}
```

## 文件读取规则

读取以下文件类型：

- 代码：`.py`、`.js`、`.jsx`、`.mjs`、`.cjs`、`.ts`、`.tsx`、`.java`、`.go`、`.rs`、`.c`、`.cc`、`.cpp`、`.cxx`、`.h`、`.hpp`、`.cs`、`.php`、`.rb`、`.kt`、`.kts`、`.swift`、`.vue`、`.svelte`
- 前端和脚本：`.html`、`.htm`、`.css`、`.scss`、`.sass`、`.less`、`.sh`、`.bash`、`.zsh`、`.ps1`、`.bat`、`.cmd`
- 文档：`.md`、`.mdx`、`.rst`、`.adoc`、`.txt`、`.pdf`、`.docx`
- 表格：`.csv`、`.tsv`、`.xlsx`
- 配置和数据：`.json`、`.jsonl`、`.yaml`、`.yml`、`.toml`、`.ini`、`.cfg`、`.conf`、`.properties`、`.xml`、`.sql`、`.graphql`、`.gql`、`.proto`、`.gradle`
- 特殊文件名：`Dockerfile`、`Containerfile`、`Makefile`、`CMakeLists.txt`、`requirements.txt`、`Pipfile`、`Gemfile`、`Rakefile`、`Cargo.toml`、`go.mod`、`go.sum`、`package.json`、`pyproject.toml`、`setup.py`

读取编码：

- 优先读取 `UTF-8` 和 `UTF-8 with BOM`
- 兼容 `GB18030`、`CP936`、`CP1252`、`Latin-1`
- 含明显二进制内容的文件会跳过

说明：

- `.pdf` 依赖 `pypdf`
- `.docx` 依赖 `python-docx`
- `.xlsx` 依赖 `openpyxl`
- 如果缺少对应依赖，相关文件会跳过，不会影响其他文件入库

忽略以下目录：

- `.git`
- `node_modules`
- `.venv`
- `venv`
- `dist`
- `build`
- `__pycache__`
- `.cache`
- `.pytest_cache`
- `.mypy_cache`
- `.ruff_cache`
- `.next`
- `.nuxt`
- `coverage`
- `target`
- `vendor`

忽略以下文件：

- `.env`
- `*.png`
- `*.jpg`
- `*.jpeg`
- `*.gif`
- `*.webp`
- `*.ico`
- `*.svg`
- `*.mp4`
- `*.mov`
- `*.avi`
- `*.mkv`
- `*.mp3`
- `*.wav`
- `*.zip`
- `*.tar`
- `*.gz`
- `*.rar`
- `*.7z`
- `*.exe`
- `*.dll`
- `*.so`
- `*.dylib`
- `*.class`
- `*.jar`
- `*.pyc`
- `package-lock.json`
- `yarn.lock`

## 常见问题

### DeepSeek API Key 未配置

`/repository/load` 不需要 DeepSeek API Key。`/chat` 在未配置 API Key 时不会再直接报错；如果检索到了相关片段，会返回基于真实来源的保守依据摘要。

示例：

```json
{
  "answer": "已从仓库中找到相关依据，但当前未配置 DeepSeek API Key，因此无法调用模型生成完整自然语言回答...",
  "sources": [
    {
      "file_path": "README.md",
      "chunk_id": 0
    }
  ],
  "logs": []
}
```

如果没有检索到相关片段，则返回：

```json
{
  "answer": "无法从代码库中找到可靠依据。",
  "sources": [],
  "logs": []
}
```

### 第一次导入仓库很慢

导入分两段耗时：

- 远程遍历：通过 GitHub API 读取默认分支文件树和受支持的单文件内容。
- 建立索引：会读取文件、切分代码、生成 embedding、写入 Chroma；仓库越大，耗时越长。

可优化方向：

- 设置 `GITHUB_TOKEN` 提高 GitHub API 限额。
- 如果没有 token，公开仓库会自动尝试 GitHub 网页浏览 fallback。
- 降低 `MAX_REPOSITORY_FILES` 或 `MAX_REPOSITORY_BYTES`，限制超大仓库读取规模。
- 已导入仓库再次导入时会复用文件级索引 manifest，未变化文件不会重复写入 Chroma。

### 重新导入同一个仓库

同一个 GitHub URL 会生成稳定的 `repository_id`。系统会重新远程遍历 GitHub 默认分支，并基于文件级 manifest 判断哪些文件需要重新写入 Chroma。

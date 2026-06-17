# GitHub Code RAG

最小可运行版 GitHub 单仓库代码 RAG 问答后端。

功能：

- 输入 GitHub 仓库 URL 后自动克隆到 `repos/`
- 读取常见代码和文档文件
- 过滤依赖目录、构建产物、锁文件、二进制文件和 `.env`
- 使用 LangChain 切分文本
- 使用 ChromaDB 保存向量索引到 `chroma_db/`
- 使用本地 `sentence-transformers` 生成 embedding
- 使用 DeepSeek Chat API 基于检索片段回答问题

## 项目结构

```text
github-code-rag/
├─ app/
│  ├─ main.py
│  ├─ config.py
│  ├─ schemas.py
│  ├─ services/
│  │  ├─ repo_loader.py
│  │  ├─ file_parser.py
│  │  ├─ vector_store.py
│  │  └─ llm_service.py
│  └─ utils/
│     └─ file_utils.py
├─ repos/
├─ chroma_db/
├─ requirements.txt
├─ .env.example
└─ README.md
```

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

第一次安装 `sentence-transformers` 相关依赖可能较慢。

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

第一版默认使用本地 embedding 模型：

```env
EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
```

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

### 2. 导入 GitHub 仓库

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
  "chunks_indexed": 560
}
```

导入后，本地会生成：

- `repos/<repository_id>/`
- `chroma_db/`

### 3. 仓库问答

将上一步返回的 `repository_id` 填入请求：

```powershell
curl.exe -X POST http://127.0.0.1:8000/chat `
  -H "Content-Type: application/json" `
  -d "{\"repository_id\":\"tiangolo-fastapi-a1b2c3d4e5\",\"question\":\"这个项目怎么启动？\"}"
```

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

## 文件读取规则

读取以下文件类型：

- `.py`
- `.js`
- `.ts`
- `.tsx`
- `.java`
- `.go`
- `.md`
- `.json`
- `.yaml`
- `.yml`
- `.txt`

忽略以下目录：

- `.git`
- `node_modules`
- `.venv`
- `venv`
- `dist`
- `build`
- `__pycache__`
- `.cache`

忽略以下文件：

- `.env`
- `*.png`
- `*.jpg`
- `*.jpeg`
- `*.gif`
- `*.mp4`
- `*.zip`
- `package-lock.json`
- `yarn.lock`

## 常见问题

### DeepSeek API Key 未配置

`/repository/load` 不需要 DeepSeek API Key，但 `/chat` 需要。

如果没有配置，`/chat` 会返回：

```json
{
  "detail": "DEEPSEEK_API_KEY is not configured"
}
```

### 第一次导入仓库很慢

第一次运行会下载本地 embedding 模型，并对仓库文件生成向量。仓库越大，耗时越长。

### 重新导入同一个仓库

同一个 GitHub URL 会生成稳定的 `repository_id`。重新导入时会删除旧的本地仓库目录，并重建对应 Chroma collection。

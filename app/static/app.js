const repositorySelect = document.querySelector("#repositorySelect");
const refreshReposButton = document.querySelector("#refreshReposButton");
const githubUrlInput = document.querySelector("#githubUrl");
const loadRepoButton = document.querySelector("#loadRepoButton");
const workspaceModeButtons = document.querySelectorAll("[data-workspace-mode]");
const deepOnlyElements = document.querySelectorAll("[data-deep-only]");
const modePrivacyNote = document.querySelector("#modePrivacyNote");
const githubModeHint = document.querySelector("#githubModeHint");
const storageModeText = document.querySelector("#storageModeText");
const composerHint = document.querySelector("#composerHint");
const sourceTabButtons = document.querySelectorAll("[data-source-tab]");
const githubImportPanel = document.querySelector("#githubImportPanel");
const zipImportPanel = document.querySelector("#zipImportPanel");
const zipFileInput = document.querySelector("#zipFileInput");
const zipDropzone = document.querySelector("#zipDropzone");
const zipFileName = document.querySelector("#zipFileName");
const uploadZipButton = document.querySelector("#uploadZipButton");
const importProgress = document.querySelector("#importProgress");
const loadStatus = document.querySelector("#loadStatus");
const activeRepoText = document.querySelector("#activeRepoText");
const messages = document.querySelector("#messages");
const chatForm = document.querySelector("#chatForm");
const questionInput = document.querySelector("#questionInput");
const sendButton = document.querySelector("#sendButton");
const clearChatButton = document.querySelector("#clearChatButton");
const reportButton = document.querySelector("#reportButton");
const readmeButton = document.querySelector("#readmeButton");
const quickPromptButtons = document.querySelectorAll(".quick-prompts button");
const artifactPanel = document.querySelector("#artifactPanel");
const artifactTitle = document.querySelector("#artifactTitle");
const artifactMeta = document.querySelector("#artifactMeta");
const artifactBody = document.querySelector("#artifactBody");
const artifactLogs = document.querySelector("#artifactLogs");
const closeArtifactButton = document.querySelector("#closeArtifactButton");
const loginOverlay = document.querySelector("#loginOverlay");
const loginForm = document.querySelector("#loginForm");
const loginUsername = document.querySelector("#loginUsername");
const loginPassword = document.querySelector("#loginPassword");
const loginButton = document.querySelector("#loginButton");
const loginStatus = document.querySelector("#loginStatus");
const logoutButton = document.querySelector("#logoutButton");
const projectSummary = document.querySelector("#projectSummary");
const deleteProjectButton = document.querySelector("#deleteProjectButton");
const deleteProjectDialog = document.querySelector("#deleteProjectDialog");
const deleteProjectName = document.querySelector("#deleteProjectName");
const confirmDeleteButton = document.querySelector("#confirmDeleteButton");
const copyArtifactButton = document.querySelector("#copyArtifactButton");
const downloadArtifactButton = document.querySelector("#downloadArtifactButton");
const sidebarToggle = document.querySelector("#sidebarToggle");
const sidebarCloseButton = document.querySelector("#sidebarCloseButton");
const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
let csrfToken = "";
let authEnabled = false;
const state = {
  repositories: [],
  activeRepositoryId: localStorage.getItem("activeRepositoryId") || "",
  isLoading: false,
  artifact: null,
  activeSource: "github",
  zipFile: null,
  workspaceMode: "online",
  onlineGithubUrl: "",
};

function getWelcomeMessage() {
  if (state.workspaceMode === "online") {
    return (
      "输入 GitHub 仓库地址后即可联网提问。\n" +
      "源码只在本次请求的内存中处理，不会创建本地快照或 Chroma 索引。"
    );
  }
  return (
    "选择或导入知识库后，你可以连续询问架构、调用链、入口文件和安全风险。\n" +
    "深度分析会保存经过过滤的本地快照与检索索引。"
  );
}

function getApiHeaders(extraHeaders = {}, includeCsrf = false) {
  const headers = { ...extraHeaders };
  if (includeCsrf && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  return headers;
}

function friendlyError(status, detail = "") {
  if (status === 401) {
    showLogin("登录已过期，请重新登录。");
    return "登录已过期，请重新登录。";
  }
  if (status === 403) {
    return "安全校验失败，请刷新页面后重试。";
  }
  if (status === 429) {
    return "请求过于频繁，请稍等一分钟后重试。";
  }
  if (status === 413) {
    return detail || "ZIP 文件超过允许大小，请精简后重试。";
  }
  if (status === 400) {
    return detail || "输入或文件不符合要求，请检查后重试。";
  }
  if (status >= 500) {
    return "服务暂时无法完成操作，请检查后端配置或稍后重试。";
  }
  return detail || "操作失败，请重试。";
}

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (_error) {
    throw new Error("无法连接后端，请确认服务已经启动。");
  }
  let data = {};
  try {
    data = await response.json();
  } catch (_error) {
    data = {};
  }
  if (!response.ok) {
    throw new Error(friendlyError(response.status, data.detail));
  }
  return data;
}

function showLogin(message = "") {
  loginStatus.textContent = message;
  loginOverlay.classList.remove("is-hidden");
  logoutButton.classList.add("is-hidden");
  loginPassword.value = "";
  loginUsername.focus();
}

function hideLogin() {
  loginOverlay.classList.add("is-hidden");
  logoutButton.classList.toggle("is-hidden", !authEnabled);
}

async function initializeAuth() {
  const data = await requestJson("/auth/status");
  authEnabled = data.enabled === true;
  csrfToken = data.csrf_token || "";
  if (authEnabled && !data.authenticated) {
    showLogin();
    return;
  }
  hideLogin();
  await refreshRepositories();
}

async function login(event) {
  event.preventDefault();
  loginButton.disabled = true;
  loginStatus.textContent = "正在验证...";
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUsername.value.trim(), password: loginPassword.value }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "登录失败");
    }
    authEnabled = true;
    csrfToken = data.csrf_token || "";
    hideLogin();
    await refreshRepositories();
  } catch (error) {
    showLogin(error.message);
  } finally {
    loginButton.disabled = false;
  }
}

async function logout() {
  await requestJson("/auth/logout", {
    method: "POST",
    headers: getApiHeaders({}, true),
  });
  csrfToken = "";
  resetMessages();
  showLogin("已安全退出。");
}

function getActiveRepositoryId() {
  return state.activeRepositoryId || repositorySelect.value;
}

function setStatus(text, isError = false) {
  loadStatus.textContent = text;
  loadStatus.classList.toggle("error", isError);
  importProgress.classList.toggle("is-error", isError);
}

function normalizeGitHubRepositoryUrl(rawValue) {
  let parsed;
  try {
    parsed = new URL(rawValue.trim());
  } catch (_error) {
    throw new Error("请输入完整的 GitHub 仓库地址。");
  }
  const pathParts = parsed.pathname.split("/").filter(Boolean);
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname.toLowerCase() !== "github.com" ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    pathParts.length !== 2
  ) {
    throw new Error("地址必须是 https://github.com/owner/repo 格式。");
  }
  const owner = pathParts[0];
  const repository = pathParts[1].replace(/\.git$/i, "");
  if (!owner || !repository || !/^[A-Za-z0-9_.-]+$/.test(owner) || !/^[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new Error("GitHub 用户名或仓库名包含不支持的字符。");
  }
  return `https://github.com/${owner}/${repository}`;
}

function setWorkspaceMode(mode, { resetChat = true } = {}) {
  if (!['online', 'deep'].includes(mode)) {
    return;
  }
  state.workspaceMode = mode;
  workspaceModeButtons.forEach((button) => {
    const isActive = button.dataset.workspaceMode === mode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  deepOnlyElements.forEach((element) => {
    element.hidden = mode !== "deep";
  });

  if (mode === "online") {
    setSourceMode("github");
    loadRepoButton.textContent = "使用此仓库联网问答";
    modePrivacyNote.textContent =
      "请求期间临时读取 GitHub 内容；不会创建仓库快照、manifest 或 Chroma 索引。";
    githubModeHint.textContent =
      "为生成回答，必要片段可能发送给已配置的模型服务；快速模式不等于完整代码审计。";
    storageModeText.textContent = "联网临时读取 · 不持久化源码";
    composerHint.textContent = "联网模式 · Enter 发送，Shift + Enter 换行";
    questionInput.placeholder = "向 GitHub 仓库临时提问…";
  } else {
    loadRepoButton.textContent = "导入并建立索引";
    modePrivacyNote.textContent =
      "深度分析会在本机保存过滤后的快照、manifest 和 Chroma 索引，可通过删除知识库清除。";
    githubModeHint.textContent =
      "导入后可以连续问答，并生成项目报告、README 和静态分析结果。";
    storageModeText.textContent = "本地索引 · 可重复使用";
    composerHint.textContent = "深度分析模式 · Enter 发送，Shift + Enter 换行";
    questionInput.placeholder = "向当前知识库提问…";
    setSourceMode(state.activeSource === "zip" ? "zip" : "github");
  }

  closeArtifact();
  updateActiveRepoText();
  if (resetChat) {
    resetMessages();
  }
}

function setSourceMode(mode) {
  if (!["github", "zip"].includes(mode)) {
    return;
  }
  if (mode === "zip" && state.workspaceMode !== "deep") {
    return;
  }
  state.activeSource = mode;
  sourceTabButtons.forEach((button) => {
    const isActive = button.dataset.sourceTab === mode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  });
  githubImportPanel.hidden = mode !== "github";
  zipImportPanel.hidden = mode !== "zip";
  setStatus(
    mode === "github" && state.workspaceMode === "online"
      ? "联网问答不会保存源码、manifest 或向量索引"
      : mode === "github"
        ? "深度分析会保存经过过滤的本地快照与索引"
        : "ZIP 会在本机解析，并自动跳过密钥与不支持的文件"
  );
}

function setSidebarOpen(isOpen) {
  document.body.classList.toggle("sidebar-open", isOpen);
  sidebarToggle.setAttribute("aria-expanded", String(isOpen));
  sidebarToggle.setAttribute("aria-label", isOpen ? "关闭知识源面板" : "打开知识源面板");
  if (isOpen) {
    window.setTimeout(() => {
      const activeTab = document.querySelector("[data-source-tab].is-active");
      activeTab?.focus();
    }, 0);
  } else {
    sidebarToggle.focus();
  }
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 1024) {
    return `${Math.max(bytes || 0, 0)} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function selectZipFile(file) {
  if (!file) {
    state.zipFile = null;
    zipFileName.textContent = "尚未选择文件";
    uploadZipButton.disabled = true;
    return;
  }
  if (!file.name.toLowerCase().endsWith(".zip")) {
    state.zipFile = null;
    zipFileName.textContent = "尚未选择文件";
    uploadZipButton.disabled = true;
    setStatus("请选择扩展名为 .zip 的文件。", true);
    return;
  }
  state.zipFile = file;
  zipFileName.textContent = `${file.name} · ${formatFileSize(file.size)}`;
  uploadZipButton.disabled = state.isLoading;
  setStatus("文件已选择；导入时会检查路径、大小、压缩比和敏感文件。");
}

function updateActiveRepoText() {
  if (state.workspaceMode === "online") {
    let onlineLabel = "等待输入 GitHub 仓库地址";
    if (state.onlineGithubUrl) {
      const parsed = new URL(state.onlineGithubUrl);
      onlineLabel = `联网模式：${parsed.pathname.replace(/^\//, "")} · 不保存`;
    }
    activeRepoText.textContent = onlineLabel;
    reportButton.disabled = true;
    readmeButton.disabled = true;
    deleteProjectButton.disabled = true;
    return;
  }

  const repositoryId = getActiveRepositoryId();
  activeRepoText.textContent = repositoryId
    ? `当前知识库：${repositoryId}`
    : "请选择或导入一个知识库";
  reportButton.disabled = !repositoryId;
  readmeButton.disabled = !repositoryId;
  deleteProjectButton.disabled = !repositoryId;
  const item = state.repositories.find((repository) => repository.repository_id === repositoryId);
  if (!item) {
    projectSummary.innerHTML = '<p class="empty-summary">导入后将在这里显示索引统计。</p>';
    return;
  }
  projectSummary.innerHTML = "";
  const status = document.createElement("div");
  status.className = "project-status";
  status.classList.toggle("is-incomplete", item.status !== "ready");
  const indicator = document.createElement("span");
  indicator.className = "status-indicator";
  const statusText = document.createElement("strong");
  statusText.textContent = item.status === "ready" ? "已就绪" : "索引不完整";
  status.append(indicator, statusText);
  const metrics = document.createElement("dl");
  const isZipSource = item.source_type === "zip_upload";
  const isGitHubSource = item.source_type === "github" || Boolean(item.github_url || item.default_branch);
  const sourceLabel = isGitHubSource ? "分支" : "来源";
  const sourceValue = isGitHubSource
    ? item.default_branch || "未知"
    : isZipSource
      ? "本地 ZIP"
      : "未知";
  for (const [label, value] of [
    ["文件", item.files_indexed],
    ["代码片段", item.chunks_indexed],
    [sourceLabel, sourceValue],
  ]) {
    const metric = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = String(value);
    metric.append(term, description);
    metrics.appendChild(metric);
  }
  projectSummary.append(status, metrics);
}

function appendMessage(role, content, sources = [], options = {}) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "你" : "AI";

  const stack = document.createElement("div");
  stack.className = "message-stack";

  const name = document.createElement("div");
  name.className = "message-name";
  name.textContent = role === "user" ? "你" : "代码助手";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (options.typing) {
    const typing = document.createElement("div");
    typing.className = "typing";
    typing.setAttribute("aria-label", content);
    for (let index = 0; index < 3; index += 1) {
      typing.appendChild(document.createElement("span"));
    }
    bubble.appendChild(typing);
  } else {
    bubble.appendChild(renderMessageContent(content));
  }

  if (sources.length > 0) {
    const sourceBox = document.createElement("div");
    sourceBox.className = "sources";
    for (const source of sources) {
      const chip = document.createElement("span");
      chip.className = "source-chip";
      const lineRange =
        source.start_line && source.end_line ? `:${source.start_line}-${source.end_line}` : "";
      const chunkId = source.chunk_id ?? source.chunk_index;
      chip.textContent = `${source.file_path}${lineRange} #${chunkId}`;
      sourceBox.appendChild(chip);
    }
    bubble.appendChild(sourceBox);
  }

  if (options.logs && options.logs.length > 0) {
    bubble.appendChild(renderAgentLogs(options.logs));
  }

  stack.appendChild(name);
  stack.appendChild(bubble);
  article.appendChild(avatar);
  article.appendChild(stack);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function renderAgentLogs(logs) {
  const details = document.createElement("details");
  details.className = "agent-logs";
  const summary = document.createElement("summary");
  summary.textContent = `Agent 执行日志 · ${logs.length} 步`;
  details.appendChild(summary);

  const list = document.createElement("div");
  list.className = "agent-log-list";
  for (const log of logs) {
    const row = document.createElement("div");
    row.className = "agent-log-row";

    const main = document.createElement("span");
    main.textContent = `${log.agent}: ${log.action}`;

    const meta = document.createElement("span");
    meta.className = "agent-log-meta";
    const duration = typeof log.duration_ms === "number" ? `${log.duration_ms.toFixed(2)}ms` : "";
    const cached = log.cached === true ? "cached" : log.cached === false ? "fresh" : "";
    meta.textContent = [duration, cached].filter(Boolean).join(" · ");

    row.appendChild(main);
    row.appendChild(meta);
    list.appendChild(row);
  }
  details.appendChild(list);
  return details;
}

function renderInlineText(text) {
  const fragment = document.createDocumentFragment();
  const parts = text.split(/(`[^`]+`)/g);

  for (const part of parts) {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      const code = document.createElement("code");
      code.textContent = part.slice(1, -1);
      fragment.appendChild(code);
    } else if (part) {
      fragment.appendChild(document.createTextNode(part));
    }
  }

  return fragment;
}

function renderMessageContent(content) {
  const wrapper = document.createElement("div");
  wrapper.className = "answer-content";
  const lines = content.split("\n");
  let list = null;
  let codeBlock = null;
  let inCodeBlock = false;

  function closeList() {
    if (list) {
      wrapper.appendChild(list);
      list = null;
    }
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      closeList();
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeBlock = document.createElement("code");
        const pre = document.createElement("pre");
        pre.appendChild(codeBlock);
        wrapper.appendChild(pre);
      } else {
        inCodeBlock = false;
        codeBlock = null;
      }
      continue;
    }

    if (inCodeBlock && codeBlock) {
      codeBlock.textContent += `${line}\n`;
      continue;
    }

    const bulletMatch = line.match(/^\s*[-*]\s+(.+)$/);
    if (bulletMatch) {
      if (!list) {
        list = document.createElement("ul");
      }
      const item = document.createElement("li");
      item.appendChild(renderInlineText(bulletMatch[1]));
      list.appendChild(item);
      continue;
    }

    closeList();

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = String(Math.min(headingMatch[1].length + 2, 4));
      const heading = document.createElement(`h${level}`);
      heading.appendChild(renderInlineText(headingMatch[2]));
      wrapper.appendChild(heading);
      continue;
    }

    if (!line.trim()) {
      continue;
    }

    const paragraph = document.createElement("p");
    paragraph.appendChild(renderInlineText(line));
    wrapper.appendChild(paragraph);
  }

  closeList();
  return wrapper;
}

function openArtifact({ title, meta, markdown, logs = [] }) {
  state.artifact = { title, meta, markdown };
  artifactTitle.textContent = title;
  artifactMeta.textContent = meta;
  artifactBody.innerHTML = "";
  artifactBody.appendChild(renderMessageContent(markdown));
  artifactLogs.innerHTML = "";

  if (logs.length > 0) {
    artifactLogs.appendChild(renderAgentLogs(logs));
  }

  artifactPanel.classList.remove("is-hidden");
}

function closeArtifact() {
  state.artifact = null;
  artifactPanel.classList.add("is-hidden");
}

function resetMessages() {
  messages.innerHTML = "";
  appendMessage("assistant", getWelcomeMessage());
}

function requireRepository() {
  if (state.workspaceMode !== "deep") {
    appendMessage("assistant", "项目报告和 README 需要先切换到深度分析并建立本地索引。");
    return "";
  }
  const repositoryId = getActiveRepositoryId();
  if (!repositoryId) {
    appendMessage("assistant", "请先选择或导入一个知识库。");
    return "";
  }
  return repositoryId;
}

async function refreshRepositories(selectedId = "") {
  const data = await requestJson("/repositories", { headers: getApiHeaders() });
  state.repositories = data.items || (data.repositories || []).map((repositoryId) => ({
    repository_id: repositoryId,
    status: "ready",
    files_indexed: 0,
    chunks_indexed: 0,
    default_branch: null,
  }));
  repositorySelect.innerHTML = "";

  if (state.repositories.length === 0) {
    state.activeRepositoryId = "";
    localStorage.removeItem("activeRepositoryId");
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无知识库";
    repositorySelect.appendChild(option);
  } else {
    for (const repository of state.repositories) {
      const option = document.createElement("option");
      option.value = repository.repository_id;
      option.textContent = repository.repository_id;
      repositorySelect.appendChild(option);
    }
    const requestedId = selectedId || state.activeRepositoryId;
    const exists = state.repositories.some((repository) => repository.repository_id === requestedId);
    state.activeRepositoryId = exists ? requestedId : state.repositories[0].repository_id;
    repositorySelect.value = state.activeRepositoryId;
    if (state.workspaceMode === "deep") {
      localStorage.setItem("activeRepositoryId", state.activeRepositoryId);
    }
  }

  updateActiveRepoText();
}

function prepareOnlineRepository() {
  let githubUrl;
  try {
    githubUrl = normalizeGitHubRepositoryUrl(githubUrlInput.value);
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  state.onlineGithubUrl = githubUrl;
  githubUrlInput.value = githubUrl;
  closeArtifact();
  resetMessages();
  updateActiveRepoText();
  setStatus("联网仓库已就绪；发送问题后会临时读取并在回答完成后释放源码内容。");
  appendMessage(
    "assistant",
    `已选择联网仓库：${new URL(githubUrl).pathname.replace(/^\//, "")}\n现在可以直接提问，不会建立本地知识库。`
  );
  if (window.matchMedia("(max-width: 820px)").matches) {
    setSidebarOpen(false);
  }
}

async function loadRepository() {
  if (state.workspaceMode !== "deep") {
    prepareOnlineRepository();
    return;
  }
  const githubUrl = githubUrlInput.value.trim();
  if (!githubUrl) {
    setStatus("请先输入 GitHub 仓库 URL。", true);
    return;
  }

  state.isLoading = true;
  loadRepoButton.disabled = true;
  loadRepoButton.textContent = "分析中…";
  uploadZipButton.disabled = true;
  setStatus("正在读取仓库文件、过滤敏感内容并建立代码索引…");

  try {
    const data = await requestJson("/repository/load", {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }, true),
      body: JSON.stringify({ github_url: githubUrl }),
    });

    const indexStatus = data.index_cached
      ? "索引未变化，已复用缓存"
      : `写入 ${data.chunks_written ?? data.chunks_indexed} 个片段，变化文件 ${data.changed_files_count ?? data.files_indexed} 个`;
    setStatus(`导入成功：${data.files_indexed} 个文件，${data.chunks_indexed} 个片段。${indexStatus}。`);
    await refreshRepositories(data.repository_id);
    appendMessage("assistant", `GitHub 知识库已导入：${data.repository_id}\n现在可以开始提问。`);
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.isLoading = false;
    loadRepoButton.disabled = false;
    loadRepoButton.textContent = "导入并建立索引";
    uploadZipButton.disabled = !state.zipFile;
  }
}

async function uploadZipArchive() {
  if (state.workspaceMode !== "deep") {
    setStatus("ZIP 导入需要切换到深度分析模式。", true);
    return;
  }
  const file = state.zipFile;
  if (!file) {
    setStatus("请先选择一个 ZIP 文件。", true);
    return;
  }

  state.isLoading = true;
  loadRepoButton.disabled = true;
  uploadZipButton.disabled = true;
  uploadZipButton.textContent = "安全检查与解析中…";
  setStatus("正在检查压缩包路径与大小，并在本机建立检索索引…");

  try {
    const data = await requestJson("/repository/upload-zip", {
      method: "POST",
      headers: getApiHeaders(
        {
          "Content-Type": "application/zip",
          "X-Archive-Name": encodeURIComponent(file.name),
        },
        true
      ),
      body: file,
    });
    const indexStatus = data.index_cached
      ? "索引未变化，已复用缓存"
      : `写入 ${data.chunks_written ?? data.chunks_indexed} 个片段，变化文件 ${data.changed_files_count ?? data.files_indexed} 个`;
    setStatus(
      `导入成功：${data.files_indexed} 个文件，${data.chunks_indexed} 个片段。${indexStatus}。`
    );
    await refreshRepositories(data.repository_id);
    appendMessage("assistant", `本地 ZIP 知识库已导入：${data.repository_id}\n现在可以开始提问。`);
    selectZipFile(null);
    zipFileInput.value = "";
    if (window.matchMedia("(max-width: 820px)").matches) {
      setSidebarOpen(false);
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.isLoading = false;
    loadRepoButton.disabled = false;
    uploadZipButton.disabled = !state.zipFile;
    uploadZipButton.textContent = "导入并解析";
  }
}

async function sendQuestion(event) {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) {
    return;
  }

  const isOnline = state.workspaceMode === "online";
  let endpoint = "/chat";
  let payload;
  if (isOnline) {
    try {
      state.onlineGithubUrl = normalizeGitHubRepositoryUrl(
        githubUrlInput.value.trim() || state.onlineGithubUrl
      );
    } catch (error) {
      setStatus(error.message, true);
      appendMessage("assistant", error.message);
      githubUrlInput.focus();
      return;
    }
    githubUrlInput.value = state.onlineGithubUrl;
    endpoint = "/chat/online";
    payload = { github_url: state.onlineGithubUrl, question };
    updateActiveRepoText();
    setStatus("正在临时读取 GitHub 内容；本次请求不会建立本地知识库…");
  } else {
    const repositoryId = getActiveRepositoryId();
    if (!repositoryId) {
      appendMessage("assistant", "请先选择或导入一个知识库。");
      return;
    }
    payload = { repository_id: repositoryId, question };
  }

  appendMessage("user", question);
  questionInput.value = "";
  questionInput.style.height = "auto";
  sendButton.disabled = true;
  const thinking = appendMessage(
    "assistant",
    isOnline ? "正在联网读取并临时检索证据..." : "正在检索代码片段并调用模型...",
    [],
    { typing: true }
  );

  try {
    const data = await requestJson(endpoint, {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }, true),
      body: JSON.stringify(payload),
    });
    thinking.remove();
    appendMessage("assistant", data.answer, data.sources, { logs: data.logs || [] });
    if (isOnline) {
      setStatus(
        `联网问答完成：临时读取 ${data.files_scanned ?? 0} 个文件、${data.chunks_scanned ?? 0} 个片段；未保存源码或索引。`
      );
    }
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `出错了：${error.message}`);
    if (isOnline) {
      setStatus(error.message, true);
    }
  } finally {
    sendButton.disabled = false;
    questionInput.focus();
  }
}

async function generateProjectReport() {
  const repositoryId = requireRepository();
  if (!repositoryId) {
    return;
  }

  reportButton.disabled = true;
  const thinking = appendMessage("assistant", "RepositoryAgent 正在生成项目分析报告...", [], { typing: true });

  try {
    const data = await requestJson(`/repository/report/${encodeURIComponent(repositoryId)}`, {
      headers: getApiHeaders(),
    });
    thinking.remove();
    openArtifact({
      title: "项目分析报告",
      meta: `Repository ID: ${repositoryId}`,
      markdown: data.markdown,
      logs: data.logs || [],
    });
    appendMessage("assistant", "项目分析报告已生成，已在文档视图中打开。");
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `出错了：${error.message}`);
  } finally {
    reportButton.disabled = false;
  }
}

async function generateReadme() {
  const repositoryId = requireRepository();
  if (!repositoryId) {
    return;
  }

  readmeButton.disabled = true;
  const thinking = appendMessage("assistant", "WriterAgent 正在生成 README...", [], { typing: true });

  try {
    const data = await requestJson("/repository/generate-readme", {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }, true),
      body: JSON.stringify({ repository_id: repositoryId }),
    });
    thinking.remove();
    openArtifact({
      title: "README 草稿",
      meta: `Repository ID: ${repositoryId}`,
      markdown: data.markdown,
      logs: data.logs || [],
    });
    appendMessage("assistant", "README 草稿已生成，已在文档视图中打开。");
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `出错了：${error.message}`);
  } finally {
    readmeButton.disabled = false;
  }
}

function selectRepository() {
  if (state.workspaceMode !== "deep") {
    setWorkspaceMode("deep", { resetChat: false });
  }
  state.activeRepositoryId = repositorySelect.value;
  if (state.activeRepositoryId) {
    localStorage.setItem("activeRepositoryId", state.activeRepositoryId);
  } else {
    localStorage.removeItem("activeRepositoryId");
  }
  closeArtifact();
  resetMessages();
  updateActiveRepoText();
}

function openDeleteDialog() {
  if (state.workspaceMode !== "deep") {
    return;
  }
  const repositoryId = getActiveRepositoryId();
  if (!repositoryId) {
    return;
  }
  deleteProjectName.textContent = repositoryId;
  deleteProjectDialog.showModal();
}

async function deleteActiveRepository() {
  const repositoryId = getActiveRepositoryId();
  if (!repositoryId) {
    return;
  }
  confirmDeleteButton.disabled = true;
  confirmDeleteButton.textContent = "删除中…";
  try {
    await requestJson(`/repositories/${encodeURIComponent(repositoryId)}`, {
      method: "DELETE",
      headers: getApiHeaders({}, true),
    });
    deleteProjectDialog.close();
    state.activeRepositoryId = "";
    localStorage.removeItem("activeRepositoryId");
    closeArtifact();
    resetMessages();
    setStatus(`已删除本地项目：${repositoryId}`);
    await refreshRepositories();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    confirmDeleteButton.disabled = false;
    confirmDeleteButton.textContent = "确认删除";
  }
}

async function copyArtifact() {
  if (!state.artifact) {
    return;
  }
  await navigator.clipboard.writeText(state.artifact.markdown);
  copyArtifactButton.textContent = "已复制";
  window.setTimeout(() => {
    copyArtifactButton.textContent = "复制";
  }, 1600);
}

function downloadArtifact() {
  if (!state.artifact) {
    return;
  }
  const blob = new Blob([state.artifact.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const safeTitle = state.artifact.title.replace(/[^A-Za-z0-9\u4e00-\u9fff_-]+/g, "-");
  link.href = url;
  link.download = `${safeTitle || "code-rag-artifact"}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

questionInput.addEventListener("input", () => {
  questionInput.style.height = "auto";
  questionInput.style.height = `${questionInput.scrollHeight}px`;
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

sourceTabButtons.forEach((button) => {
  button.addEventListener("click", () => setSourceMode(button.dataset.sourceTab || "github"));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const visibleButtons = Array.from(sourceTabButtons).filter((item) => !item.hidden);
    const index = visibleButtons.indexOf(button);
    if (index < 0 || visibleButtons.length === 0) {
      return;
    }
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const nextIndex = (index + direction + visibleButtons.length) % visibleButtons.length;
    const nextButton = visibleButtons[nextIndex];
    setSourceMode(nextButton.dataset.sourceTab || "github");
    nextButton.focus();
  });
});

workspaceModeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setWorkspaceMode(button.dataset.workspaceMode || "online");
  });
});

zipFileInput.addEventListener("change", () => selectZipFile(zipFileInput.files?.[0] || null));
zipDropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    zipFileInput.click();
  }
});
zipDropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (!state.isLoading) {
    zipDropzone.classList.add("is-dragging");
  }
});
zipDropzone.addEventListener("dragleave", () => zipDropzone.classList.remove("is-dragging"));
zipDropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  zipDropzone.classList.remove("is-dragging");
  if (!state.isLoading) {
    selectZipFile(event.dataTransfer?.files?.[0] || null);
  }
});

loadRepoButton.addEventListener("click", loadRepository);
uploadZipButton.addEventListener("click", uploadZipArchive);
refreshReposButton.addEventListener("click", () => refreshRepositories().catch((error) => setStatus(error.message, true)));
repositorySelect.addEventListener("change", selectRepository);
chatForm.addEventListener("submit", sendQuestion);
clearChatButton.addEventListener("click", resetMessages);
reportButton.addEventListener("click", generateProjectReport);
readmeButton.addEventListener("click", generateReadme);
closeArtifactButton.addEventListener("click", closeArtifact);
deleteProjectButton.addEventListener("click", openDeleteDialog);
confirmDeleteButton.addEventListener("click", deleteActiveRepository);
copyArtifactButton.addEventListener("click", () => copyArtifact().catch((error) => setStatus(error.message, true)));
downloadArtifactButton.addEventListener("click", downloadArtifact);
loginForm.addEventListener("submit", login);
logoutButton.addEventListener("click", () => logout().catch((error) => appendMessage("assistant", `出错了：${error.message}`)));
sidebarToggle.addEventListener("click", () =>
  setSidebarOpen(!document.body.classList.contains("sidebar-open"))
);
sidebarCloseButton.addEventListener("click", () => setSidebarOpen(false));
sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) {
    setSidebarOpen(false);
  }
});
quickPromptButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.action === "report") {
      generateProjectReport();
      return;
    }
    if (button.dataset.action === "readme") {
      generateReadme();
      return;
    }
    questionInput.value = button.dataset.question || "";
    questionInput.focus();
    questionInput.style.height = "auto";
    questionInput.style.height = `${questionInput.scrollHeight}px`;
  });
});

setWorkspaceMode("online");
initializeAuth().catch((error) => showLogin(error.message));

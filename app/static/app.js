const repositorySelect = document.querySelector("#repositorySelect");
const refreshReposButton = document.querySelector("#refreshReposButton");
const githubUrlInput = document.querySelector("#githubUrl");
const loadRepoButton = document.querySelector("#loadRepoButton");
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
let csrfToken = "";
let authEnabled = false;
const state = {
  repositories: [],
  activeRepositoryId: localStorage.getItem("activeRepositoryId") || "",
  isLoading: false,
  artifact: null,
};

const welcomeMessage =
  "先在左侧导入 GitHub 仓库，然后像聊天一样直接提问。我会基于检索到的代码片段回答，并列出来源文件。";

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
  if (status === 400) {
    return detail || "输入不符合要求，请检查仓库地址。";
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
  const response = await fetch("/auth/logout", {
    method: "POST",
    headers: getApiHeaders({}, true),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.detail || "退出失败");
  }
  csrfToken = "";
  resetMessages();
  showLogin("已安全退出。" );
}

function getActiveRepositoryId() {
  return state.activeRepositoryId || repositorySelect.value;
}

function setStatus(text, isError = false) {
  loadStatus.textContent = text;
  loadStatus.classList.toggle("error", isError);
}

function updateActiveRepoText() {
  const repositoryId = getActiveRepositoryId();
  activeRepoText.textContent = repositoryId ? `当前仓库：${repositoryId}` : "请选择或导入一个仓库";
  deleteProjectButton.disabled = !repositoryId;
  const item = state.repositories.find((repository) => repository.repository_id === repositoryId);
  if (!item) {
    projectSummary.innerHTML = '<p class="empty-summary">导入后将在这里显示索引统计。</p>';
    return;
  }
  projectSummary.innerHTML = "";
  const status = document.createElement("div");
  status.className = "project-status";
  const indicator = document.createElement("span");
  indicator.className = "status-indicator";
  const statusText = document.createElement("strong");
  statusText.textContent = item.status === "ready" ? "分析就绪" : "索引不完整";
  status.append(indicator, statusText);
  const metrics = document.createElement("dl");
  for (const [label, value] of [
    ["文件", item.files_indexed],
    ["代码片段", item.chunks_indexed],
    ["分支", item.default_branch || "未知"],
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
  appendMessage("assistant", welcomeMessage);
}

function requireRepository() {
  const repositoryId = getActiveRepositoryId();
  if (!repositoryId) {
    appendMessage("assistant", "请先在左侧选择或导入一个仓库。");
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
    option.textContent = "暂无仓库";
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
    localStorage.setItem("activeRepositoryId", state.activeRepositoryId);
  }

  updateActiveRepoText();
}

async function loadRepository() {
  const githubUrl = githubUrlInput.value.trim();
  if (!githubUrl) {
    setStatus("请先输入 GitHub 仓库 URL。", true);
    return;
  }

  state.isLoading = true;
  loadRepoButton.disabled = true;
  loadRepoButton.textContent = "分析中…";
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
    appendMessage("assistant", `仓库已导入：${data.repository_id}\n现在可以开始提问。`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.isLoading = false;
    loadRepoButton.disabled = false;
    loadRepoButton.textContent = "导入";
  }
}

async function sendQuestion(event) {
  event.preventDefault();
  const repositoryId = getActiveRepositoryId();
  const question = questionInput.value.trim();

  if (!repositoryId) {
    appendMessage("assistant", "请先在左侧选择或导入一个仓库。");
    return;
  }
  if (!question) {
    return;
  }

  appendMessage("user", question);
  questionInput.value = "";
  questionInput.style.height = "auto";
  sendButton.disabled = true;
  const thinking = appendMessage("assistant", "正在检索代码片段并调用模型...", [], { typing: true });

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }, true),
      body: JSON.stringify({ repository_id: repositoryId, question }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "问答失败");
    }
    thinking.remove();
    appendMessage("assistant", data.answer, data.sources, { logs: data.logs || [] });
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `出错了：${error.message}`);
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
    const response = await fetch(`/repository/report/${encodeURIComponent(repositoryId)}`, {
      headers: getApiHeaders(),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "生成项目报告失败");
    }
    thinking.remove();
    openArtifact({
      title: "项目分析报告",
      meta: `Repository ID: ${repositoryId}`,
      markdown: data.markdown,
      logs: data.logs || [],
    });
    appendMessage("assistant", "项目分析报告已生成，已在右侧文档区打开。");
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
    const response = await fetch("/repository/generate-readme", {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }, true),
      body: JSON.stringify({ repository_id: repositoryId }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "生成 README 失败");
    }
    thinking.remove();
    openArtifact({
      title: "README 草稿",
      meta: `Repository ID: ${repositoryId}`,
      markdown: data.markdown,
      logs: data.logs || [],
    });
    appendMessage("assistant", "README 草稿已生成，已在右侧文档区打开。");
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `出错了：${error.message}`);
  } finally {
    readmeButton.disabled = false;
  }
}

function selectRepository() {
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

loadRepoButton.addEventListener("click", loadRepository);
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

resetMessages();
initializeAuth().catch((error) => showLogin(error.message));

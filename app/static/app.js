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

const welcomeMessage =
  "先在左侧导入 GitHub 仓库，然后像聊天一样直接提问。我会基于检索到的代码片段回答，并列出来源文件。";

function getApiHeaders(extraHeaders = {}) {
  const headers = { ...extraHeaders };
  const apiKey = window.localStorage.getItem("githubCodeRagApiKey");
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  return headers;
}

function getActiveRepositoryId() {
  return repositorySelect.value;
}

function setStatus(text, isError = false) {
  loadStatus.textContent = text;
  loadStatus.classList.toggle("error", isError);
}

function updateActiveRepoText() {
  const repositoryId = getActiveRepositoryId();
  activeRepoText.textContent = repositoryId ? `当前仓库：${repositoryId}` : "请选择或导入一个仓库";
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
  const response = await fetch("/repositories", { headers: getApiHeaders() });
  if (!response.ok) {
    throw new Error("读取仓库列表失败");
  }
  const data = await response.json();
  repositorySelect.innerHTML = "";

  if (data.repositories.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无仓库";
    repositorySelect.appendChild(option);
  } else {
    for (const repositoryId of data.repositories) {
      const option = document.createElement("option");
      option.value = repositoryId;
      option.textContent = repositoryId;
      repositorySelect.appendChild(option);
    }
    if (selectedId) {
      repositorySelect.value = selectedId;
    }
  }

  updateActiveRepoText();
}

async function loadRepository() {
  const githubUrl = githubUrlInput.value.trim();
  if (!githubUrl) {
    setStatus("请先输入 GitHub 仓库 URL。", true);
    return;
  }

  loadRepoButton.disabled = true;
  setStatus("正在通过 GitHub API 远程遍历项目并建立索引，不会 clone 或下载仓库到本地...");

  try {
    const response = await fetch("/repository/load", {
      method: "POST",
      headers: getApiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ github_url: githubUrl }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "导入失败");
    }

    const indexStatus = data.index_cached
      ? "索引未变化，已复用缓存"
      : `写入 ${data.chunks_written ?? data.chunks_indexed} 个片段，变化文件 ${data.changed_files_count ?? data.files_indexed} 个`;
    setStatus(`导入成功：${data.files_indexed} 个文件，${data.chunks_indexed} 个片段。${indexStatus}。`);
    await refreshRepositories(data.repository_id);
    appendMessage("assistant", `仓库已导入：${data.repository_id}\n现在可以开始提问。`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    loadRepoButton.disabled = false;
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
      headers: getApiHeaders({ "Content-Type": "application/json" }),
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
      headers: getApiHeaders({ "Content-Type": "application/json" }),
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
repositorySelect.addEventListener("change", updateActiveRepoText);
chatForm.addEventListener("submit", sendQuestion);
clearChatButton.addEventListener("click", resetMessages);
reportButton.addEventListener("click", generateProjectReport);
readmeButton.addEventListener("click", generateReadme);
closeArtifactButton.addEventListener("click", closeArtifact);
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
refreshRepositories().catch((error) => setStatus(error.message, true));

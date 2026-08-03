(() => {
  "use strict";

  const labels = {
    review_pending: "검토 대기",
    planned: "지원 예정",
    applied: "지원 완료",
    excluded: "제외",
  };
  const state = {
    posts: [],
    settings: null,
    institutionKeywords: [],
    roleKeywords: [],
    activeGroup: "active",
    activeStatus: "review_pending",
    pollTimer: null,
    undoTimer: null,
  };

  const byId = (id) => document.getElementById(id);
  const crawlButton = byId("crawl-button");
  const crawlStatus = byId("crawl-status");
  const crawlErrors = byId("crawl-errors");
  const errorMessage = byId("error-message");
  const toast = byId("toast");

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = "요청을 처리하지 못했습니다.";
      try {
        detail = (await response.json()).detail || detail;
      } catch (_) {
        // A non-JSON error response still has a useful status below.
      }
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function showError(message) {
    errorMessage.textContent = message;
    errorMessage.hidden = false;
    window.setTimeout(() => { errorMessage.hidden = true; }, 5000);
  }

  function clearNode(node) {
    node.replaceChildren();
  }

  function textElement(tagName, text, className = "") {
    const element = document.createElement(tagName);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function formatDeadline(post) {
    if (post.deadline_kind !== "dated" || !post.deadline_date) return post.deadline_raw;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const deadline = new Date(`${post.deadline_date}T00:00:00`);
    const days = Math.round((deadline - today) / 86400000);
    if (days === 0) return `${post.deadline_raw} · D-day`;
    if (days > 0) return `${post.deadline_raw} · D-${days}`;
    return `${post.deadline_raw} · 마감 지남`;
  }

  function createStatusButton(post, status) {
    const button = textElement("button", labels[status]);
    button.type = "button";
    button.addEventListener("click", () => updateStatus(post, status));
    return button;
  }

  function createCard(post) {
    const card = document.createElement("article");
    card.className = "job-card";
    const title = document.createElement("h3");
    const link = document.createElement("a");
    link.href = post.link;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = post.display_roles?.length
      ? (post.institution || post.original_title)
      : post.original_title;
    title.append(link);
    card.append(title);

    if (post.institution) {
      const meta = document.createElement("div");
      meta.className = "badges";
      [post.employment, post.career].filter(Boolean).forEach((value) => {
        meta.append(textElement("span", value, "badge"));
      });
      if (meta.childElementCount) card.append(meta);
    }

    if (post.display_roles && post.display_roles.length) {
      const roles = document.createElement("div");
      roles.className = "roles";
      post.display_roles.forEach((role) => {
        const roleElement = textElement("span", role, "role");
        roleElement.title = role;
        roles.append(roleElement);
      });
      card.append(roles);
    }

    const deadline = textElement("div", `마감: ${formatDeadline(post)}`, "deadline");
    if (post.deadline_kind === "dated") {
      const date = new Date(`${post.deadline_date}T00:00:00`);
      const days = Math.round((date - new Date().setHours(0, 0, 0, 0)) / 86400000);
      if (days >= 0 && days <= 7) deadline.classList.add("soon");
    }
    card.append(deadline);

    const actions = document.createElement("div");
    actions.className = "card-actions";
    if (post.status === "review_pending") {
      actions.append(createStatusButton(post, "planned"), createStatusButton(post, "applied"));
    } else if (post.status === "planned") {
      actions.append(createStatusButton(post, "applied"), createStatusButton(post, "review_pending"));
    } else if (post.status === "applied") {
      actions.append(createStatusButton(post, "review_pending"));
    } else {
      actions.append(createStatusButton(post, "review_pending"));
    }
    if (post.status !== "excluded") {
      const exclude = textElement("button", "제외");
      exclude.type = "button";
      exclude.addEventListener("click", () => excludePost(post));
      actions.append(exclude);
    }
    if (post.status === "excluded" || post.deadline_kind !== "dated") {
      const remove = textElement("button", "영구 삭제");
      remove.type = "button";
      remove.addEventListener("click", () => deletePost(post));
      actions.append(remove);
    }
    card.append(actions);
    return card;
  }

  function filteredPosts() {
    const query = byId("search-input").value.trim().toLocaleLowerCase();
    const category = byId("category-filter").value;
    const sort = byId("sort-select").value;
    return state.posts.filter((post) => {
      const searchText = [post.institution, post.original_title, ...(post.display_roles || [])]
        .filter(Boolean).join(" ").toLocaleLowerCase();
      return (!query || searchText.includes(query)) && (!category || post.category === category);
    }).sort((first, second) => {
      if (sort === "recent") return String(second.last_seen_at).localeCompare(String(first.last_seen_at));
      return String(first.deadline_date || "9999-12-31").localeCompare(String(second.deadline_date || "9999-12-31"));
    });
  }

  function renderStatus(status, posts) {
    document.querySelectorAll(`[data-count="${status}"]`).forEach((count) => { count.textContent = String(posts.length); });
    document.querySelectorAll(`[data-post-list="${status}"]`).forEach((list) => {
      clearNode(list);
      if (!posts.length) {
        list.append(textElement("p", "표시할 공고가 없습니다.", "empty-state"));
        return;
      }
      posts.forEach((post) => list.append(createCard(post)));
    });
  }

  function renderPosts() {
    const posts = filteredPosts();
    Object.keys(labels).forEach((status) => renderStatus(status, posts.filter((post) => post.status === status)));
  }

  function populateCategories() {
    const select = byId("category-filter");
    const selected = select.value;
    const categories = [...new Set(state.posts.map((post) => post.category))].sort();
    clearNode(select);
    select.append(new Option("전체 카테고리", ""));
    categories.forEach((category) => select.append(new Option(category, category)));
    select.value = categories.includes(selected) ? selected : "";
  }

  async function refreshPosts() {
    const payload = await request("/api/posts");
    state.posts = payload.posts;
    populateCategories();
    renderPosts();
  }

  async function updateStatus(post, status) {
    try {
      await request("/api/posts/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ link: post.link, status }),
      });
      post.status = status;
      renderPosts();
    } catch (error) { showError(error.message); }
  }

  async function excludePost(post) {
    const previousStatus = post.status;
    await updateStatus(post, "excluded");
    if (post.status !== "excluded") return;
    toast.textContent = "공고를 제외했습니다. 10초 안에 실행 취소할 수 있습니다.";
    const undo = textElement("button", "실행 취소");
    undo.type = "button";
    undo.addEventListener("click", async () => {
      window.clearTimeout(state.undoTimer);
      toast.hidden = true;
      await updateStatus(post, previousStatus);
    });
    toast.append(document.createTextNode(" "), undo);
    toast.hidden = false;
    window.clearTimeout(state.undoTimer);
    state.undoTimer = window.setTimeout(() => { toast.hidden = true; }, 10000);
  }

  async function deletePost(post) {
    if (!window.confirm("이 공고를 영구 삭제할까요? 다음 수집에서도 차단됩니다.")) return;
    try {
      await request("/api/posts/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ link: post.link }),
      });
      state.posts = state.posts.filter((item) => item.link !== post.link);
      renderPosts();
      await refreshDeletedLinks();
    } catch (error) { showError(error.message); }
  }

  function setCrawlStatus(snapshot) {
    if (snapshot.running) {
      crawlStatus.textContent = `수집 중: ${snapshot.completed_categories}/${snapshot.total_categories} 카테고리 · 신규 ${snapshot.new_count}건`;
    } else {
      crawlStatus.textContent = snapshot.total_categories
        ? `최근 수집: ${snapshot.completed_categories}/${snapshot.total_categories} 카테고리 · 신규 ${snapshot.new_count}건`
        : "아직 수집 결과가 없습니다.";
    }
    crawlButton.disabled = snapshot.running;
    clearNode(crawlErrors);
    if (snapshot.run_error) {
      crawlErrors.append(textElement("span", `실행 오류: ${snapshot.run_error}`));
    }
    Object.entries(snapshot.category_errors || {}).forEach(([category, error]) => {
      const item = textElement("span", `${category}: ${error}`, "crawl-error-item");
      const retry = textElement("button", "다시 시도");
      retry.type = "button";
      retry.setAttribute("aria-label", `${category} 다시 시도`);
      retry.disabled = snapshot.running;
      retry.addEventListener("click", () => retryCategory(category));
      item.append(retry);
      crawlErrors.append(item);
    });
  }

  function stopPolling() {
    if (state.pollTimer !== null) window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  async function checkCrawlStatus() {
    try {
      const snapshot = await request("/api/crawl/status");
      setCrawlStatus(snapshot);
      if (!snapshot.running && state.pollTimer !== null) {
        stopPolling();
        await refreshPosts();
      }
      return snapshot;
    } catch (error) { showError(error.message); return null; }
  }

  function startPolling() {
    if (state.pollTimer !== null) return;
    state.pollTimer = window.setInterval(checkCrawlStatus, 1000);
  }

  async function startCrawl() {
    try {
      await request("/api/crawl/start", { method: "POST" });
      crawlButton.disabled = true;
      const snapshot = await checkCrawlStatus();
      if (snapshot?.running !== false) startPolling();
      if (snapshot?.running === false) await refreshPosts();
    } catch (error) { showError(error.message); }
  }

  async function retryCategory(category) {
    try {
      await request(`/api/crawl/retry/${encodeURIComponent(category)}`, { method: "POST" });
      crawlButton.disabled = true;
      const snapshot = await checkCrawlStatus();
      if (snapshot?.running !== false) startPolling();
      if (snapshot?.running === false) await refreshPosts();
    } catch (error) { showError(error.message); }
  }

  function renderKeywords(container, values, remove) {
    clearNode(container);
    values.forEach((value, index) => {
      const chip = textElement("span", value, "keyword-chip");
      const button = textElement("button", "×");
      button.type = "button";
      button.setAttribute("aria-label", `${value} 삭제`);
      button.addEventListener("click", () => remove(index));
      chip.append(button);
      container.append(chip);
    });
  }

  function renderSettings() {
    if (!state.settings) return;
    document.querySelector(`input[name="concurrency"][value="${state.settings.concurrency}"]`).checked = true;
    byId("open-browser").checked = state.settings.open_browser;
    renderKeywords(byId("institution-keywords"), state.institutionKeywords, (index) => {
      state.institutionKeywords.splice(index, 1); renderSettings();
    });
    renderKeywords(byId("role-keywords"), state.roleKeywords, (index) => {
      state.roleKeywords.splice(index, 1); renderSettings();
    });
  }

  function addKeyword(inputId, values) {
    const input = byId(inputId);
    const value = input.value.trim();
    if (!value) return;
    if (!values.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) values.push(value);
    input.value = "";
    renderSettings();
  }

  async function refreshSettings() {
    const settings = await request("/api/settings");
    state.settings = settings;
    state.institutionKeywords = [...settings.institution_keywords];
    state.roleKeywords = [...settings.role_keywords];
    renderSettings();
  }

  async function refreshDeletedLinks() {
    const payload = await request("/api/deleted-links");
    const list = byId("deleted-links");
    clearNode(list);
    if (!payload.deleted_links.length) list.append(textElement("li", "차단한 링크가 없습니다."));
    payload.deleted_links.forEach((record) => {
      const item = document.createElement("li");
      item.append(textElement("span", record.link));
      const unblock = textElement("button", "차단 해제");
      unblock.type = "button";
      unblock.addEventListener("click", async () => {
        try { await request(`/api/deleted-links/${record.id}`, { method: "DELETE" }); await refreshDeletedLinks(); }
        catch (error) { showError(error.message); }
      });
      item.append(unblock);
      list.append(item);
    });
  }

  function openSettings() {
    const drawer = byId("settings-drawer");
    Promise.all([refreshSettings(), refreshDeletedLinks()]).then(() => {
      drawer.hidden = false;
      byId("settings-button").setAttribute("aria-expanded", "true");
    }).catch((error) => showError(error.message));
  }

  function closeSettings() {
    byId("settings-drawer").hidden = true;
    byId("settings-button").setAttribute("aria-expanded", "false");
  }

  function bindEvents() {
    crawlButton.addEventListener("click", startCrawl);
    byId("settings-button").addEventListener("click", openSettings);
    byId("settings-close").addEventListener("click", closeSettings);
    ["search-input", "category-filter", "sort-select"].forEach((id) => byId(id).addEventListener("input", renderPosts));
    document.querySelectorAll("[data-group-tab]").forEach((button) => button.addEventListener("click", () => {
      state.activeGroup = button.dataset.groupTab;
      document.querySelectorAll("[data-group-tab]").forEach((item) => item.classList.toggle("selected", item === button));
      document.querySelectorAll("[data-view-group]").forEach((view) => { view.hidden = view.dataset.viewGroup !== state.activeGroup; });
    }));
    document.querySelectorAll("[data-status-tab]").forEach((button) => button.addEventListener("click", () => {
      state.activeStatus = button.dataset.statusTab;
      document.querySelectorAll("[data-status-tab]").forEach((item) => item.classList.toggle("selected", item === button));
      document.querySelectorAll("[data-status-panel]").forEach((panel) => { panel.hidden = panel.dataset.statusPanel !== state.activeStatus; });
    }));
    byId("institution-keyword-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); addKeyword("institution-keyword-input", state.institutionKeywords); }
    });
    byId("role-keyword-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); addKeyword("role-keyword-input", state.roleKeywords); }
    });
    byId("settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const concurrency = Number(document.querySelector('input[name="concurrency"]:checked').value);
      try {
        const settings = await request("/api/settings", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ concurrency, open_browser: byId("open-browser").checked, institution_keywords: state.institutionKeywords, role_keywords: state.roleKeywords }),
        });
        state.settings = settings;
        state.institutionKeywords = [...settings.institution_keywords];
        state.roleKeywords = [...settings.role_keywords];
        renderSettings();
        await refreshPosts();
        toast.textContent = "설정을 저장했습니다.";
        toast.hidden = false;
        window.setTimeout(() => { toast.hidden = true; }, 3000);
      } catch (error) { showError(error.message); }
    });
  }

  async function initialize() {
    bindEvents();
    try {
      const snapshot = await checkCrawlStatus();
      if (snapshot?.running) startPolling();
      await refreshPosts();
    } catch (error) { showError(error.message); }
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();

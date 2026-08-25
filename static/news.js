(() => {
  "use strict";

  const state = {
    items: [],
    period: "today",
    loaded: false,
    loading: false,
    requestVersion: 0,
    pollTimer: null,
    searchTimer: null,
  };

  const byId = (id) => document.getElementById(id);
  const jobView = byId("job-view");
  const newsView = byId("news-view");
  const newsList = byId("news-list");
  const newsCrawlButton = byId("news-crawl-button");
  const newsCrawlStatus = byId("news-crawl-status");
  const newsCrawlErrors = byId("news-crawl-errors");
  const newsErrors = byId("news-errors");

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = "요청을 처리하지 못했습니다.";
      try {
        detail = (await response.json()).detail || detail;
      } catch (_) {
        // A non-JSON response still provides a useful generic message.
      }
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function textElement(tagName, text, className = "") {
    const element = document.createElement(tagName);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function showNewsError(message) {
    newsErrors.textContent = message;
    newsErrors.hidden = false;
  }

  function clearNewsError() {
    newsErrors.textContent = "";
    newsErrors.hidden = true;
  }

  function formatDate(item) {
    const value = item.published_at || item.discovered_at;
    if (!value) return "날짜 없음";
    const date = new Date(value);
    const label = Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString("ko-KR");
    return item.used_discovered_date ? `수집 ${label}` : label;
  }

  function createExternalLink(label, url, className = "") {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = label;
    if (className) link.className = className;
    return link;
  }

  function renderNews() {
    newsList.replaceChildren();
    newsList.setAttribute("aria-busy", String(state.loading));
    if (state.loading) {
      newsList.append(textElement("p", "뉴스를 불러오는 중입니다.", "empty-state"));
      return;
    }
    if (!state.items.length) {
      newsList.append(textElement("p", "표시할 뉴스가 없습니다.", "empty-state"));
      return;
    }
    state.items.forEach((item) => newsList.append(createNewsRow(item)));
  }

  function createNewsRow(item) {
    const row = document.createElement("article");
    row.className = "news-row";

    const metadata = document.createElement("div");
    metadata.className = "news-metadata";
    metadata.append(
      textElement("span", item.item_type === "institution" ? "기관소식" : "뉴스", "news-type"),
      textElement("span", item.source_name, "news-source"),
      textElement("span", item.category, "news-category"),
      textElement("time", formatDate(item), "news-date"),
    );
    row.append(metadata);

    const content = document.createElement("div");
    content.className = "news-content";
    const title = document.createElement("h2");
    title.append(createExternalLink(item.title, item.url, "news-title-link"));
    content.append(title, createExternalLink("원문 보기", item.url, "news-original-link"));
    row.append(content);

    const actions = document.createElement("div");
    actions.className = "news-actions";
    const dismissButton = textElement("button", "처리 완료", "secondary-button");
    dismissButton.type = "button";
    dismissButton.addEventListener("click", () => dismissNews(item, dismissButton));
    actions.append(dismissButton);
    row.append(actions);
    return row;
  }

  async function dismissNews(item, button) {
    button.disabled = true;
    try {
      await request(`/api/news/${item.id}`, { method: "DELETE" });
      state.items = state.items.filter((current) => current.id !== item.id);
      renderNews();
    } catch (error) {
      button.disabled = false;
      showNewsError(error.message);
    }
  }

  function newsQuery() {
    const params = new URLSearchParams({ period: state.period });
    const filters = {
      item_type: byId("news-type-filter").value,
      source: byId("news-source-filter").value,
      category: byId("news-category-filter").value,
      q: byId("news-search-input").value.trim(),
    };
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    return params;
  }

  async function refreshNews() {
    const version = ++state.requestVersion;
    state.loading = true;
    renderNews();
    clearNewsError();
    try {
      const response = await request(`/api/news?${newsQuery()}`);
      if (version !== state.requestVersion) return;
      state.items = response.items;
    } catch (error) {
      if (version !== state.requestVersion) return;
      state.items = [];
      showNewsError(error.message);
    } finally {
      if (version === state.requestVersion) {
        state.loading = false;
        renderNews();
      }
    }
  }

  function renderNewsCrawl(snapshot) {
    newsCrawlStatus.textContent = [
      `수집 ${snapshot.completed_sources}/${snapshot.total_sources}`,
      `신규 ${snapshot.new_count}`,
      `중복 ${snapshot.duplicate_count}`,
      `TTL ${snapshot.expired_count}`,
    ].join(" · ");
    newsCrawlErrors.replaceChildren();
    Object.entries(snapshot.source_errors || {}).forEach(([source, message]) => {
      newsCrawlErrors.append(textElement("span", `${source}: ${message}`, "crawl-error-item"));
    });
    if (snapshot.run_error) showNewsError(snapshot.run_error);
  }

  function stopNewsPolling() {
    if (state.pollTimer !== null) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  async function pollNewsCrawl() {
    try {
      const snapshot = await request("/api/news/crawl/status");
      renderNewsCrawl(snapshot);
      if (snapshot?.running) {
        state.pollTimer = window.setTimeout(pollNewsCrawl, 1000);
        return;
      }
      if (snapshot?.running === false) {
        stopNewsPolling();
        newsCrawlButton.disabled = false;
        await refreshNews();
      }
    } catch (error) {
      showNewsError(error.message);
      state.pollTimer = window.setTimeout(pollNewsCrawl, 1000);
    }
  }

  function startNewsPolling() {
    if (state.pollTimer === null) pollNewsCrawl();
  }

  async function startNewsCrawl() {
    newsCrawlButton.disabled = true;
    clearNewsError();
    try {
      await request("/api/news/crawl/start", { method: "POST" });
      const snapshot = await request("/api/news/crawl/status");
      renderNewsCrawl(snapshot);
      if (snapshot?.running !== false) {
        startNewsPolling();
      } else {
        newsCrawlButton.disabled = false;
        await refreshNews();
      }
    } catch (error) {
      newsCrawlButton.disabled = false;
      showNewsError(error.message);
    }
  }

  async function initializeNews() {
    if (state.loaded) return;
    state.loaded = true;
    await refreshNews();
    try {
      const snapshot = await request("/api/news/crawl/status");
      renderNewsCrawl(snapshot);
      newsCrawlButton.disabled = Boolean(snapshot.running);
      if (snapshot.running) startNewsPolling();
      else newsCrawlButton.disabled = false;
    } catch (error) {
      newsCrawlButton.disabled = false;
      showNewsError(error.message);
    }
  }

  function selectPrimaryTab(tab) {
    const showNews = tab === "news";
    jobView.hidden = showNews;
    newsView.hidden = !showNews;
    document.querySelectorAll("[data-primary-tab]").forEach((button) => {
      const selected = button.dataset.primaryTab === tab;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    if (showNews) initializeNews();
  }

  document.querySelectorAll("[data-primary-tab]").forEach((button) => {
    button.addEventListener("click", () => selectPrimaryTab(button.dataset.primaryTab));
  });
  document.querySelectorAll("[data-news-period]").forEach((button) => {
    button.addEventListener("click", () => {
      state.period = button.dataset.newsPeriod;
      document.querySelectorAll("[data-news-period]").forEach((periodButton) => {
        const selected = periodButton === button;
        periodButton.classList.toggle("selected", selected);
        periodButton.setAttribute("aria-pressed", String(selected));
      });
      refreshNews();
    });
  });
  ["news-type-filter", "news-source-filter", "news-category-filter"].forEach((id) => {
    byId(id).addEventListener("change", refreshNews);
  });
  byId("news-search-input").addEventListener("input", () => {
    if (state.searchTimer !== null) window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(refreshNews, 250);
  });
  newsCrawlButton.addEventListener("click", startNewsCrawl);
})();

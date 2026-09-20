(() => {
  "use strict";

  const FILTER_STORAGE_KEY = "gonggonggo.news.checkboxFilters";
  const FILTER_VALUES = {
    sources: ["hankyung", "mk", "reb", "kodit", "kogas"],
    categories: ["주요뉴스", "정치", "경제", "사회", "IT", "세계", "보도자료", "정기 통계"],
  };

  const state = {
    items: [],
    period: "today",
    itemType: "",
    loaded: false,
    loading: false,
    listError: "",
    requestVersion: 0,
    pollTimer: null,
    polling: false,
    crawlActive: false,
    searchTimer: null,
  };

  const byId = (id) => document.getElementById(id);
  const jobView = byId("job-view");
  const newsView = byId("news-view");
  const newsList = byId("news-list");
  const newsCount = byId("news-count");
  const newsCrawlButton = byId("news-crawl-button");
  const newsCrawlStatus = byId("news-crawl-status");
  const crawlError = byId("news-crawl-errors");
  const listError = byId("news-errors");

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = "요청을 처리하지 못했습니다.";
      try {
        detail = (await response.json()).detail || detail;
      } catch (_) {
        // A non-JSON response still provides a useful generic message.
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function textElement(tagName, text, className = "") {
    const element = document.createElement(tagName);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function showListError(message) {
    state.listError = message;
    listError.textContent = message;
    listError.hidden = false;
  }

  function clearListError() {
    state.listError = "";
    listError.textContent = "";
    listError.hidden = true;
  }

  function showCrawlError(message) {
    crawlError.textContent = message;
  }

  function formatDate(item) {
    const value = item.published_at || item.discovered_at;
    if (!value) return "날짜 없음";
    const date = new Date(value);
    const label = Number.isNaN(date.valueOf())
      ? value
      : date.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
    return item.used_discovered_date ? `수집일 기준 ${label}` : label;
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

  function selectedCheckboxValues(name) {
    return new Set(
      [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value),
    );
  }

  function filteredItems() {
    const sources = selectedCheckboxValues("news-source");
    const categories = selectedCheckboxValues("news-category");
    return state.items.filter((item) => sources.has(item.source) && categories.has(item.category));
  }

  function renderNews() {
    const items = filteredItems();
    newsCount.textContent = `총 ${items.length}건`;
    newsList.replaceChildren();
    newsList.setAttribute("aria-busy", String(state.loading));
    if (state.loading) {
      newsList.append(textElement("p", "뉴스를 불러오는 중입니다.", "empty-state"));
      return;
    }
    if (state.listError) {
      newsList.append(textElement("p", state.listError, "empty-state"));
      return;
    }
    if (!items.length) {
      newsList.append(textElement("p", "표시할 뉴스가 없습니다.", "empty-state"));
      return;
    }
    items.forEach((item) => newsList.append(createNewsRow(item)));
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
    );
    const time = textElement("time", formatDate(item), "news-date");
    time.dateTime = item.published_at || item.discovered_at || "";
    metadata.append(time);
    row.append(metadata);

    const content = document.createElement("div");
    content.className = "news-content";
    const title = document.createElement("h2");
    title.append(createExternalLink(item.title, item.url, "news-title news-title-link"));
    content.append(title);
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
    ++state.requestVersion;
    button.disabled = true;
    try {
      await request(`/api/news/${item.id}`, { method: "DELETE" });
      state.items = state.items.filter((current) => current.id !== item.id);
      renderNews();
      await refreshNews();
    } catch (error) {
      button.disabled = false;
      showListError(error.message);
    }
  }

  function newsQuery() {
    const params = new URLSearchParams({ period: state.period });
    const filters = {
      item_type: state.itemType,
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
    clearListError();
    try {
      const response = await request(`/api/news?${newsQuery()}`);
      if (version !== state.requestVersion) return;
      state.items = response.items;
    } catch (error) {
      if (version !== state.requestVersion) return;
      state.items = [];
      showListError(error.message);
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
    crawlError.replaceChildren();
    Object.entries(snapshot.source_errors || {}).forEach(([source, message]) => {
      crawlError.append(textElement("span", `${source}: ${message}`, "crawl-error-item"));
    });
    if (snapshot.run_error) crawlError.append(textElement("span", snapshot.run_error, "crawl-error-item"));
  }

  function stopNewsPolling() {
    if (state.pollTimer !== null) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function scheduleNewsPolling() {
    if (state.pollTimer !== null) return;
    state.pollTimer = window.setTimeout(() => {
      state.pollTimer = null;
      pollNewsCrawl();
    }, 1000);
  }

  async function pollNewsCrawl() {
    if (state.polling) return;
    state.polling = true;
    try {
      const snapshot = await request("/api/news/crawl/status");
      renderNewsCrawl(snapshot);
      if (snapshot?.running) {
        state.crawlActive = true;
        scheduleNewsPolling();
        return;
      }
      if (snapshot?.running === false) {
        if (!state.crawlActive) return;
        state.crawlActive = false;
        stopNewsPolling();
        newsCrawlButton.disabled = false;
        await refreshNews();
      }
    } catch (error) {
      showCrawlError(error.message);
      if (state.crawlActive) scheduleNewsPolling();
    } finally {
      state.polling = false;
    }
  }

  function startNewsPolling() {
    if (state.pollTimer === null && !state.polling) pollNewsCrawl();
  }

  async function startNewsCrawl() {
    newsCrawlButton.disabled = true;
    crawlError.replaceChildren();
    try {
      await request("/api/news/crawl/start", { method: "POST" });
    } catch (error) {
      if (error.status !== 409) {
        newsCrawlButton.disabled = false;
        showCrawlError(error.message);
        return;
      }
      showCrawlError("이미 뉴스 수집이 진행 중입니다.");
    }
    state.crawlActive = true;
    startNewsPolling();
  }

  async function initializeNews() {
    if (state.loaded) return;
    state.loaded = true;
    try {
      const snapshot = await request("/api/news/crawl/status");
      renderNewsCrawl(snapshot);
      if (snapshot.running) {
        state.crawlActive = true;
        newsCrawlButton.disabled = true;
        startNewsPolling();
      } else {
        newsCrawlButton.disabled = false;
      }
    } catch (error) {
      newsCrawlButton.disabled = false;
      showCrawlError(error.message);
    }
    await refreshNews();
  }

  function restoreCheckboxFilters() {
    let saved;
    try {
      saved = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY));
    } catch (_) {
      return;
    }
    if (!saved || !Array.isArray(saved.sources) || !Array.isArray(saved.categories)) return;
    const sources = new Set(saved.sources.filter((value) => FILTER_VALUES.sources.includes(value)));
    const categories = new Set(saved.categories.filter((value) => FILTER_VALUES.categories.includes(value)));
    document.querySelectorAll('input[name="news-source"]').forEach((input) => {
      input.checked = sources.has(input.value);
    });
    document.querySelectorAll('input[name="news-category"]').forEach((input) => {
      input.checked = categories.has(input.value);
    });
  }

  function saveCheckboxFilters() {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({
      sources: [...selectedCheckboxValues("news-source")],
      categories: [...selectedCheckboxValues("news-category")],
    }));
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

  restoreCheckboxFilters();

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
  document.querySelectorAll("[data-news-type]").forEach((button) => {
    button.addEventListener("click", () => {
      state.itemType = button.dataset.newsType;
      document.querySelectorAll("[data-news-type]").forEach((typeButton) => {
        const selected = typeButton === button;
        typeButton.classList.toggle("selected", selected);
        typeButton.setAttribute("aria-pressed", String(selected));
      });
      refreshNews();
    });
  });
  document.querySelectorAll('input[name="news-source"], input[name="news-category"]').forEach((input) => {
    input.addEventListener("change", () => {
      saveCheckboxFilters();
      renderNews();
    });
  });
  byId("news-search-input").addEventListener("input", () => {
    if (state.searchTimer !== null) window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(refreshNews, 250);
  });
  newsCrawlButton.addEventListener("click", startNewsCrawl);
})();

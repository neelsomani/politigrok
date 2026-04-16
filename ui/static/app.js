const statusEl = document.getElementById("status");
const cardsEl = document.getElementById("cards");
const searchEl = document.getElementById("search");
const refreshEl = document.getElementById("refresh");
const loadMoreEl = document.getElementById("load-more");
const templateEl = document.getElementById("card-template");

let allRows = [];
let totalRows = 0;
let currentPage = 1;
const pageSize = 20;
let hasMore = false;
let currentQuery = "";
let currentSlug = "";

function toText(value) {
  if (!value) return "";
  return String(value);
}

function setTextSafe(root, selector, value) {
  const node = root.querySelector(selector);
  if (!node) return;
  node.textContent = value;
}

function render(rows) {
  cardsEl.innerHTML = "";

  if (!rows.length) {
    statusEl.textContent = currentSlug ? "No comparison found for that link." : "No matching fact checks.";
    return;
  }

  statusEl.textContent = `Showing ${rows.length} of ${totalRows} fact checks`;

  for (const row of rows) {
    const fragment = templateEl.content.cloneNode(true);

    setTextSafe(fragment, ".claim", toText(row.claim) || toText(row.title) || "(No claim text)");

    const link = fragment.querySelector(".source-link");
    if (row.url) {
      link.href = row.url;
      link.textContent = "Open PolitiFact article";
    } else {
      link.removeAttribute("href");
      link.textContent = "No source URL";
    }

    const copyLinkBtn = fragment.querySelector(".copy-link-btn");
    if (copyLinkBtn) {
      copyLinkBtn.addEventListener("click", async () => {
        const deepLink = `${window.location.origin}${window.location.pathname}?slug=${encodeURIComponent(row.slug)}`;
        try {
          await navigator.clipboard.writeText(deepLink);
          copyLinkBtn.textContent = "Copied!";
          setTimeout(() => {
            copyLinkBtn.textContent = "Copy link";
          }, 1200);
        } catch (err) {
          copyLinkBtn.textContent = "Copy failed";
          setTimeout(() => {
            copyLinkBtn.textContent = "Copy link";
          }, 1200);
        }
      });
    }

    const meta = [];
    if (row.published) meta.push(`Published: ${row.published}`);
    if (row.grok_generated_at) meta.push(`Grok generated: ${row.grok_generated_at}`);
    setTextSafe(fragment, ".meta", meta.join(" • "));

    setTextSafe(
      fragment,
      ".politifact-verdict",
      row.politifact_verdict ? `Verdict: ${row.politifact_verdict}` : "Verdict unavailable",
    );
    setTextSafe(fragment, ".politifact-text", toText(row.politifact_text) || "No PolitiFact raw text found.");

    const grokTitle = row.grok_model ? `Grok (${row.grok_model})` : "Grok";
    setTextSafe(fragment, ".grok-title", grokTitle);

    let grokVerdictLine = "Verdict unavailable";
    if (row.grok_verdict) {
      if (row.grok_confidence !== null && row.grok_confidence !== undefined) {
        grokVerdictLine = `Verdict: ${row.grok_verdict} (${row.grok_confidence}%)`;
      } else {
        grokVerdictLine = `Verdict: ${row.grok_verdict}`;
      }
    }
    setTextSafe(
      fragment,
      ".grok-verdict",
      grokVerdictLine,
    );
    setTextSafe(fragment, ".grok-text", toText(row.grok_display_text) || "No Grok output found.");

    cardsEl.appendChild(fragment);
  }
}

function applyFilter() {
  render(allRows);
  updateLoadMoreVisibility();
}

function updateLoadMoreVisibility() {
  if (currentSlug) {
    loadMoreEl.classList.add("hidden");
    return;
  }

  const query = searchEl.value.trim();
  if (query) {
    loadMoreEl.classList.add("hidden");
    return;
  }

  if (hasMore) {
    loadMoreEl.classList.remove("hidden");
  } else {
    loadMoreEl.classList.add("hidden");
  }
}

async function fetchPage(page) {
  try {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (currentQuery) {
      params.set("q", currentQuery);
    }
    if (currentSlug) {
      params.set("slug", currentSlug);
    }
    const response = await fetch(`/api/fact-checks?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }
    return await response.json();
  } catch (err) {
    statusEl.textContent = `Failed to load data: ${err.message}`;
    cardsEl.innerHTML = "";
    loadMoreEl.classList.add("hidden");
    return null;
  }
}

async function loadRows() {
  statusEl.textContent = "Loading fact checks...";
  currentPage = 1;
  allRows = [];
  const params = new URLSearchParams(window.location.search);
  currentSlug = (params.get("slug") || "").trim();

  currentQuery = currentSlug ? "" : searchEl.value.trim();

  searchEl.disabled = Boolean(currentSlug);

  const payload = await fetchPage(currentPage);
  if (!payload) return;

  allRows = payload.items || [];
  totalRows = payload.total || allRows.length;
  hasMore = Boolean(payload.has_more);
  applyFilter();
  updateLoadMoreVisibility();
}

async function loadMore() {
  if (!hasMore) return;
  loadMoreEl.disabled = true;
  loadMoreEl.textContent = "Loading...";

  const nextPage = currentPage + 1;
  const payload = await fetchPage(nextPage);
  if (!payload) {
    loadMoreEl.disabled = false;
    loadMoreEl.textContent = "Load more";
    return;
  }

  currentPage = nextPage;
  allRows = [...allRows, ...(payload.items || [])];
  totalRows = payload.total || allRows.length;
  hasMore = Boolean(payload.has_more);
  applyFilter();
  updateLoadMoreVisibility();

  loadMoreEl.disabled = false;
  loadMoreEl.textContent = "Load more";
}

let searchDebounceTimer = null;
searchEl.addEventListener("input", () => {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer);
  }
  searchDebounceTimer = setTimeout(() => {
    loadRows();
  }, 250);
});
refreshEl.addEventListener("click", loadRows);
loadMoreEl.addEventListener("click", loadMore);

loadRows();

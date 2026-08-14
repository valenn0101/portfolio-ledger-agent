const sessionId = (() => {
  let value = localStorage.getItem("portfolio_ledger_agent_session");
  if (!value) {
    value = localStorage.getItem("cartera_clara_session");
  }
  if (!value) {
    value = crypto.randomUUID().replaceAll("-", "");
  }
  localStorage.setItem("portfolio_ledger_agent_session", value);
  return value;
})();

const messages = document.getElementById("messages");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const movementPlaceholder = "Ej.: Vendí 2 MSFT a 425 USD en Wallbit ayer";
const correctionPlaceholder = "Ej.: el precio correcto es 330,50 y el ticker es GOOG";
const draftActions = document.getElementById("draft-actions");
const confirmButton = document.getElementById("confirm-button");
const cancelButton = document.getElementById("cancel-button");
const researchForm = document.getElementById("research-form");
const researchType = document.getElementById("research-type");
const researchQuery = document.getElementById("research-query");
const researchAdvice = document.getElementById("research-advice");
const researchSubmit = document.getElementById("research-submit");
const quoteRefresh = document.getElementById("quote-refresh");
const logoutButton = document.getElementById("logout-button");
const researchReports = new Map();
const defaultDocumentTitle = document.title;

async function apiFetch(input, init) {
  const response = await fetch(input, init);
  if (response.status === 401) {
    window.location.replace("/login?expired=1");
    throw new Error("La sesión venció.");
  }
  return response;
}

async function loadSession() {
  const response = await apiFetch("/api/session");
  const session = await response.json();
  logoutButton.classList.toggle("hidden", !session.auth_enabled);
  if (session.auth_enabled && session.username) {
    logoutButton.title = `Sesión de ${session.username}`;
  }
}

function addMessage(role, text, sources = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  if (role === "assistant") {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "PL";
    article.appendChild(avatar);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  if (sources.length) {
    const links = document.createElement("div");
    links.className = "message-sources";
    sources.slice(0, 5).forEach((source, index) => {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `Fuente ${index + 1}: ${source.title || source.url}`;
      links.appendChild(link);
    });
    bubble.appendChild(links);
  }
  article.appendChild(bubble);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
}

function setBusy(busy) {
  input.disabled = busy;
  composer.querySelector("button").disabled = busy;
}

async function sendMessage(text) {
  const clean = text.trim();
  if (!clean) return;
  addMessage("user", clean);
  input.value = "";
  setBusy(true);
  try {
    const response = await apiFetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: clean }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo procesar el mensaje.");
    if (result.kind === "research") {
      renderResearch(result);
      addMessage("assistant", `Preparé y guardé la investigación #${result.id}. El informe completo y sus fuentes están en la sección Investigación.`);
      await loadResearchHistory();
    } else {
      addMessage("assistant", result.message, result.sources || []);
    }
    draftActions.classList.toggle("hidden", !["missing", "confirmation"].includes(result.kind));
    confirmButton.classList.toggle("hidden", result.kind !== "confirmation");
    await refreshDashboard();
  } catch (error) {
    addMessage("assistant", `No pude completar esa acción: ${error.message}`);
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function refreshDashboard() {
  const [statusResponse, historyResponse] = await Promise.all([
    apiFetch(`/api/status?session_id=${sessionId}`),
    apiFetch("/api/movements"),
    loadPortfolioValuation(),
  ]);
  const status = await statusResponse.json();
  const history = await historyResponse.json();

  document.getElementById("movement-count").textContent = status.stats.movements;
  document.getElementById("pending-count").textContent = status.pending ? 1 : 0;
  document.getElementById("error-count").textContent = status.stats.sync_errors;
  const cash = (status.cash_balances || [])[0];
  document.getElementById("cash-amount").textContent = cash
    ? `${new Intl.NumberFormat("es-AR", { maximumFractionDigits: 2 }).format(cash.amount)} ${cash.currency}`
    : "—";
  document.getElementById("default-account").value = status.settings.default_account || "";
  document.getElementById("default-currency").value = status.settings.default_currency || "";

  const excelStatus = document.getElementById("excel-status");
  excelStatus.textContent = status.excel_ready ? "Excel listo" : "Excel pendiente";
  excelStatus.className = `pill ${status.excel_ready ? "ok" : "warning"}`;
  const aiStatus = document.getElementById("ai-status");
  aiStatus.textContent = status.ai_enabled ? "IA activada" : "Modo local";
  aiStatus.className = `pill ${status.ai_enabled ? "ok" : ""}`;
  const researchStatus = document.getElementById("research-status");
  researchStatus.textContent = status.research_enabled ? "GPT + web listo" : "Falta API key";
  researchStatus.className = `pill ${status.research_enabled ? "ok" : "warning"}`;
  researchSubmit.disabled = !status.research_enabled;

  document.getElementById("system-note").textContent = status.excel_error
    ? `La base local funciona. Excel informa: ${status.excel_error}`
    : "La base local y el archivo Excel están disponibles.";

  draftActions.classList.toggle("hidden", !status.pending);
  confirmButton.classList.toggle("hidden", !status.pending || status.pending_missing.length > 0);
  input.placeholder = status.pending ? correctionPlaceholder : movementPlaceholder;
  renderHistory(history.movements || []);
}

async function loadPortfolioValuation(refresh = false) {
  const status = document.getElementById("portfolio-quote-status");
  const button = quoteRefresh;
  status.textContent = refresh ? "Actualizando" : "Cargando precios";
  status.className = "pill";
  button.disabled = true;
  try {
    const response = await apiFetch(`/api/portfolio/valuation${refresh ? "?refresh=1" : ""}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "No se pudieron cargar las cotizaciones.");
    renderPortfolioValuation(payload);
  } catch (error) {
    status.textContent = "Precios no disponibles";
    status.className = "pill warning";
    const container = document.getElementById("portfolio-quotes");
    container.innerHTML = `<tr><td colspan="6" class="portfolio-empty"></td></tr>`;
    container.querySelector("td").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderPortfolioValuation(payload) {
  const summary = payload.summary || {};
  setPortfolioMetric("portfolio-market-value", summary.market_value, "money");
  setPortfolioMetric("portfolio-total-value", summary.portfolio_value, "money");
  setPortfolioMetric("portfolio-day-change", summary.daily_change, "signed-money");
  setPortfolioMetric("portfolio-unrealized-change", summary.unrealized_change, "signed-money");
  document.getElementById("portfolio-day-percent").textContent = summary.daily_change_percent === null
    ? "sin cierre anterior comparable"
    : `${formatSignedPercent(summary.daily_change_percent)} contra cierre anterior`;
  document.getElementById("portfolio-unrealized-percent").textContent = summary.unrealized_change_percent === null
    ? "costo no calculable"
    : `${formatSignedPercent(summary.unrealized_change_percent)} no realizado`;

  const quoteStatus = document.getElementById("portfolio-quote-status");
  const coverage = payload.coverage || { quoted: 0, total: 0 };
  if (!coverage.total) {
    quoteStatus.textContent = "Sin posiciones";
    quoteStatus.className = "pill";
  } else if (payload.status === "ready") {
    quoteStatus.textContent = `${coverage.quoted}/${coverage.total} con precio`;
    quoteStatus.className = "pill ok";
  } else {
    quoteStatus.textContent = `${coverage.quoted}/${coverage.total} con precio`;
    quoteStatus.className = "pill warning";
  }

  const body = document.getElementById("portfolio-quotes");
  body.replaceChildren();
  if (!(payload.positions || []).length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "portfolio-empty";
    cell.textContent = "Todavía no hay posiciones abiertas para cotizar.";
    row.appendChild(cell);
    body.appendChild(row);
  } else {
    payload.positions.forEach((position) => body.appendChild(portfolioRow(position)));
  }

  const providers = (payload.providers_used || []).join(", ") || "sin proveedor";
  document.getElementById("portfolio-freshness").textContent =
    `Actualizado ${formatResearchDate(payload.generated_at)} · ${providers} · ${coverage.quoted}/${coverage.total} posiciones.`;
  document.getElementById("portfolio-currency-note").textContent = payload.currency_note || "Los totales se expresan en USD.";

  const warningBox = document.getElementById("portfolio-warnings");
  warningBox.replaceChildren();
  warningBox.classList.toggle("hidden", !(payload.warnings || []).length);
  if ((payload.warnings || []).length) {
    const title = document.createElement("strong");
    title.textContent = "Datos a revisar";
    const list = document.createElement("ul");
    payload.warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      list.appendChild(item);
    });
    warningBox.append(title, list);
  }
}

function portfolioRow(position) {
  const row = document.createElement("tr");
  const quote = position.quote;

  const investment = portfolioCell("Inversión");
  const identity = document.createElement("div");
  identity.className = "portfolio-identity";
  const ticker = document.createElement("strong");
  ticker.textContent = position.ticker;
  const account = document.createElement("span");
  account.textContent = (position.accounts || []).join(", ") || "Sin cuenta";
  identity.append(ticker, account);
  if (position.allocation_percent !== null) {
    const allocation = document.createElement("small");
    allocation.textContent = `${formatNumber(position.allocation_percent, 1)}% de inversiones`;
    identity.appendChild(allocation);
  }
  investment.appendChild(identity);

  const holding = portfolioCell("Tenencia", "number-cell");
  holding.appendChild(valueWithDetail(
    formatNumber(position.quantity, 6),
    position.average_cost === null ? "Costo no disponible" : `Costo prom. ${formatMoney(position.average_cost)} ${position.currency}`,
  ));

  const price = portfolioCell("Cotización", "number-cell");
  if (quote) {
    price.appendChild(valueWithDetail(
      `${formatMoney(quote.price)} ${quote.currency || "USD"}`,
      `${providerLabel(quote.provider)} · ${formatMarketTimestamp(quote.market_timestamp)}`,
    ));
  } else {
    price.appendChild(valueWithDetail("—", position.error || "Sin cotización"));
  }

  const daily = portfolioCell("Hoy", "number-cell");
  daily.appendChild(changeBlock(position.daily_change, position.daily_change_percent));

  const result = portfolioCell("Resultado", "number-cell");
  result.appendChild(changeBlock(position.unrealized_change, position.unrealized_change_percent));

  const value = portfolioCell("Valor actual", "number-cell portfolio-value");
  value.appendChild(valueWithDetail(
    position.market_value === null ? "—" : `${formatMoney(position.market_value)} USD`,
    position.cost_basis === null ? "Sin base comparable" : `Invertido ${formatMoney(position.cost_basis)} ${position.currency}`,
  ));

  row.append(investment, holding, price, daily, result, value);
  return row;
}

function portfolioCell(label, className = "") {
  const cell = document.createElement("td");
  cell.dataset.label = label;
  cell.className = className;
  return cell;
}

function valueWithDetail(value, detail) {
  const wrapper = document.createElement("div");
  wrapper.className = "portfolio-cell-value";
  const main = document.createElement("strong");
  main.textContent = value;
  const secondary = document.createElement("small");
  secondary.textContent = detail;
  wrapper.append(main, secondary);
  return wrapper;
}

function changeBlock(amount, percent) {
  const wrapper = valueWithDetail(
    amount === null ? "—" : `${formatSignedMoney(amount)} USD`,
    percent === null ? "Sin comparación" : formatSignedPercent(percent),
  );
  wrapper.classList.add(changeClass(amount));
  return wrapper;
}

function setPortfolioMetric(id, value, format) {
  const element = document.getElementById(id);
  if (value === null || value === undefined) {
    element.textContent = "—";
    element.className = "";
    return;
  }
  element.textContent = format === "signed-money"
    ? `${formatSignedMoney(value)} USD`
    : `${formatMoney(value)} USD`;
  element.className = format === "signed-money" ? changeClass(value) : "";
}

function formatNumber(value, maximumFractionDigits = 2) {
  return new Intl.NumberFormat("es-AR", { maximumFractionDigits }).format(Number(value));
}

function formatMoney(value) {
  return new Intl.NumberFormat("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
}

function formatSignedMoney(value) {
  const numeric = Number(value);
  if (Math.abs(numeric) < 0.005) return "0,00";
  return `${numeric > 0 ? "+" : "−"}${formatMoney(Math.abs(numeric))}`;
}

function formatSignedPercent(value) {
  const numeric = Number(value);
  if (Math.abs(numeric) < 0.005) return "0,00%";
  return `${numeric > 0 ? "+" : "−"}${formatNumber(Math.abs(numeric), 2)}%`;
}

function changeClass(value) {
  if (value === null || value === undefined || Math.abs(Number(value)) < 0.005) return "neutral-change";
  return Number(value) > 0 ? "positive-change" : "negative-change";
}

function providerLabel(provider) {
  return ({
    twelve_data: "Twelve Data",
    finnhub: "Finnhub",
    tiingo: "Tiingo",
    fmp: "FMP",
  })[provider] || provider || "Fuente desconocida";
}

function formatMarketTimestamp(value) {
  if (!value) return "sin fecha";
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00Z` : value;
  return new Intl.DateTimeFormat("es-AR", { dateStyle: "medium" }).format(new Date(normalized));
}

async function loadResearchHistory() {
  const response = await apiFetch("/api/research?limit=10");
  if (!response.ok) throw new Error("No se pudo cargar la investigación guardada.");
  const payload = await response.json();
  const reports = payload.reports || [];
  researchReports.clear();
  reports.forEach((report) => researchReports.set(String(report.id), report));
  document.getElementById("research-count").textContent = reports.length;
  renderResearchHistory(reports);
}

async function loadFreeSourceCatalog() {
  const response = await apiFetch("/api/research/sources");
  if (!response.ok) throw new Error("No se pudo cargar el catálogo de fuentes.");
  const payload = await response.json();
  const container = document.getElementById("free-source-catalog");
  container.replaceChildren();
  (payload.sources || []).forEach((source) => {
    const item = document.createElement(source.url ? "a" : "div");
    item.className = "free-source-item";
    if (source.url) {
      item.href = source.url;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    }
    const heading = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = source.name;
    const access = document.createElement("small");
    const integrated = source.integration === "web" || (source.integration === "runtime_api" && source.configured);
    access.className = integrated ? "source-ready" : "source-optional";
    access.textContent = source.integration === "runtime_api"
      ? `${source.configured ? "Integrada" : "Falta configurar"} · ${source.access}`
      : source.integration === "web"
        ? source.access
        : `API opcional · ${source.access}`;
    heading.append(name, access);
    const use = document.createElement("p");
    use.textContent = source.use;
    item.append(heading, use);
    container.appendChild(item);
  });
}

function renderResearchHistory(reports) {
  const container = document.getElementById("research-history");
  container.replaceChildren();
  if (!reports.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Todavía no hay investigaciones guardadas.";
    container.appendChild(empty);
    return;
  }
  reports.forEach((report) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "research-history-item";
    button.dataset.reportId = report.id;
    const title = document.createElement("strong");
    title.textContent = report.query;
    const detail = document.createElement("span");
    const completion = researchCompletionInfo(report);
    detail.textContent = `${completion.shortLabel} · ${researchTypeLabel(report.research_type)}${report.include_advice ? " · con orientación" : ""} · ${formatResearchDate(report.created_at)}`;
    button.classList.toggle("incomplete", completion.status === "incomplete");
    button.append(title, detail);
    button.addEventListener("click", () => renderResearch(researchReports.get(String(report.id))));
    container.appendChild(button);
  });
}

function renderResearch(report) {
  if (!report) return;
  document.getElementById("research-result-title").textContent = report.query;
  const completion = researchCompletionInfo(report);
  const completionBadge = document.getElementById("research-completion-status");
  completionBadge.className = `report-status ${completion.status}`;
  completionBadge.textContent = completion.label;
  const duration = report.duration_ms ? ` · ${formatDuration(report.duration_ms)}` : "";
  const advice = report.include_advice ? " · orientación solicitada" : "";
  document.getElementById("research-meta").textContent = `${researchTypeLabel(report.research_type)} · ${formatResearchDate(report.created_at)}${duration}${advice} · ${report.model}`;

  const text = report.answer || report.message || "";
  const citations = report.citations || [];
  const sources = report.sources || [];
  const citationReferences = buildCitationReferences(citations, sources);
  const answer = document.getElementById("research-answer");
  answer.classList.remove("empty-result");
  answer.replaceChildren();
  if (completion.status === "incomplete") {
    const warning = document.createElement("div");
    warning.className = "report-warning";
    warning.textContent = completion.detail;
    answer.appendChild(warning);
  }
  renderResearchAnswer(answer, text, citations, citationReferences);

  const sourceSection = document.getElementById("research-sources-section");
  const sourceTitle = document.getElementById("research-sources-title");
  const sourceList = document.getElementById("research-sources");
  const sourceNote = document.getElementById("research-source-note");
  sourceList.replaceChildren();
  const displayedSources = citationReferences.length
    ? citationReferences
    : sources.slice(0, 12).map((source, index) => sourceReference(source, index + 1));
  sourceSection.classList.toggle("hidden", !displayedSources.length);
  sourceTitle.textContent = citationReferences.length ? "Fuentes citadas" : "Fuentes consultadas";
  sourceNote.textContent = citationReferences.length
    ? `${citationReferences.length} fuentes citadas en el texto · ${sources.length} consultadas durante la búsqueda.`
    : `${sources.length} fuentes consultadas durante la búsqueda.`;
  displayedSources.forEach((source) => {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const number = document.createElement("span");
    number.className = "source-number";
    number.textContent = source.number;
    const copy = document.createElement("div");
    copy.className = "source-copy";
    const label = document.createElement("strong");
    label.textContent = source.title;
    const domain = document.createElement("small");
    domain.textContent = source.domain;
    copy.append(label, domain);
    link.append(number, copy);
    sourceList.appendChild(link);
  });
  setResearchRunState(
    completion.status === "completed" ? "success" : "warning",
    completion.status === "completed" ? "Investigación terminada y guardada" : "Investigación incompleta",
    completion.detail,
    report.duration_ms,
  );
  document.querySelector(".research-result-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function researchCompletionInfo(report) {
  if (report.status === "incomplete") {
    const hitLimit = ["max_tokens", "max_output_tokens"].includes(report.incomplete_reason);
    return {
      status: "incomplete",
      label: "⚠ Incompleta",
      shortLabel: "Incompleta",
      detail: hitLimit
        ? "OpenAI alcanzó el límite de salida. El texto puede terminar cortado; conviene repetir una consulta más acotada."
        : `OpenAI no marcó el informe como terminado (${report.incomplete_reason || "motivo no informado"}).`,
    };
  }
  return {
    status: "completed",
    label: "✓ Terminada",
    shortLabel: "Terminada",
    detail: "OpenAI confirmó la respuesta completa y el informe quedó guardado en la base local.",
  };
}

function setResearchRunState(state, title, detail, durationMs = null) {
  const container = document.getElementById("research-run-status");
  container.className = `research-run-status ${state}`;
  document.getElementById("research-run-title").textContent = title;
  document.getElementById("research-run-detail").textContent = detail;
  document.getElementById("research-elapsed").textContent = durationMs === null
    ? "—"
    : formatDuration(durationMs);
}

function formatDuration(milliseconds) {
  const seconds = Math.max(0, Math.round(Number(milliseconds || 0) / 1000));
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function researchPhase(seconds) {
  if (seconds < 6) return "Preparando la consulta y el contexto de cartera.";
  if (seconds < 22) return "Consultando fuentes y datos recientes.";
  if (seconds < 45) return "Contrastando hechos, consenso y riesgos.";
  return "Redactando el informe y verificando sus citas.";
}

function buildCitationReferences(citations, sources) {
  const sourceByUrl = new Map(sources.map((source) => [source.url, source]));
  const referenceByUrl = new Map();
  citations.forEach((citation) => {
    if (!citation.url || referenceByUrl.has(citation.url)) return;
    const source = sourceByUrl.get(citation.url) || citation;
    referenceByUrl.set(
      citation.url,
      sourceReference({
        url: citation.url,
        title: bestSourceTitle(citation.title, source.title, citation.url),
      }, referenceByUrl.size + 1),
    );
  });
  return [...referenceByUrl.values()];
}

function sourceReference(source, number) {
  let domain = "fuente externa";
  try {
    domain = new URL(source.url).hostname.replace(/^www\./, "");
  } catch (_error) {
    // La URL ya fue validada por el servidor; este fallback mantiene la UI legible.
  }
  return {
    number,
    url: source.url,
    title: bestSourceTitle(source.title, "", source.url) || domain,
    domain,
  };
}

function bestSourceTitle(primary, secondary, url) {
  const candidates = [primary, secondary]
    .map((value) => String(value || "").trim())
    .filter((value) => value && value !== url && !/^https?:\/\//i.test(value));
  return candidates.sort((left, right) => right.length - left.length)[0] || "";
}

function renderResearchAnswer(container, text, citations, references) {
  const referenceByUrl = new Map(references.map((reference) => [reference.url, reference]));
  const valid = citations
    .filter((item) => Number.isInteger(item.start_index) && Number.isInteger(item.end_index) && item.end_index > item.start_index && item.start_index >= 0 && item.end_index <= text.length)
    .sort((left, right) => left.start_index - right.start_index);
  let annotated = "";
  let cursor = 0;
  valid.forEach((citation) => {
    if (citation.start_index < cursor) return;
    annotated += text.slice(cursor, citation.start_index);
    const citedText = text.slice(citation.start_index, citation.end_index);
    const reference = referenceByUrl.get(citation.url);
    annotated += citationTextReplacement(citedText, reference?.number);
    cursor = citation.end_index;
  });
  annotated += text.slice(cursor);
  renderMarkdownBlocks(container, annotated, references);
  decorateResearchSections(container);
}

function citationTextReplacement(fragment, number) {
  if (!number) return fragment;
  const token = `\uE000${number}\uE001`;
  const withoutMarkdownCitation = fragment
    .replace(/\s*\(\s*\[[^\]]+\]\(https?:\/\/[^)\s]+\)\s*\)/gi, "")
    .replace(/\s*\[[^\]]+\]\(https?:\/\/[^)\s]+\)/gi, "");
  if (withoutMarkdownCitation !== fragment) {
    return `${withoutMarkdownCitation.trimEnd()}${token}`;
  }
  if (/^\s*[([{<]?https?:\/\/\S+[)\]}>.,;:]?\s*$/i.test(fragment)) return token;
  return `${fragment}${token}`;
}

function renderMarkdownBlocks(container, text, references) {
  const lines = text.split(/\r?\n/);
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(heading[1].length <= 2 ? "h3" : "h4");
      if (/opinión orientativa/i.test(heading[2])) element.classList.add("advice-heading");
      if (/zonas? de precio/i.test(heading[2])) element.classList.add("price-zone-heading");
      appendInlineMarkdown(element, heading[2], references);
      container.appendChild(element);
      index += 1;
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (bullet || numbered) {
      const list = document.createElement(numbered ? "ol" : "ul");
      const pattern = numbered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-*]\s+(.+)$/;
      while (index < lines.length) {
        const itemMatch = lines[index].match(pattern);
        if (!itemMatch) break;
        const item = document.createElement("li");
        appendInlineMarkdown(item, itemMatch[1], references);
        list.appendChild(item);
        index += 1;
      }
      container.appendChild(list);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(?:#{1,6}\s+|\s*[-*]\s+|\s*\d+[.)]\s+)/.test(lines[index])) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, paragraphLines.join(" "), references);
    container.appendChild(paragraph);
  }
}

function decorateResearchSections(container) {
  container.querySelectorAll("h3.price-zone-heading").forEach((heading) => {
    const section = document.createElement("section");
    section.className = "price-zone-section";
    heading.before(section);
    let current = heading;
    while (current && !(current !== heading && current.matches?.("h3"))) {
      const next = current.nextSibling;
      section.appendChild(current);
      current = next;
    }
  });
}

function appendInlineMarkdown(container, text, references) {
  const referenceByNumber = new Map(references.map((reference) => [reference.number, reference]));
  const pattern = /(\uE000(\d+)\uE001|\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\*([^*\n]+)\*)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[2]) {
      const reference = referenceByNumber.get(Number(match[2]));
      if (reference) container.appendChild(createCitationBadge(reference));
    } else if (match[3]) {
      const strong = document.createElement("strong");
      strong.textContent = match[3];
      container.appendChild(strong);
    } else if (match[4] && match[5]) {
      const link = document.createElement("a");
      link.className = "inline-source-link";
      link.href = match[5];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[4];
      container.appendChild(link);
    } else if (match[6]) {
      const emphasis = document.createElement("em");
      emphasis.textContent = match[6];
      container.appendChild(emphasis);
    }
    cursor = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(cursor)));
}

function createCitationBadge(reference) {
  const link = document.createElement("a");
  link.className = "citation-badge";
  link.href = reference.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `${reference.number}. ${reference.title}`;
  link.setAttribute("aria-label", `Fuente ${reference.number}: ${reference.title}`);
  link.textContent = reference.number;
  return link;
}

function researchTypeLabel(value) {
  return ({
    asset: "Activo",
    opportunities: "Oportunidades",
    portfolio: "Cartera",
    question: "Pregunta",
  })[value] || "Investigación";
}

function formatResearchDate(value) {
  if (!value) return "sin fecha";
  return new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

async function runResearch(query, type) {
  const clean = query.trim();
  if (!clean) {
    researchQuery.focus();
    return;
  }
  researchSubmit.disabled = true;
  researchSubmit.textContent = "Investigando…";
  const startedAt = Date.now();
  document.title = `Investigando… · ${defaultDocumentTitle}`;
  setResearchRunState("running", "Investigación en curso", researchPhase(0), 0);
  const progressTimer = window.setInterval(() => {
    const elapsedMs = Date.now() - startedAt;
    setResearchRunState(
      "running",
      "Investigación en curso",
      researchPhase(Math.floor(elapsedMs / 1000)),
      elapsedMs,
    );
  }, 1000);
  document.getElementById("research-result-title").textContent = "Buscando y contrastando fuentes…";
  const completionBadge = document.getElementById("research-completion-status");
  completionBadge.className = "report-status neutral";
  completionBadge.textContent = "En curso";
  document.getElementById("research-meta").textContent = "Esperando respuesta de OpenAI";
  const answer = document.getElementById("research-answer");
  answer.className = "research-answer empty-result loading-result";
  answer.textContent = "GPT está consultando información reciente y preparando un informe auditable.";
  try {
    const response = await apiFetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        query: clean,
        research_type: type,
        include_advice: researchAdvice.checked,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo completar la investigación.");
    window.clearInterval(progressTimer);
    renderResearch(result);
    if (result.status === "incomplete") {
      document.title = `⚠ Investigación incompleta · ${defaultDocumentTitle}`;
      addMessage("assistant", `La investigación #${result.id} quedó guardada, pero OpenAI no la marcó como completa. La sección Investigación muestra el motivo.`);
    } else {
      document.title = `✓ Investigación terminada · ${defaultDocumentTitle}`;
      addMessage("assistant", `La investigación #${result.id} terminó y quedó guardada. El informe completo está en Investigación.`);
    }
    await loadResearchHistory();
  } catch (error) {
    document.title = `Error de investigación · ${defaultDocumentTitle}`;
    document.getElementById("research-result-title").textContent = "No se pudo completar";
    completionBadge.className = "report-status incomplete";
    completionBadge.textContent = "Error";
    document.getElementById("research-meta").textContent = "Error de investigación";
    answer.className = "research-answer empty-result";
    answer.textContent = error.message;
    setResearchRunState("error", "La investigación falló", error.message, Date.now() - startedAt);
  } finally {
    window.clearInterval(progressTimer);
    researchSubmit.disabled = false;
    researchSubmit.textContent = "Investigar con fuentes";
  }
}

function renderHistory(items) {
  const container = document.getElementById("history");
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Todavía no hay movimientos confirmados.";
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";
    const type = document.createElement("span");
    type.className = "history-type";
    type.textContent = item.movement_type.slice(0, 3).toUpperCase();
    const main = document.createElement("div");
    main.className = "history-main";
    const title = document.createElement("strong");
    title.textContent = `${item.ticker || item.currency} · ${item.account}`;
    const detail = document.createElement("span");
    const amount = item.quantity ?? item.cash_amount ?? "—";
    detail.textContent = `${amount} · ${item.currency}`;
    main.append(title, detail);
    const date = document.createElement("span");
    date.className = "history-date";
    date.textContent = item.trade_date;
    row.append(type, main, date);
    container.appendChild(row);
  });
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.querySelectorAll("[data-message]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.message));
});

confirmButton.addEventListener("click", () => sendMessage("confirmar"));
cancelButton.addEventListener("click", () => sendMessage("cancelar"));
document.getElementById("help-button").addEventListener("click", () => sendMessage("ayuda"));
quoteRefresh.addEventListener("click", () => loadPortfolioValuation(true));

document.getElementById("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const response = await apiFetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.fromEntries(form.entries())),
  });
  const result = await response.json();
  addMessage("assistant", result.message || "Configuración guardada.");
  await refreshDashboard();
});

researchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runResearch(researchQuery.value, researchType.value);
});

document.querySelectorAll("[data-research-query]").forEach((button) => {
  button.addEventListener("click", () => {
    researchType.value = button.dataset.researchType;
    researchQuery.value = button.dataset.researchQuery;
    researchForm.requestSubmit();
  });
});

logoutButton.addEventListener("click", async () => {
  logoutButton.disabled = true;
  try {
    await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  } finally {
    window.location.replace("/login");
  }
});

Promise.all([loadSession(), refreshDashboard(), loadResearchHistory(), loadFreeSourceCatalog()]).catch((error) => {
  document.getElementById("system-note").textContent = `No pude leer el estado: ${error.message}`;
});

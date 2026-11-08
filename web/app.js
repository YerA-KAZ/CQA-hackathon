import {buildChecklistEditor} from "./checklist.js";
import {addRecording} from "./audio.js";

const label = value => ({yes: "Да", no: "Нет", "n/a": "Не применимо"})[value] ?? value ?? "—";
const channelName = channel => channel === "voice call" ? "Звонок" : "Чат";
const languageName = language => ({RU: "Русский", KZ: "Казахский", EN: "Английский"})[language] ?? language;
const number = value => Number(value).toLocaleString("ru-RU", {maximumFractionDigits: 2});

function element(tag, text, parent) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  parent?.append(node);
  return node;
}

async function request(path, method = "GET", body, signal) {
  const response = await fetch("/api" + path, {
    method,
    signal,
    headers: body === undefined ? {} : {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch(error => {
    if (error.name === "AbortError") throw error;
    throw new Error("Нет связи с сервером. Повторите действие.");
  });
  const data = await response.json().catch(error => {
    if (error.name === "AbortError") throw error;
    throw new Error("Сервер вернул неожиданный ответ.");
  });
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Проверьте введённые данные.");
  return data;
}

class CqaApp extends HTMLElement {
  connectedCallback() {
    const header = element("header", null, this);
    header.className = "masthead";
    const brand = element("div", null, header);
    brand.className = "brand";
    element("span", "CQA", brand).className = "brand-mark";
    element("strong", "Freedom Broker", brand);
    element("span", "Контроль качества", brand).className = "brand-context";
    const main = element("main", null, this);
    const heading = element("div", null, main);
    heading.className = "page-heading";
    const title = element("div", null, heading);
    element("h1", "Рабочее место контроля качества", title);
    element("p", "Проверка диалогов, оценок и апелляций", title).className = "muted";
    const toolbar = element("form", null, heading);
    toolbar.className = "toolbar";
    const groupLabel = element("label", "Код набора", toolbar);
    const group = element("input", null, groupLabel);
    group.value = "DEV";
    group.required = true;
    const load = element("button", "Загрузить набор", toolbar);
    load.className = "primary";
    toolbar.addEventListener("submit", event => {
      event.preventDefault();
      this.run(async () => {
        const code = group.value.trim();
        if (!code) throw new Error("Введите код набора диалогов.");
        const result = await request("/datasets/" + encodeURIComponent(code), "POST");
        await this.refresh();
        return "Загружено диалогов: " + result.imported;
      });
    });
    this.status = element("div", null, main);
    this.status.className = "status";
    this.status.setAttribute("role", "status");

    const editor = element("details", null, main);
    editor.className = "checklist panel";
    element("summary", "Редактор чек-листа", editor);
    const editorBody = element("div", null, editor);
    editorBody.className = "editor-body";
    element("p", "Измените критерии и сохраните новую версию, чтобы пересчитать оценки диалогов.", editorBody).className = "muted";
    this.editor = buildChecklistEditor(editorBody, element);
    this.button(editorBody, "Сохранить и пересчитать", async () => {
      const result = await request("/checklist", "PUT", this.editor.read());
      this.showDiff(result.diff);
      await this.refresh(true);
      return "Сохранена версия " + result.version;
    }).className = "primary";
    this.diff = element("div", null, editorBody);
    this.diff.className = "diff";

    const layout = element("div", null, main);
    layout.className = "layout";
    const sidebar = element("aside", null, layout);
    sidebar.className = "sidebar panel";
    element("h2", "Диалоги", sidebar);
    const filters = element("div", null, sidebar);
    filters.className = "filters";
    this.search = element("input", null, element("label", "Поиск", filters));
    this.search.type = "search";
    this.search.placeholder = "По коду диалога";
    this.filter = element("select", null, element("label", "Статус оценки", filters));
    for (const [value, name] of [["all", "Все диалоги"], ["pending", "Без оценки"], ["review", "Требуют проверки"], ["scored", "С баллом"]]) {
      element("option", name, this.filter).value = value;
    }
    this.search.addEventListener("input", () => this.renderList());
    this.filter.addEventListener("change", () => this.renderList());
    this.listCount = element("p", null, sidebar);
    this.listCount.className = "list-count";
    this.listCount.setAttribute("role", "status");
    this.list = element("nav", null, sidebar);
    this.list.setAttribute("aria-label", "Список диалогов");
    this.detail = element("section", null, layout);
    this.detail.className = "dialog panel";
    const empty = element("div", null, this.detail);
    empty.className = "empty detail-empty";
    element("h2", "Диалог не выбран", empty);
    element("p", "Выберите диалог из списка, чтобы открыть расшифровку и оценку.", empty);
    this.renderList();
    this.run(() => this.refresh(true));
  }

  async run(action) {
    if (this.busy) return;
    this.busy = true;
    this.status.textContent = "Выполняется…";
    this.status.className = "status working";
    this.querySelectorAll("button:not(.stop)").forEach(button => { button.disabled = true; });
    try {
      this.status.textContent = await action() ?? "";
    } catch (error) {
      this.status.textContent = error.message;
      this.status.className = "status error";
    } finally {
      this.querySelectorAll("button:not(.stop)").forEach(button => { button.disabled = false; });
      this.status.classList.remove("working");
      this.busy = false;
      if (!this.rows) this.renderList();
    }
  }

  button(parent, text, action) {
    const button = element("button", text, parent);
    button.type = "button";
    button.addEventListener("click", () => this.run(action));
    return button;
  }

  async refresh(loadChecklist = false) {
    if (loadChecklist || !this.checklist) {
      this.checklist = await request("/checklist");
      this.editor.load(this.checklist);
    }
    this.rows = await request("/conversations");
    this.renderList();
    if (this.selected) await this.open(this.selected);
  }

  renderList() {
    const query = this.search.value.trim().toLowerCase();
    const rows = (this.rows ?? []).filter(row => {
      const status = row.unreliable ? "review" : row.total != null ? "scored" : "pending";
      return row.conversation_id.toLowerCase().includes(query) &&
        (this.filter.value === "all" || this.filter.value === status);
    });
    this.list.replaceChildren();
    if (!this.rows) {
      element("p", this.status.classList.contains("error") ? "Список недоступен." : "Загружаем список…", this.list).className = "empty";
      return;
    }
    this.listCount.textContent = "Показано " + rows.length + " из " + (this.rows?.length ?? 0);
    if (!rows.length) element("p", this.rows.length
      ? "Совпадений нет. Измените поиск или фильтр."
      : "Загрузите набор, чтобы увидеть диалоги.", this.list).className = "empty";
    for (const row of rows) {
      const button = this.button(this.list, null, async () => {
        await this.open(row.conversation_id);
        this.detail.querySelector("h2").focus();
      });
      button.className = "dialog-row";
      button.dataset.id = row.conversation_id;
      button.setAttribute("aria-current", row.conversation_id === this.selected ? "true" : "false");
      button.disabled = Boolean(this.busy);
      element("strong", row.conversation_id, button);
      element("span", channelName(row.channel) + " · " + languageName(row.language), button).className = "row-meta";
      const badges = element("span", null, button);
      badges.className = "badges";
      const status = row.unreliable ? "Требует проверки" : row.total != null ? number(row.total) + " / 100" : "Без оценки";
      element("span", status, badges).className = "badge " + (row.unreliable ? "warning" : row.total != null ? "positive" : "");
      if (row.flag === "yes") element("span", "Грубая ошибка", badges).className = "badge negative";
    }
  }
  async open(id) {
    const path = "/conversations/" + encodeURIComponent(id);
    const {conversation, assessment} = await request(path);
    this.selected = id;
    for (const button of this.list.querySelectorAll("button")) {
      button.setAttribute("aria-current", String(button.dataset.id === id));
    }
    this.detail.querySelector("audio")?.pause();
    this.detail.replaceChildren();
    const top = element("div", null, this.detail);
    top.className = "detail-heading";
    const identity = element("div", null, top);
    element("p", "Карточка диалога", identity).className = "eyebrow";
    element("h2", id, identity).tabIndex = -1;
    element("p", channelName(conversation.channel) + " · " + languageName(conversation.language) + " · Реплик: " + conversation.transcript.length, identity).className = "muted";
    const actions = element("div", null, top);
    actions.className = "actions";
    const stop = element("button", "Остановить", actions);
    stop.hidden = true;
    stop.className = "stop";
    this.button(actions, assessment ? "Оценить повторно" : "Оценить диалог", async () => {
      const controller = new AbortController();
      this.status.textContent = "Анализ диалога…";
      stop.hidden = false;
      stop.focus();
      stop.onclick = () => controller.abort();
      try {
        await request(path + "/assess", "POST", undefined, controller.signal);
        await this.refresh();
      } catch (error) {
        if (error.name === "AbortError") return "Анализ остановлен.";
        throw error;
      } finally {
        stop.hidden = true;
        stop.onclick = null;
      }
    }).className = "primary";
    const player = conversation.audio_url ? addRecording(this.detail, conversation, element) : null;
    const columns = element("div", null, this.detail);
    columns.className = "review-layout";
    const transcriptPanel = element("div", null, columns);
    transcriptPanel.className = "review-pane transcript-pane";
    element("h3", "Расшифровка", transcriptPanel);
    const evaluation = element("div", null, columns);
    evaluation.className = "review-pane evaluation";
    element("h3", "Оценка качества", evaluation);
    const overview = element("div", null, evaluation);
    overview.className = "score-overview";
    const scoreInfo = element("div", null, overview);
    if (!assessment) element("p", "Запустите оценку, чтобы увидеть результаты по критериям.", scoreInfo).className = "empty";
    if (assessment?.unreliable) element("p", "Оценка недостоверна", scoreInfo).className = "badge warning";
    if (assessment?.total != null && !assessment.unreliable) {
      const score = Number(assessment.total);
      const band = score >= 80 ? "Высокий" : score >= 60 ? "Средний" : "Низкий";
      const level = score >= 80 ? "high" : score >= 60 ? "medium" : "low";
      const ring = element("div", null, overview);
      ring.className = "score-ring " + level;
      ring.style.setProperty("--progress", Math.max(0, Math.min(100, score)) + "%");
      const center = element("div", null, ring);
      element("strong", number(score), center);
      element("span", "из 100", center);
      element("p", band + " балл", scoreInfo).className = "score-caption";
    } else if (assessment && !assessment.unreliable) {
      element("p", "Итоговый балл не рассчитан", scoreInfo);
    }
    if (assessment) element("p", "Грубые ошибки: " + label(assessment.flag), scoreInfo).className = assessment.flag === "yes" ? "badge negative" : "muted";
    const transcript = element("div", null, transcriptPanel);
    transcript.className = "transcript";
    const roles = {operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система"};
    const recordingStart = player ? Date.parse(
      conversation.transcript.find(line =>
        line.speaker === "system" && /запись и транскрибирование запущены|recording and transcription (?:have )?(?:started|been started)/i.test(line.text)
      )?.timestamp ?? conversation.transcript[0]?.timestamp
    ) : NaN;
    let timingCorrection = 0;
    let timingInput;
    if (player) {
      const label = element("label", "Сдвиг подсветки, сек. ", transcriptPanel);
      label.className = "timing";
      transcriptPanel.insertBefore(label, transcript);
      timingInput = element("input", null, label);
      timingInput.type = "number";
      timingInput.step = "0.1";
      timingInput.value = "0";
      element("small", "Плюс — раньше, минус — позже", label);
    }
    const offsets = player ? conversation.transcript.map(line =>
      (Date.parse(line.timestamp) - recordingStart) / 1000
    ) : [];
    const lines = conversation.transcript.map((line, index) => {
      const seconds = offsets[index];
      const time = Number.isFinite(seconds) && seconds >= 0
        ? Math.floor(seconds / 60) + ":" + String(Math.floor(seconds % 60)).padStart(2, "0")
        : line.timestamp;
      const row = element("div", null, transcript);
      row.className = "transcript-line";
      row.title = line.timestamp;
      row.dataset.speaker = line.speaker;
      row.tabIndex = -1;
      const meta = element("div", null, row);
      meta.className = "line-meta";
      element("strong", roles[line.speaker], meta);
      element("span", time, meta).className = "line-time";
      if (Number.isFinite(offsets[index])) this.button(meta, "Слушать", async () => {
        const seconds = offsets[index];
        player.currentTime = Math.max(0, seconds - timingCorrection);
        await player.play();
      }).className = "compact";
      element("p", line.text, row);
      return row;
    });
    if (!lines.length) element("p", "В диалоге нет реплик.", transcript).className = "empty";
    if (player) {
      const timeline = offsets.map((seconds, index) => ({seconds, index}))
        .filter(entry => Number.isFinite(entry.seconds))
        .sort((a, b) => a.seconds - b.seconds);
      let activeRow;
      let isPlaying = false;
      const highlight = () => {
        const entry = timeline.findLast(item => item.seconds <= player.currentTime + timingCorrection);
        const row = isPlaying && !player.seeking ? lines[entry?.index] : undefined;
        if (row === activeRow) return;
        activeRow?.classList.remove("playing-line");
        activeRow?.removeAttribute("aria-current");
        activeRow = row;
        if (!row) return;
        row.classList.add("playing-line");
        row.setAttribute("aria-current", "true");
        const viewport = transcript.getBoundingClientRect();
        const bounds = row.getBoundingClientRect();
        if (bounds.top < viewport.top || bounds.bottom > viewport.bottom) {
          transcript.scrollTop += bounds.top - viewport.top;
        }
      };
      timingInput.addEventListener("input", () => {
        timingCorrection = Number.isFinite(timingInput.valueAsNumber) ? timingInput.valueAsNumber : 0;
        highlight();
      });
      player.addEventListener("playing", () => {
        isPlaying = true;
        highlight();
      });
      player.addEventListener("seeked", () => {
        isPlaying = !player.paused && !player.ended && player.readyState >= 3;
        highlight();
      });
      player.addEventListener("timeupdate", highlight);
      for (const event of ["waiting", "seeking", "pause", "ended", "error", "emptied"]) {
        player.addEventListener(event, () => {
          isPlaying = false;
          highlight();
        });
      }
    }
    for (const item of assessment?.criteria ?? []) {
      const criterion = this.checklist.find(entry => entry.id === item.criterion_id);
      if (!criterion) continue;
      const article = element("article", null, evaluation);
      article.className = "criterion";
      const heading = element("div", null, article);
      heading.className = "criterion-heading";
      element("h4", criterion.name, heading);
      element("span", "Вес " + number(criterion.weight), heading).className = "badge";
      const answers = element("dl", null, article);
      answers.className = "answers";
      const finalAnswer = assessment.final_answers?.[criterion.id] ?? item.result?.answer;
      for (const [name, value, unreliable] of [
        ["Исходная оценка", item.result?.answer, item.unreliable],
        ["Финальная оценка", finalAnswer, false],
      ]) {
        const answer = element("div", null, answers);
        element("dt", name, answer);
        element("dd", unreliable ? "Недостоверно" : label(value), answer).className =
          "badge " + (unreliable ? "warning" : value === "yes" ? "positive" : value === "no" ? "negative" : "");
      }
      if (item.unreliable && item.failure_reason) element("p", item.failure_reason, article).className = "notice";
      if (item.result) {
        element("h5", "Обоснование", article);
        element("p", item.result.reasoning, article).className = "reasoning";
        if (item.result.na_reason) element("p", item.result.na_reason, article).className = "muted";
        const indices = new Set(item.result.evidence_indices);
        if (item.result.checked_range) for (const index of item.result.checked_range) indices.add(index);
        const quotes = new Map();
        for (const evidence of item.result.evidence ?? []) {
          indices.add(evidence.index);
          if (!quotes.has(evidence.index)) quotes.set(evidence.index, new Set());
          quotes.get(evidence.index).add(evidence.quote);
        }
        if (indices.size) {
          const evidence = element("div", null, article);
          evidence.className = "evidence";
          element("h5", "Доказательства", evidence);
          for (const index of indices) {
            const reference = element("div", null, evidence);
            const jump = element(lines[index] ? "a" : "span", "Реплика " + (index + 1), reference);
            if (lines[index]) {
              lines[index].id = "replica-" + (index + 1);
              jump.href = "#" + lines[index].id;
              jump.addEventListener("click", event => {
                event.preventDefault();
                lines[index].focus();
              });
            }
            for (const quote of quotes.get(index) ?? []) element("blockquote", quote, reference);
          }
        }
      }
      this.appeal(article, path, criterion, assessment.appeals?.[criterion.id]);
    }
  }

  appeal(article, path, criterion, appeal) {
    const endpoint = path + "/appeals/" + encodeURIComponent(criterion.id);
    const panel = element(appeal ? "div" : "details", null, article);
    panel.className = "appeal";
    if (!appeal) {
      element("summary", "Подать апелляцию", panel);
      const comment = element("textarea", null, element("label", "Комментарий к апелляции", panel));
      comment.rows = 2;
      this.button(panel, "Отправить апелляцию", async () => {
        await request(endpoint, "POST", {comment: comment.value.trim()});
        await this.refresh();
      });
      return;
    }
    element("h5", "Апелляция", panel);
    element("p", appeal.decision ? "Решение принято" : "Ожидает решения", panel).className = "badge " + (appeal.decision ? "positive" : "warning");
    element("p", appeal.comment, panel).className = "reasoning";
    if (appeal.decision) {
      element("p", "Решение: " + label(appeal.decision.answer), panel).className = "decision";
      element("p", appeal.decision.reason, panel).className = "reasoning";
      return;
    }
    const select = element("select", null, element("label", "Решение проверяющего", panel));
    const values = criterion.answer_type === "scale" ? criterion.scale_steps.map(step => step.value) : ["yes", "no"];
    if (criterion.na_allowed) values.push("n/a");
    for (const value of values) element("option", label(value), select);
    const reason = element("textarea", null, element("label", "Причина решения", panel));
    reason.rows = 2;
    this.button(panel, "Сохранить решение", async () => {
      await request(endpoint, "PUT", {answer: values[select.selectedIndex], reason: reason.value.trim()});
      await this.refresh();
    });
  }

  showDiff(diff) {
    this.diff.replaceChildren();
    if (!diff.length) {
      element("p", "Баллы не изменились", this.diff);
      return;
    }
    const table = element("table", null, this.diff);
    element("caption", "Изменения итоговых баллов", table);
    const header = element("tr", null, element("thead", null, table));
    for (const name of ["Диалог", "Было", "Стало", "Разница"]) element("th", name, header).scope = "col";
    const body = element("tbody", null, table);
    for (const item of diff) {
      const row = element("tr", null, body);
      element("th", item.conversation_id, row).scope = "row";
      for (const value of [item.before, item.after, item.delta]) element("td", value == null ? "—" : number(value), row);
    }
  }
}

customElements.define("cqa-app", CqaApp);

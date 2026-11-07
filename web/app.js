import {buildChecklistEditor} from "./checklist.js";
import {addRecording} from "./audio.js";

const label = value => ({yes: "Да", no: "Нет", "n/a": "Не применимо"})[value] ?? value ?? "—";

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
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Проверьте данные");
  return data;
}

class CqaApp extends HTMLElement {
  connectedCallback() {
    element("header", "Freedom Broker · Контроль качества", this);
    const main = element("main", null, this);
    const toolbar = element("section", null, main);
    const group = element("input", null, toolbar);
    group.value = "DEV";
    group.setAttribute("aria-label", "Группа диалогов");
    this.button(toolbar, "Загрузить набор", async () => {
      const result = await request("/datasets/" + encodeURIComponent(group.value.trim()), "POST");
      await this.refresh();
      return "Загружено диалогов: " + result.imported;
    });
    this.status = element("p", null, main);
    this.status.setAttribute("role", "status");

    const editor = element("details", null, main);
    element("summary", "Редактор чек-листа", editor);
    this.editor = buildChecklistEditor(editor, element);
    this.button(editor, "Сохранить и пересчитать", async () => {
      const result = await request("/checklist", "PUT", this.editor.read());
      this.showDiff(result.diff);
      await this.refresh(true);
      return "Сохранена версия " + result.version;
    });
    this.diff = element("div", null, editor);

    const layout = element("div", null, main);
    layout.className = "layout";
    this.list = element("aside", null, layout);
    this.detail = element("section", "Выберите диалог", layout);
    this.run(() => this.refresh(true));
  }

  async run(action) {
    if (this.busy) return;
    this.busy = true;
    this.setAttribute("aria-busy", "true");
    this.status.textContent = "Выполняется…";
    try {
      this.status.textContent = await action() ?? "";
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.busy = false;
      this.setAttribute("aria-busy", "false");
    }
  }

  button(parent, text, action) {
    const button = element("button", text, parent);
    button.type = "button";
    button.addEventListener("click", () => this.run(action));
  }

  async refresh(loadChecklist = false) {
    if (loadChecklist) {
      this.checklist = await request("/checklist");
      this.editor.load(this.checklist);
    }
    const rows = await request("/conversations");
    this.list.replaceChildren();
    element("h2", "Диалоги", this.list);
    for (const row of rows) {
      const status = row.unreliable ? "Недостоверно" : row.total ?? "Без оценки";
      this.button(this.list, [row.conversation_id, row.language, status, row.flag === "yes" ? "Грубая ошибка" : ""].filter(Boolean).join(" · "),
        () => this.open(row.conversation_id));
    }
    if (this.selected) await this.open(this.selected);
  }

  async open(id) {
    const path = "/conversations/" + encodeURIComponent(id);
    const {conversation, assessment} = await request(path);
    this.selected = id;
    this.detail.replaceChildren();
    element("h2", id, this.detail);
    const actions = element("div", null, this.detail);
    const stop = element("button", "Стоп", actions);
    stop.hidden = true;
    stop.className = "stop";
    this.button(actions, "Оценить", async () => {
      const controller = new AbortController();
      stop.hidden = false;
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
    });
    if (assessment) {
      if (assessment.unreliable) element("p", "Оценка недостоверна", this.detail);
      element("p", "Грубые ошибки: " + label(assessment.flag), this.detail);
    }
    const player = conversation.audio_url ? addRecording(this.detail, conversation, element) : null;
    const columns = element("div", null, this.detail);
    columns.className = "review-layout";
    const transcriptPanel = element("div", null, columns);
    element("h3", "Расшифровка", transcriptPanel);
    const evaluation = element("div", null, columns);
    element("h3", "Оценка качества", evaluation);
    if (assessment?.total != null && !assessment.unreliable) {
      const score = Number(assessment.total);
      const band = score >= 80 ? "Высокий" : score >= 60 ? "Средний" : "Низкий";
      const level = score >= 80 ? "high" : score >= 60 ? "medium" : "low";
      const ring = element("div", null, evaluation);
      ring.className = "score-ring " + level;
      ring.style.setProperty("--progress", Math.max(0, Math.min(100, score)) + "%");
      const center = element("div", null, ring);
      element("strong", score.toLocaleString("ru-RU", {maximumFractionDigits: 2}), center);
      element("span", "из 100", center);
      element("p", band + " балл", evaluation).className = "score-caption";
    } else if (assessment && !assessment.unreliable) {
      element("p", "Итоговый балл не рассчитан", evaluation);
    }
    const transcript = element("div", null, transcriptPanel);
    transcript.className = "transcript";
    const roles = {operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система"};
    const lines = conversation.transcript.map((line) => {
      const row = element("p", line.timestamp + " · " + roles[line.speaker] + "\n" + line.text, transcript);
      row.tabIndex = -1;
      if (player) this.button(row, "Слушать", async () => {
        const seconds = (Date.parse(line.timestamp) - Date.parse(conversation.transcript[0].timestamp)) / 1000;
        if (Number.isFinite(seconds)) player.currentTime = Math.max(0, seconds);
        await player.play();
      });
      return row;
    });
    for (const item of assessment?.criteria ?? []) {
      const criterion = this.checklist.find(entry => entry.id === item.criterion_id);
      if (!criterion) continue;
      const article = element("article", null, evaluation);
      element("h3", criterion.name + " · вес " + criterion.weight, article);
      element("p", "Исходная оценка: " + (item.unreliable ? "Недостоверно" : label(item.result?.answer)), article);
      element("p", "Финальная оценка: " + label(assessment.final_answers?.[criterion.id] ?? item.result?.answer), article);
      if (item.result) {
        element("p", item.result.reasoning, article);
        if (item.result.na_reason) element("p", item.result.na_reason, article);
        const indices = new Set(item.result.evidence_indices);
        if (item.result.checked_range) for (const index of item.result.checked_range) indices.add(index);
        for (const index of indices) this.button(article, "Реплика " + (index + 1), () => lines[index]?.focus());
      }
      this.appeal(article, path, criterion, assessment.appeals?.[criterion.id]);
    }
  }

  appeal(article, path, criterion, appeal) {
    const endpoint = path + "/appeals/" + encodeURIComponent(criterion.id);
    if (!appeal) {
      const comment = element("input", null, article);
      comment.setAttribute("aria-label", "Комментарий к апелляции");
      this.button(article, "Подать апелляцию", async () => {
        await request(endpoint, "POST", {comment: comment.value.trim()});
        await this.refresh();
      });
      return;
    }
    element("p", "Апелляция: " + appeal.comment, article);
    if (appeal.decision) {
      element("p", "Решение: " + label(appeal.decision.answer) + " · " + appeal.decision.reason, article);
      return;
    }
    element("p", "Ожидает решения", article);
    const select = element("select", null, article);
    select.setAttribute("aria-label", "Решение проверяющего");
    const values = criterion.answer_type === "scale" ? criterion.scale_steps.map(step => step.value) : ["yes", "no"];
    if (criterion.na_allowed) values.push("n/a");
    for (const value of values) element("option", label(value), select);
    const reason = element("input", null, article);
    reason.setAttribute("aria-label", "Причина решения");
    this.button(article, "Сохранить решение", async () => {
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
    const header = element("tr", null, table);
    for (const name of ["Диалог", "Было", "Стало", "Разница"]) element("th", name, header);
    for (const item of diff) {
      const row = element("tr", null, table);
      for (const value of [item.conversation_id, item.before, item.after, item.delta]) element("td", label(value), row);
    }
  }
}

customElements.define("cqa-app", CqaApp);

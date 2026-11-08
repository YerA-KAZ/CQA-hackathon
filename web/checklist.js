export function buildChecklistEditor(container, make) {
  const toolbar = make("div", null, container);
  const toggle = make("button", "Режим JSON", toolbar);
  const add = make("button", "Добавить критерий", toolbar);
  const form = make("div", null, container);
  const json = make("textarea", null, container);
  json.rows = 16;
  json.hidden = true;
  json.setAttribute("aria-label", "Чек-лист в формате JSON");
  const error = make("p", null, container);
  error.setAttribute("role", "alert");
  let draft = [];

  function read() {
    const value = json.hidden ? draft : JSON.parse(json.value);
    if (!Array.isArray(value) || value.some(item => !item || typeof item !== "object" || Array.isArray(item))) {
      throw new Error("Укажите массив объектов критериев.");
    }
    return value;
  }

  function field(parent, title, value, update, options = {}) {
    const label = make("label", title, parent);
    const input = make(options.choices ? "select" : options.multiline ? "textarea" : "input", null, label);
    if (options.choices) {
      for (const [key, name] of options.choices) make("option", name, input).value = key;
    } else if (!options.multiline) {
      input.type = options.type || "text";
      if (input.type === "number") input.min = "0";
    }
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
    input.addEventListener("input", () => {
      update(input.type === "checkbox" ? input.checked :
        input.type === "number" ? (input.value === "" ? null : input.valueAsNumber) : input.value);
    });
    return input;
  }

  function render(openIndex = -1) {
    form.replaceChildren();
    draft.forEach((item, index) => {
      const card = make("details", null, form);
      card.open = index === openIndex;
      const title = () => [item.id, item.name].filter(Boolean).join(" · ") || "Новый критерий";
      const summary = make("summary", title(), card);
      const grid = make("div", null, card);
      grid.className = "editor-fields";
      const bind = key => value => {
        item[key] = value;
        if (key === "description") item.sub_questions = [];
        summary.textContent = title();
      };
      field(grid, "Код", item.id, bind("id"));
      field(grid, "Название", item.name, bind("name"));
      field(grid, "Раздел", item.section, bind("section"));
      field(grid, "Вес", item.weight, bind("weight"), {type: "number"}).disabled = item.answer_type === "flag";
      field(grid, "Тип ответа", item.answer_type, type => {
        item.answer_type = type;
        item.sub_questions = [];
        if (type === "flag") item.weight = 0;
        item.scale_steps = type === "scale" ? [{value: 0, description: ""}, {value: 10, description: ""}] : [];
        render(index);
      }, {choices: [["yes_no", "Да / Нет"], ["scale", "Шкала"], ["flag", "Флаг"]]});
      field(card, "Описание", item.description, bind("description"), {multiline: true});
      field(card, "Разрешить «Не применимо»", item.na_allowed, value => {
        item.na_allowed = value;
        if (!value) item.na_condition = null;
        render(index);
      }, {type: "checkbox"});
      if (item.na_allowed) field(card, "Условие применимости N/A", item.na_condition, bind("na_condition"));
      if (item.sub_questions?.length) make("p", "Используются подвопросы из JSON. Изменение описания или шкалы переключит критерий на прямую оценку.", card);
      if (item.answer_type === "scale") {
        make("h4", "Ступени шкалы", card);
        const steps = item.scale_steps ??= [];
        steps.forEach((step, stepIndex) => {
          const row = make("div", null, card);
          row.className = "editor-fields";
          field(row, "Балл", step.value, value => { step.value = value; item.sub_questions = []; }, {type: "number"});
          field(row, "Описание ступени", step.description, value => { step.description = value; item.sub_questions = []; });
          make("button", "Удалить ступень", row).onclick = () => { steps.splice(stepIndex, 1); item.sub_questions = []; render(index); };
        });
        make("button", "Добавить ступень", card).onclick = () => {
          steps.push({value: 0, description: ""}); item.sub_questions = [];
          render(index);
        };
      }
      make("button", "Удалить критерий", card).onclick = () => { draft.splice(index, 1); render(); };
    });
  }

  add.onclick = () => {
    draft.push({id: "", name: "", description: "", section: "", version: 1,
      weight: 0, answer_type: "yes_no", na_allowed: false, na_condition: null, scale_steps: []});
    render(draft.length - 1);
    const newCard = form.lastElementChild;
    newCard.scrollIntoView({block: "nearest"});
    newCard.querySelector("input").focus({preventScroll: true});
  };
  toggle.onclick = () => {
    error.textContent = "";
    try {
      if (json.hidden) json.value = JSON.stringify(draft, null, 2);
      else draft = read();
      json.hidden = !json.hidden;
      form.hidden = add.hidden = !json.hidden;
      toggle.textContent = json.hidden ? "Режим JSON" : "Вернуться к полям";
      if (json.hidden) render();
    } catch (failure) {
      error.textContent = failure.message;
    }
  };
  return {
    read,
    load(value) {
      draft = structuredClone(value);
      json.value = JSON.stringify(draft, null, 2);
      render();
    },
  };
}

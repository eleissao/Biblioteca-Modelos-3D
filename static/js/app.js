import {
  createProject,
  deleteModel,
  deleteProject,
  fetchProjects,
  formatBytes,
  renameModel,
  renameProject,
  uploadModel,
} from "./api.js";
import { ModelViewer, createPreviewViewer } from "./viewer.js";

const app = document.querySelector("#app");
const dialogRoot = document.querySelector("#dialog-root");
const managerRoot = document.querySelector("#manager-root");
const toastEl = document.querySelector("#toast");

let projects = [];
let previewPool = null;
let currentKey = null;
let activeDialog = null;
let toastTimer = null;
let closeManager = null;

const kindLabels = {
  blend: "BLEND",
  fbx: "FBX",
  glb: "GLB",
  gltf: "GLTF",
};

const kindNames = {
  blend: "Blender",
  fbx: "Autodesk FBX",
  glb: "glTF binário",
  gltf: "glTF",
};

function convertedKind(kind) {
  return kind === "blend" || kind === "fbx";
}


function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function decodeSegment(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function projectHash(project) {
  return `#/p/${encodeURIComponent(project.id)}`;
}

function modelHash(project, model) {
  return `${projectHash(project)}/m/${encodeURIComponent(model.id)}`;
}

function modelPathHash(project, relativePath) {
  return `${projectHash(project)}/m/${encodeURIComponent(relativePath)}`;
}

function routeFromHash() {
  const hash = window.location.hash || "#/";
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const parts = raw.split("/").filter(Boolean);

  if (parts[0] !== "p") {
    return { view: "projects" };
  }

  const projectId = decodeSegment(parts[1] || "");
  if (parts[2] === "m" && parts[3]) {
    return {
      view: "project",
      projectId,
      modelId: decodeSegment(parts.slice(3).join("/")),
    };
  }
  return { view: "project", projectId };
}

function countLabel(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function showToast(message, type = "error") {
  toastEl.textContent = message;
  toastEl.className = `toast is-visible${type === "error" ? " is-error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastEl.className = "toast";
  }, 5000);
}

function actionButton(label, { ghost = false, danger = false, onClick } = {}) {
  const node = el(
    "button",
    `button${ghost ? " ghost" : ""}${danger ? " danger" : ""}`,
    label,
  );
  node.type = "button";
  if (onClick) node.addEventListener("click", onClick);
  return node;
}

function openManager({
  title,
  description,
  fields = [],
  submitLabel = "Salvar",
  danger = false,
  onSubmit,
}) {
  closeManager?.({ restoreFocus: false });

  const previousFocus = document.activeElement;
  const overlay = el("div", "dialog-backdrop manager-backdrop");
  const panel = el("section", "dialog form-dialog");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-labelledby", "form-dialog-title");

  const heading = el("div", "form-heading");
  heading.append(
    el("p", "eyebrow", "Gerenciar"),
    el("h2", "dialog-title", title),
  );
  if (description) heading.append(el("p", "form-description", description));

  const closeButton = el("button", "form-close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Fechar");
  closeButton.title = "Fechar (Esc)";

  const header = el("header", "form-head");
  header.append(heading, closeButton);

  const form = el("form", "form-body");
  const fieldsWrap = el("div", "form-fields");
  const controls = new Map();

  for (const field of fields) {
    const id = `field-${field.name}`;
    const label = el("label", "form-label", field.label);
    label.htmlFor = id;

    let input;
    if (field.type === "file") {
      input = el("input", "form-input form-file");
      input.type = "file";
      if (field.accept) input.accept = field.accept;
      input.multiple = Boolean(field.multiple);
    } else {
      input = el("input", "form-input");
      input.type = "text";
      input.value = field.value || "";
      input.autocomplete = "off";
      input.maxLength = 200;
      if (field.placeholder) input.placeholder = field.placeholder;
    }
    input.id = id;
    input.name = field.name;
    input.required = Boolean(field.required);

    const row = el("div", "form-field");
    row.append(label, input);
    if (field.help) row.append(el("p", "form-help", field.help));
    fieldsWrap.append(row);
    controls.set(field.name, { input, field });
  }

  const error = el("p", "form-error");
  error.hidden = true;

  const cancel = actionButton("Cancelar", { ghost: true });
  const submit = actionButton(submitLabel, { danger });
  submit.type = "submit";

  const footer = el("div", "form-actions");
  footer.append(cancel, submit);

  form.append(fieldsWrap, error, footer);
  panel.append(header, form);
  overlay.append(panel);
  managerRoot.replaceChildren(overlay);
  document.body.classList.add("modal-open");
  updatePause();

  const close = ({ restoreFocus = true } = {}) => {
    if (managerRoot.firstChild !== overlay) return;
    document.removeEventListener("keydown", onKeydown);
    overlay.remove();
    managerRoot.replaceChildren();
    document.body.classList.remove("modal-open");
    if (closeManager === close) closeManager = null;
    updatePause();
    if (
      restoreFocus &&
      previousFocus &&
      typeof previousFocus.focus === "function" &&
      document.contains(previousFocus)
    ) {
      previousFocus.focus({ preventScroll: true });
    }
  };

  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const focusables = [
      ...panel.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((node) => !node.hidden && node.offsetParent !== null);

    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  document.addEventListener("keydown", onKeydown);
  closeButton.addEventListener("click", () => close());
  cancel.addEventListener("click", () => close());
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;

    const values = {};
    try {
      for (const [name, { input, field }] of controls) {
        if (field.type === "file") {
          values[name] = Array.from(input.files || []);
        } else {
          values[name] = input.value.trim();
          if (field.required && !values[name]) {
            throw new Error(`Preencha ${field.label.toLowerCase()}.`);
          }
        }
      }
    } catch (validationError) {
      error.textContent = validationError.message;
      error.hidden = false;
      return;
    }

    submit.disabled = true;
    cancel.disabled = true;
    submit.textContent = "Salvando…";
    try {
      await onSubmit(values);
      close({ restoreFocus: false });
    } catch (submitError) {
      error.textContent = submitError.message || String(submitError);
      error.hidden = false;
      submit.disabled = false;
      cancel.disabled = false;
      submit.textContent = submitLabel;
    }
  });

  closeManager = close;
  const first = fields.length ? panel.querySelector(".form-input") : submit;
  first?.focus();
  if (first && first.select) first.select();
}

async function reloadProjects(navigate) {
  const next = await fetchProjects();
  projects = next;
  destroyPreviews();
  currentKey = null;
  if (navigate !== undefined) {
    history.replaceState(null, "", navigate);
  }
  applyRoute();
}

function openCreateProject() {
  openManager({
    title: "Novo projeto",
    description:
      "O projeto será criado como uma pasta com models/ dentro de projects/.",
    fields: [
      {
        name: "name",
        label: "Nome do projeto",
        placeholder: "Ex.: Minha coleção",
        required: true,
      },
    ],
    submitLabel: "Criar projeto",
    onSubmit: async ({ name }) => {
      const result = await createProject(name);
      await reloadProjects(projectHash({ id: result.project_id }));
      showToast("Projeto criado.", "info");
    },
  });
}

function openRenameProject(project) {
  openManager({
    title: "Renomear projeto",
    description: `A pasta ${project.id}/ será renomeada. Os modelos continuam iguais.`,
    fields: [
      {
        name: "name",
        label: "Novo nome",
        value: project.name,
        required: true,
      },
    ],
    submitLabel: "Renomear",
    onSubmit: async ({ name }) => {
      const result = await renameProject(project.id, name);
      await reloadProjects(projectHash({ id: result.project_id }));
      showToast("Projeto renomeado.", "info");
    },
  });
}

function confirmDeleteProject(project) {
  openManager({
    title: "Excluir projeto",
    description: `A pasta ${project.id}/ e todos os modelos dentro dela serão apagados. Esta ação não pode ser desfeita.`,
    submitLabel: "Excluir projeto",
    danger: true,
    onSubmit: async () => {
      await deleteProject(project.id);
      await reloadProjects("#/");
      showToast("Projeto excluído.", "info");
    },
  });
}

function openUploadModel(project) {
  openManager({
    title: "Adicionar modelo",
    description:
      "Envie um modelo por vez. Para .gltf, selecione também o .bin e as texturas usadas por ele. Para .fbx, inclua as texturas externas, se houver.",
    fields: [
      {
        name: "files",
        label: "Arquivos do modelo",
        type: "file",
        accept:
          ".blend,.fbx,.glb,.gltf,.bin,.png,.jpg,.jpeg,.webp,.avif,.gif,.ktx2,.basis,.tga,.dds,.bmp,.tif,.tiff,.exr,.hdr",
        multiple: true,
        required: true,
        help: "O arquivo principal define o nome da pasta do modelo.",
      },
    ],
    submitLabel: "Enviar modelo",
    onSubmit: async ({ files }) => {
      if (!files.length) {
        throw new Error("Selecione ao menos um arquivo.");
      }
      const primaries = files.filter((file) =>
        /\.(blend|fbx|glb|gltf)$/i.test(file.name),
      );
      if (primaries.length !== 1) {
        throw new Error(
          "Selecione exatamente um arquivo .blend, .fbx, .glb ou .gltf, junto com os arquivos auxiliares.",
        );
      }
      await uploadModel(project.id, files);
      await reloadProjects(projectHash(project));
      showToast("Modelo adicionado.", "info");
    },
  });
}

function openRenameModel(project, model) {
  openManager({
    title: "Renomear modelo",
    description:
      "O nome do arquivo será alterado. Se a pasta tiver o mesmo nome e contiver só esse modelo, ela também será renomeada.",
    fields: [
      {
        name: "name",
        label: "Novo nome",
        value: model.name,
        required: true,
      },
    ],
    submitLabel: "Renomear",
    onSubmit: async ({ name }) => {
      const result = await renameModel(project.id, model.id, name);
      await reloadProjects(modelPathHash(project, result.path));
      showToast("Modelo renomeado.", "info");
    },
  });
}

function confirmDeleteModel(project, model) {
  openManager({
    title: "Excluir modelo",
    description: `O arquivo ${model.id} será apagado. Se a pasta do modelo ficar sem modelos, ela também será removida.`,
    submitLabel: "Excluir modelo",
    danger: true,
    onSubmit: async () => {
      await deleteModel(project.id, model.id);
      await reloadProjects(projectHash(project));
      showToast("Modelo excluído.", "info");
    },
  });
}

function projectActions(project) {
  const actions = el("div", "view-actions");
  actions.append(
    actionButton("Adicionar modelo", { onClick: () => openUploadModel(project) }),
    actionButton("Renomear", {
      ghost: true,
      onClick: () => openRenameProject(project),
    }),
    actionButton("Excluir", {
      ghost: true,
      danger: true,
      onClick: () => confirmDeleteProject(project),
    }),
  );
  return actions;
}

function loadingView(text) {
  const wrap = el("div", "loading-view");
  wrap.append(el("span", "spinner large"), el("p", "", text));
  return wrap;
}

function emptyState(title, description, codeText) {
  const wrap = el("section", "empty-state");
  wrap.append(el("div", "empty-icon", "◈"), el("h2", "", title));
  if (description) wrap.append(el("p", "", description));
  if (codeText) {
    const pre = el("pre");
    pre.append(el("code", "", codeText));
    wrap.append(pre);
  }
  return wrap;
}

function viewHeader({
  eyebrow,
  title,
  subtitle,
  backHref,
  backLabel,
  statValue,
  statLabel,
  actions,
}) {
  const section = el("section", "view-head");
  const heading = el("div", "view-heading");

  if (backHref) {
    const back = el("a", "back-link", `← ${backLabel}`);
    back.href = backHref;
    heading.append(back);
  }

  heading.append(el("p", "eyebrow", eyebrow), el("h1", "", title));
  if (subtitle) heading.append(el("p", "lede", subtitle));
  if (actions) heading.append(actions);

  section.append(heading);

  if (statValue !== undefined) {
    const stat = el("div", "view-stat");
    stat.append(el("strong", "", String(statValue)), el("span", "", statLabel));
    section.append(stat);
  }

  return section;
}

function previewStatus(text) {
  const status = el("div", "preview-status");
  status.append(el("span", "spinner"), el("span", "preview-status-text", text));
  return status;
}

function cardPreview(model, statusText) {
  const preview = el("div", "card-preview");
  preview.append(el("span", "kind-badge", kindLabels[model.kind] || model.kind));

  if (model) {
    const status = previewStatus(
      statusText || "Aguardando prévia…",
    );
    preview.append(status);
    previewPool?.add(preview, model, status);
  }

  return preview;
}

class PreviewPool {
  constructor() {
    this.entries = new Map();
    this.paused = false;
    this.observer = new IntersectionObserver(
      (observed) => {
        for (const item of observed) {
          const entry = this.entries.get(item.target);
          if (!entry) continue;
          entry.visible = item.isIntersecting;
          if (item.isIntersecting) {
            this.start(entry);
          } else {
            this.stop(entry);
          }
        }
      },
      { rootMargin: "0px", threshold: 0.9 },
    );
  }

  add(container, model, status) {
    const entry = {
      container,
      model,
      status,
      viewer: null,
      failed: false,
      visible: false,
    };
    this.entries.set(container, entry);
    this.observer.observe(container);
    this.setStatus(entry, "Aguardando prévia…");
  }

  setStatus(entry, text, { error = false } = {}) {
    if (!entry.status) return;
    entry.status.hidden = false;
    entry.status.classList.toggle("is-error", error);
    const label = entry.status.querySelector(".preview-status-text");
    const spinner = entry.status.querySelector(".spinner");
    if (label) label.textContent = text;
    if (spinner) spinner.hidden = error;
  }

  start(entry) {
    if (entry.failed) return;
    if (entry.viewer) {
      entry.viewer.setActive(true);
      return;
    }

    this.setStatus(
      entry,
      convertedKind(entry.model.kind)
        ? `Convertendo .${entry.model.kind}…`
        : "Carregando prévia…",
    );

    let viewer;
    try {
      viewer = createPreviewViewer(entry.container, entry.model, {
        autoRotate: true,
        onProgress: (ratio) => {
          this.setStatus(
            entry,
            ratio === null
              ? "Carregando prévia…"
              : `Carregando ${Math.round(ratio * 100)}%`,
          );
        },
        onReady: () => {
          if (entry.status) entry.status.hidden = true;
        },
        onError: (error) => {
          entry.failed = true;
          this.setStatus(entry, "Prévia indisponível", { error: true });
          if (entry.status) entry.status.title = error.message || String(error);
          entry.viewer?.dispose();
          entry.viewer = null;
        },
      });
    } catch (error) {
      entry.failed = true;
      this.setStatus(entry, "WebGL indisponível", { error: true });
      if (entry.status) entry.status.title = error.message || String(error);
      return;
    }

    entry.viewer = viewer;
    viewer.setActive(true);
    viewer.setPaused(this.paused);
    viewer.load();
  }

  stop(entry) {
    entry.viewer?.setActive(false);
  }

  setPaused(paused) {
    this.paused = Boolean(paused);
    for (const entry of this.entries.values()) {
      entry.viewer?.setPaused(this.paused);
    }
  }

  clear() {
    this.observer.disconnect();
    for (const entry of this.entries.values()) {
      entry.viewer?.dispose();
    }
    this.entries.clear();
    this.paused = false;
  }
}

function destroyPreviews() {
  if (previewPool) {
    previewPool.clear();
    previewPool = null;
  }
}

function renderProjectCard(project) {
  const card = el("a", "card project-card");
  card.href = projectHash(project);
  card.setAttribute("aria-label", `Abrir projeto ${project.name}`);

  const preview = el("div", "card-preview");
  preview.append(el("span", "kind-badge", "PROJETO"));

  const firstModel = project.models[0];
  if (firstModel) {
    const status = previewStatus("Aguardando prévia…");
    preview.append(status);
    previewPool?.add(preview, firstModel, status);
  } else {
    const empty = el("div", "preview-empty");
    empty.append(el("span", "preview-empty-icon", "◈"), el("span", "", "Sem modelos"));
    preview.append(empty);
  }

  const body = el("div", "card-body");
  body.append(el("h2", "card-title", project.name));

  const meta = el("div", "card-meta");
  meta.append(
    el("span", "badge", countLabel(project.model_count, "modelo", "modelos")),
  );
  body.append(meta);

  card.append(preview, body);
  return card;
}

function renderModelCard(project, model) {
  const card = el("a", "card model-card");
  card.href = modelHash(project, model);
  card.title = model.id;
  card.setAttribute("aria-label", `Abrir ${model.name} no visualizador 3D`);

  card.append(cardPreview(model));

  const body = el("div", "card-body");
  body.append(el("h2", "card-title", model.name));

  const meta = el("div", "card-meta");
  meta.append(el("span", "", formatBytes(model.size)));
  meta.append(el("span", "meta-sep", "·"));
  meta.append(el("span", "", kindNames[model.kind] || model.kind));
  body.append(meta);

  card.append(body);
  return card;
}

function renderProjects() {
  previewPool = new PreviewPool();

  const header = viewHeader({
    eyebrow: "Acervo",
    title: "Projetos",
    subtitle:
      "Cada projeto reúne modelos 3D em projects/<projeto>/models/. Clique em um projeto para ver os modelos.",
    statValue: projects.length,
    statLabel: projects.length === 1 ? "projeto" : "projetos",
    actions: (() => {
      const actions = el("div", "view-actions");
      actions.append(
        actionButton("Novo projeto", { onClick: openCreateProject }),
      );
      return actions;
    })(),
  });

  if (projects.length === 0) {
    app.replaceChildren(
      header,
      emptyState(
        "Nenhum projeto ainda",
        "Use o botão Novo projeto para criar a primeira pasta do acervo.",
        "projects/<nome-do-projeto>/models/<nome-do-modelo>/arquivo.blend",
      ),
    );
    return;
  }

  const grid = el("div", "grid projects");
  for (const project of projects) {
    grid.append(renderProjectCard(project));
  }
  app.replaceChildren(header, grid);
}

function renderProject(project) {
  previewPool = new PreviewPool();

  const header = viewHeader({
    backHref: "#/",
    backLabel: "Projetos",
    eyebrow: "Projeto",
    title: project.name,
    subtitle:
      project.model_count > 0
        ? "Clique em um modelo para abrir o visualizador 3D. Arraste para girar e use a roda para aproximar."
        : "Esta pasta ainda não tem modelos. Use o botão Adicionar modelo para enviar um arquivo.",
    statValue: project.model_count,
    statLabel: project.model_count === 1 ? "modelo" : "modelos",
    actions: projectActions(project),
  });

  if (project.models.length === 0) {
    app.replaceChildren(
      header,
      emptyState(
        "Pasta de modelos vazia",
        "Use o botão Adicionar modelo ou coloque um arquivo .blend, .fbx, .glb ou .gltf dentro de models/.",
        `projects/${project.id}/models/`,
      ),
    );
    return;
  }

  const grid = el("div", "grid models");
  for (const model of project.models) {
    grid.append(renderModelCard(project, model));
  }
  app.replaceChildren(header, grid);
}

function renderNotFound(projectId) {
  previewPool = null;
  app.replaceChildren(
    viewHeader({
      backHref: "#/",
      backLabel: "Voltar aos projetos",
      eyebrow: "Não encontrado",
      title: "Projeto não encontrado",
      subtitle: `Não existe um projeto chamado "${projectId}".`,
    }),
    emptyState(
      "Confira a pasta projects/",
      "O projeto pode ter sido renomeado ou removido. Recarregue a página para atualizar a lista.",
      "projects/<nome-do-projeto>/models/",
    ),
  );
}

function renderFatal(error) {
  previewPool = null;
  const wrap = el("section", "empty-state");
  wrap.append(
    el("div", "empty-icon", "!"),
    el("p", "eyebrow", "Erro"),
    el("h2", "", "Não foi possível carregar a biblioteca"),
    el("p", "", error?.message || "Erro desconhecido."),
    el("p", "", "Verifique se o servidor está rodando com python3 server.py."),
  );

  const retry = el("button", "button", "Tentar novamente");
  retry.type = "button";
  retry.addEventListener("click", loadProjects);
  wrap.append(retry);
  app.replaceChildren(wrap);
}

async function loadProjects() {
  destroyPreviews();
  currentKey = null;
  app.replaceChildren(loadingView("Carregando acervo…"));

  try {
    projects = await fetchProjects();
  } catch (error) {
    renderFatal(error);
    return;
  }

  applyRoute();
}

function applyRoute() {
  if (managerRoot.childElementCount) {
    closeManager?.({ restoreFocus: false });
  }
  const route = routeFromHash();

  if (route.view === "projects") {
    closeDialog({ restoreFocus: false });
    if (currentKey !== "projects") {
      destroyPreviews();
      currentKey = "projects";
      renderProjects();
    }
    updatePause();
    return;
  }

  const project = projects.find((item) => item.id === route.projectId);
  if (!project) {
    closeDialog({ restoreFocus: false });
    destroyPreviews();
    currentKey = null;
    renderNotFound(route.projectId);
    return;
  }

  const key = `project:${project.id}`;
  if (currentKey !== key) {
    destroyPreviews();
    currentKey = key;
    renderProject(project);
  }

  if (route.modelId) {
    const model = project.models.find((item) => item.id === route.modelId);
    if (model) {
      openDialog(project, model);
    } else {
      showToast("Esse modelo não existe mais. A lista foi atualizada.");
      history.replaceState(null, "", projectHash(project));
      closeDialog({ restoreFocus: false });
    }
  } else {
    closeDialog({ restoreFocus: true });
  }

  updatePause();
}

function updatePause() {
  previewPool?.setPaused(
    document.hidden ||
      Boolean(activeDialog) ||
      Boolean(managerRoot.childElementCount),
  );
}

function openDialog(project, model) {
  if (
    activeDialog &&
    activeDialog.project.id === project.id &&
    activeDialog.model.id === model.id
  ) {
    return;
  }

  closeDialog({ restoreFocus: false, resume: false });

  const previousFocus = document.activeElement;
  const overlay = el("div", "dialog-backdrop");
  const dialog = el("div", "dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "dialog-title");

  const header = el("header", "dialog-head");
  const heading = el("div");
  heading.append(
    el("p", "eyebrow", project.name),
    el("h2", "dialog-title", model.name),
  );
  const actions = el("div", "view-actions dialog-actions");
  actions.append(
    actionButton("Renomear", {
      ghost: true,
      onClick: () => openRenameModel(project, model),
    }),
    actionButton("Excluir", {
      ghost: true,
      danger: true,
      onClick: () => confirmDeleteModel(project, model),
    }),
  );
  header.append(heading, actions);

  const stage = el("div", "dialog-stage");
  stage.id = "dialog-stage";
  const hint = el(
    "div",
    "dialog-hint",
    "Arraste para girar · Role para aproximar · X para fechar",
  );

  const close = el("button", "dialog-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Fechar visualizador");
  close.title = "Fechar (Esc)";

  const loader = el("div", "dialog-loader");
  const loadText = el(
    "p",
    "dialog-load-text",
    convertedKind(model.kind)
      ? `Convertendo .${model.kind} para GLB…`
      : "Carregando modelo…",
  );
  const loadNote = el(
    "p",
    "dialog-load-note",
    convertedKind(model.kind)
      ? "A primeira abertura pode levar alguns segundos."
      : "Preparando a cena 3D.",
  );
  const progress = el("div", "dialog-progress");
  const progressBar = el("span");
  progress.append(progressBar);
  progress.hidden = true;
  loader.append(el("span", "spinner large"), loadText, progress, loadNote);

  const errorPanel = el("div", "dialog-error");
  errorPanel.hidden = true;
  const errorTitle = el("h3", "", "Não foi possível abrir este modelo");
  const errorDetail = el("pre");
  const errorClose = el("button", "button ghost", "Fechar");
  errorClose.type = "button";
  errorPanel.append(errorTitle, errorDetail, errorClose);

  dialog.append(header, stage, hint, close, loader, errorPanel);
  overlay.append(dialog);
  dialogRoot.replaceChildren(overlay);
  document.body.classList.add("dialog-open");

  const onKeydown = (event) => {
    if (managerRoot.childElementCount) return;
    if (event.key === "Escape") {
      event.preventDefault();
      requestCloseDialog();
      return;
    }
    if (event.key !== "Tab") return;

    const focusables = [
      ...dialog.querySelectorAll(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((node) => !node.hidden && node.offsetParent !== null);

    if (focusables.length === 0) {
      event.preventDefault();
      close.focus();
      return;
    }

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  document.addEventListener("keydown", onKeydown);
  close.addEventListener("click", requestCloseDialog);
  errorClose.addEventListener("click", requestCloseDialog);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) requestCloseDialog();
  });

  let viewer = null;
  try {
    viewer = new ModelViewer(stage, model, {
      interactive: true,
      autoRotate: false,
      onProgress: (ratio) => {
        if (ratio === null) {
          loadText.textContent = "Carregando modelo…";
          return;
        }
        const percent = Math.round(ratio * 100);
        loadText.textContent = `Carregando modelo… ${percent}%`;
        progress.hidden = false;
        progressBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
      },
      onReady: () => {
        loader.hidden = true;
      },
      onError: (error) => {
        loader.hidden = true;
        errorPanel.hidden = false;
        errorDetail.textContent = error.message || String(error);
        errorClose.focus();
      },
    });
  } catch (error) {
    loader.hidden = true;
    errorPanel.hidden = false;
    errorDetail.textContent = error.message || String(error);
  }

  activeDialog = {
    project,
    model,
    overlay,
    viewer,
    previousFocus,
    onKeydown,
  };

  close.focus();
  previewPool?.setPaused(true);
  viewer?.load();
}

function closeDialog({ restoreFocus = true, resume = true } = {}) {
  if (!activeDialog) return;

  const { overlay, viewer, previousFocus, onKeydown } = activeDialog;
  activeDialog = null;

  document.removeEventListener("keydown", onKeydown);
  viewer?.dispose();
  overlay.remove();
  dialogRoot.replaceChildren();
  document.body.classList.remove("dialog-open");

  if (resume) updatePause();

  if (
    restoreFocus &&
    previousFocus &&
    typeof previousFocus.focus === "function" &&
    document.contains(previousFocus)
  ) {
    previousFocus.focus({ preventScroll: true });
  }
}

function requestCloseDialog() {
  const state = activeDialog;
  if (!state) return;

  closeDialog();
  const route = routeFromHash();
  if (route.modelId) {
    history.replaceState(null, "", projectHash(state.project));
  }
}

window.addEventListener("hashchange", applyRoute);
document.addEventListener("visibilitychange", updatePause);

loadProjects();

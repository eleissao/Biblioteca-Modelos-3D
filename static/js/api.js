async function errorMessage(response) {
  let message = `Erro HTTP ${response.status}`;
  try {
    const data = await response.json();
    message = data.error || message;
    if (data.detail) message += `\n\n${data.detail}`;
  } catch {
    /* resposta sem JSON */
  }
  return message;
}

async function requestJson(url, { method = "GET", body } = {}) {
  const response = await fetch(url, {
    method,
    cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchProjects() {
  const data = await requestJson("/api/projects");
  return Array.isArray(data.projects) ? data.projects : [];
}

export function createProject(name) {
  return requestJson("/api/projects", { method: "POST", body: { name } });
}

export function renameProject(project, name) {
  return requestJson("/api/projects/rename", {
    method: "POST",
    body: { project, name },
  });
}

export function deleteProject(project) {
  return requestJson("/api/projects/delete", {
    method: "POST",
    body: { project },
  });
}

export async function uploadModel(project, files) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  const response = await fetch(
    `/api/models/upload?project=${encodeURIComponent(project)}`,
    { method: "POST", cache: "no-store", body: form },
  );
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export function renameModel(project, path, name) {
  return requestJson("/api/models/rename", {
    method: "POST",
    body: { project, path, name },
  });
}

export function deleteModel(project, path) {
  return requestJson("/api/models/delete", {
    method: "POST",
    body: { project, path },
  });
}

export async function fetchArrayBuffer(url, { onProgress, signal } = {}) {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) {
    let message = `Erro HTTP ${response.status}`;
    try {
      const data = await response.json();
      message = data.error || message;
      if (data.detail) {
        message += `\n\n${data.detail}`;
      }
    } catch {
      /* resposta sem JSON */
    }
    throw new Error(message);
  }

  const total = Number(response.headers.get("content-length")) || 0;
  if (!response.body || !onProgress) {
    return response.arrayBuffer();
  }

  const reader = response.body.getReader();
  const chunks = [];
  let loaded = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    onProgress(total > 0 ? loaded / total : null);
  }

  const result = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result.buffer;
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** exponent;
  return `${new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: value >= 10 ? 1 : 2,
  }).format(value)} ${units[exponent]}`;
}

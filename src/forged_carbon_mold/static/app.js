const $ = (selector) => document.querySelector(selector);
const canvas = $("#viewer");
const ctx = canvas.getContext("2d");
const view = { azimuth: -0.65, elevation: 0.43, zoom: 1, dragging: false, lastX: 0, lastY: 0 };
let status = null;
let currentMode = "part";
let cached = {};

function message(text, error = false) {
  $("#status").textContent = text;
  $("#status").style.color = error ? "#ec9a59" : "";
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response;
}

function resize() {
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(bounds.width * ratio));
  canvas.height = Math.max(1, Math.round(bounds.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function draw() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  const data = cached[currentMode] || cached.part;
  if (!data?.vertices?.length) return;
  const vertices = data.vertices;
  const cosA = Math.cos(view.azimuth), sinA = Math.sin(view.azimuth);
  const cosE = Math.cos(view.elevation), sinE = Math.sin(view.elevation);
  const rotated = vertices.map(([x, y, z]) => {
    const u = x * cosA - y * sinA, v = x * sinA + y * cosA;
    return [u, v * cosE - z * sinE, v * sinE + z * cosE];
  });
  let maxExtent = 1;
  for (const point of rotated) {
    maxExtent = Math.max(maxExtent, Math.abs(point[0]), Math.abs(point[1]), Math.abs(point[2]));
  }
  const scale = Math.min(width, height) * 0.37 * view.zoom / maxExtent;
  const projected = rotated.map(([x, y, z]) => [width / 2 + x * scale, height / 2 - y * scale, z]);
  const triangles = data.triangles.map(indices => ({
    points: indices.map(index => projected[index]),
    depth: indices.reduce((sum, index) => sum + projected[index][2], 0) / 3,
  })).sort((a, b) => a.depth - b.depth);
  const color = currentMode === "part" ? [163, 223, 111] : [190, 202, 191];
  for (const triangle of triangles) {
    const shade = Math.min(1, Math.max(0.5, 0.65 + (triangle.depth / maxExtent) * 0.15));
    ctx.beginPath();
    triangle.points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath();
    ctx.fillStyle = `rgba(${color.map(channel => Math.round(channel * shade)).join(",")},0.88)`;
    ctx.fill();
    ctx.strokeStyle = "rgba(12,18,12,0.42)";
    ctx.lineWidth = 0.7;
    ctx.stroke();
  }
  ctx.font = "10px DM Mono, monospace";
  ctx.fillStyle = "#819080";
  ctx.fillText("X", 25, height - 31);
  ctx.fillStyle = "#7caf57";
  ctx.fillText("Y", 39, height - 31);
  ctx.fillStyle = "#a3b2a1";
  ctx.fillText("Z", 53, height - 31);
}

async function loadMesh(kind) {
  const response = await request(`/api/mesh/${kind}`);
  cached[kind] = await response.json();
  draw();
}

function showModel(model, name) {
  status = model;
  $("#model-name").textContent = name;
  $("#empty-state").classList.add("hidden");
  $("#dimensions").textContent = Object.entries(model.bounds_mm).map(([axis, value]) => `${axis.toUpperCase()} ${value.toFixed(1)}`).join(" · ") + " mm";
  $("#solid-count").textContent = `${model.solid_count} Volumenkörper · V ${(model.volume_mm3 / 1000).toFixed(1)} cm³`;
  $("#apply-transform").disabled = false;
  $("#build").disabled = false;
  $("#show-part").disabled = false;
  $("#split").value = $("#split").value || 0;
  currentMode = "part";
  $("#show-part").classList.add("selected");
  $("#show-mold").classList.remove("selected");
  loadMesh("part").catch(error => message(error.message, true));
}

async function importFile(file) {
  if (!file) return;
  message("STEP-Datei wird geprüft …");
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await (await request("/api/import", { method: "POST", body: form })).json();
    cached = {};
    showModel(result.model, file.name);
    $("#split").value = "0";
    await refreshStatus();
    message("STEP importiert und geprüft");
  } catch (error) { message(error.message, true); }
}

async function refreshStatus() {
  const response = await request("/api/status");
  const current = await response.json();
  if (current.part) showModel(current.part, current.source_name);
  $("#wall").value = current.parameters.wall_mm;
  $("#split").value = current.parameters.split_z ?? 0;
  for (const [key, value] of Object.entries(current.transform || {})) {
    const input = document.getElementById(key === "x" || key === "y" || key === "z" ? `t${key}` : key);
    if (input) input.value = value;
  }
  if (current.mold_ready) {
    $("#show-mold").disabled = false;
    for (const button of document.querySelectorAll("[data-export]")) button.disabled = false;
  }
}

$("#step-file").addEventListener("change", event => importFile(event.target.files[0]));
$("#demo").addEventListener("click", async () => {
  try {
    message("Referenzkörper wird erzeugt …");
    const response = await request("/api/demo", { method: "POST" });
    const result = await response.json();
    cached = {};
    showModel(result.model, "Referenzkörper.step");
    $("#split").value = "0";
    message("Referenzkörper bereit");
  } catch (error) { message(error.message, true); }
});

$("#apply-transform").addEventListener("click", async () => {
  const values = Object.fromEntries(["tx", "ty", "tz", "rx", "ry", "rz"].map(key => [key, Number($(`#${key}`).value)]));
  try {
    message("Transformation wird angewendet …");
    const result = await (await request("/api/transform", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x: values.tx, y: values.ty, z: values.tz, rx: values.rx, ry: values.ry, rz: values.rz }),
    })).json();
    cached = {};
    showModel(result.model, $("#model-name").textContent);
    $("#show-mold").disabled = true;
    for (const button of document.querySelectorAll("[data-export]")) button.disabled = true;
    $("#segments").innerHTML = '<span class="muted">Neu berechnen erforderlich</span>';
    message("Transformation angewendet — Form bitte neu berechnen");
  } catch (error) { message(error.message, true); }
});

$("#build").addEventListener("click", async () => {
  try {
    message("B-Rep-Form wird berechnet …");
    const response = await request("/api/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wall_mm: Number($("#wall").value), split_z: Number($("#split").value) }),
    });
    const result = await response.json();
    $("#segments").innerHTML = result.segments.map(segment =>
      `<article><span>${segment.name}</span><small>${segment.solid_count} Körper · ${(segment.volume_mm3 / 1000).toFixed(1)} cm³</small></article>`
    ).join("");
    $("#show-mold").disabled = false;
    for (const button of document.querySelectorAll("[data-export]")) button.disabled = false;
    $("#show-mold").click();
    message("Form gültig berechnet");
  } catch (error) { message(error.message, true); }
});

$("#show-part").addEventListener("click", async () => {
  currentMode = "part";
  $("#show-part").classList.add("selected");
  $("#show-mold").classList.remove("selected");
  try { await loadMesh("part"); } catch (error) { message(error.message, true); }
});
$("#show-mold").addEventListener("click", async () => {
  currentMode = "lower";
  $("#show-part").classList.remove("selected");
  $("#show-mold").classList.add("selected");
  try {
    await loadMesh("lower");
    const lower = cached.lower;
    const upperResponse = await request("/api/mesh/upper");
    const upper = await upperResponse.json();
    cached.lower = combineMeshes(lower, upper);
    currentMode = "lower";
    draw();
  } catch (error) { message(error.message, true); }
});

function combineMeshes(a, b) {
  const offset = a.vertices.length;
  return { vertices: [...a.vertices, ...b.vertices], triangles: [...a.triangles, ...b.triangles.map(face => face.map(i => i + offset))] };
}

for (const button of document.querySelectorAll("[data-export]")) {
  button.addEventListener("click", async () => {
    try {
      const response = await request(`/api/export/${button.dataset.export}`);
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `forged-carbon-${button.dataset.export}.step`;
      link.click();
      URL.revokeObjectURL(link.href);
      message("STEP exportiert und Rundlauf geprüft");
    } catch (error) { message(error.message, true); }
  });
}

$("#save-project").addEventListener("click", async () => {
  try {
    const response = await request("/api/project");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = "forged-carbon-project.fcm";
    link.click();
    URL.revokeObjectURL(link.href);
    message("Projekt gespeichert");
  } catch (error) { message(error.message, true); }
});
$("#open-project").addEventListener("click", () => $("#project-file").click());
$("#project-file").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await (await request("/api/project", { method: "POST", body: form })).json();
    cached = {};
    showModel(result.model, file.name);
    await refreshStatus();
    message("Projekt geöffnet — Form neu berechnen");
  } catch (error) { message(error.message, true); }
});

canvas.addEventListener("pointerdown", event => { view.dragging = true; view.lastX = event.clientX; view.lastY = event.clientY; canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener("pointermove", event => {
  if (!view.dragging) return;
  view.azimuth += (event.clientX - view.lastX) * 0.008;
  view.elevation = Math.max(-1.45, Math.min(1.45, view.elevation + (event.clientY - view.lastY) * 0.008));
  view.lastX = event.clientX; view.lastY = event.clientY; draw();
});
canvas.addEventListener("pointerup", () => { view.dragging = false; });
canvas.addEventListener("wheel", event => { event.preventDefault(); view.zoom = Math.max(0.3, Math.min(5, view.zoom * (event.deltaY < 0 ? 1.1 : 0.9))); draw(); }, { passive: false });
window.addEventListener("resize", resize);
resize();
refreshStatus().catch(error => message(error.message, true));

const $ = (selector) => document.querySelector(selector);
const canvas = $("#viewer");
const view = { azimuth: -0.65, elevation: 0.43, zoom: 1, dragging: false, lastX: 0, lastY: 0 };
const display = { transparent: false, grid: false };
const gl = canvas.getContext("webgl2", { antialias: true, alpha: true })
  || canvas.getContext("webgl", { antialias: true, alpha: true });
let renderer = null;
let edgeRenderer = null;
let currentMode = "part";
let cached = {};
let meshGeneration = 0;
const meshRequests = {};

function createRenderer(context) {
  const isWebGL2 = typeof WebGL2RenderingContext !== "undefined"
    && context instanceof WebGL2RenderingContext;
  const vertexInput = isWebGL2 ? "in" : "attribute";
  const varyingOutput = isWebGL2 ? "out" : "varying";
  const vertexSource = `
    ${isWebGL2 ? "#version 300 es" : ""}
    ${vertexInput} vec3 aPosition;
    ${vertexInput} vec3 aNormal;
    uniform vec2 uResolution;
    uniform vec3 uCenter;
    uniform float uFit;
    uniform float uDepthScale;
    uniform float uZoom;
    uniform float uAlpha;
    uniform vec2 uAngles;
    ${varyingOutput} float vLight;
    void main() {
      float azimuthCos = cos(uAngles.x);
      float azimuthSin = sin(uAngles.x);
      float elevationCos = cos(uAngles.y);
      float elevationSin = sin(uAngles.y);
      vec3 point = aPosition - uCenter;
      vec3 normal = aNormal;
      float x = point.x * azimuthCos - point.y * azimuthSin;
      float y = point.x * azimuthSin + point.y * azimuthCos;
      float normalX = normal.x * azimuthCos - normal.y * azimuthSin;
      float normalY = normal.x * azimuthSin + normal.y * azimuthCos;
      point = vec3(x, y * elevationCos - point.z * elevationSin,
        y * elevationSin + point.z * elevationCos);
      normal = normalize(vec3(normalX,
        normalY * elevationCos - normal.z * elevationSin,
        normalY * elevationSin + normal.z * elevationCos));
      vec2 halfResolution = uResolution * 0.5;
      gl_Position = vec4(point.x * uFit * uZoom / halfResolution.x,
        -point.y * uFit * uZoom / halfResolution.y,
        -point.z * uDepthScale, 1.0);
      vec3 lightDirection = normalize(vec3(-0.45, 0.65, 1.0));
      vLight = 0.52 + 0.48 * max(dot(normal, lightDirection), 0.0);
    }`.trimStart();
  const fragmentSource = `
    ${isWebGL2 ? "#version 300 es" : ""}
    precision mediump float;
    uniform vec3 uColor;
    uniform float uAlpha;
    ${isWebGL2 ? "in" : "varying"} float vLight;
    ${isWebGL2 ? "out vec4 outColor;" : ""}
    void main() {
      ${isWebGL2 ? "outColor" : "gl_FragColor"} = vec4(uColor * vLight, uAlpha);
    }`.trimStart();

  function compileShader(type, source) {
    const shader = context.createShader(type);
    if (!shader) throw new Error("WebGL-Shader konnte nicht angelegt werden.");
    context.shaderSource(shader, source);
    context.compileShader(shader);
    if (!context.getShaderParameter(shader, context.COMPILE_STATUS)) {
      const error = context.getShaderInfoLog(shader);
      context.deleteShader(shader);
      throw new Error(`WebGL-Shader konnte nicht kompiliert werden: ${error}`);
    }
    return shader;
  }

  const vertexShader = compileShader(context.VERTEX_SHADER, vertexSource);
  const fragmentShader = compileShader(context.FRAGMENT_SHADER, fragmentSource);
  const program = context.createProgram();
  if (!program) throw new Error("WebGL-Programm konnte nicht angelegt werden.");
  context.attachShader(program, vertexShader);
  context.attachShader(program, fragmentShader);
  context.linkProgram(program);
  context.deleteShader(vertexShader);
  context.deleteShader(fragmentShader);
  if (!context.getProgramParameter(program, context.LINK_STATUS)) {
    const error = context.getProgramInfoLog(program);
    context.deleteProgram(program);
    throw new Error(`WebGL-Programm konnte nicht verknüpft werden: ${error}`);
  }
  context.useProgram(program);
  return {
    program,
    position: context.getAttribLocation(program, "aPosition"),
    normal: context.getAttribLocation(program, "aNormal"),
    resolution: context.getUniformLocation(program, "uResolution"),
    center: context.getUniformLocation(program, "uCenter"),
    fit: context.getUniformLocation(program, "uFit"),
    depthScale: context.getUniformLocation(program, "uDepthScale"),
    zoom: context.getUniformLocation(program, "uZoom"),
    alpha: context.getUniformLocation(program, "uAlpha"),
    angles: context.getUniformLocation(program, "uAngles"),
    color: context.getUniformLocation(program, "uColor"),
  };
}

function createEdgeRenderer(context) {
  const isWebGL2 = typeof WebGL2RenderingContext !== "undefined"
    && context instanceof WebGL2RenderingContext;
  const vertexInput = isWebGL2 ? "in" : "attribute";
  const varyingOutput = isWebGL2 ? "out" : "varying";
  const vertexSource = `
    ${isWebGL2 ? "#version 300 es" : ""}
    ${vertexInput} vec3 aPosition;
    ${vertexInput} vec3 aNormalA;
    ${vertexInput} vec3 aNormalB;
    ${vertexInput} float aFeature;
    uniform vec2 uResolution;
    uniform vec3 uCenter;
    uniform float uFit;
    uniform float uDepthScale;
    uniform float uZoom;
    uniform vec2 uAngles;
    ${varyingOutput} float vVisible;
    vec3 rotateVector(vec3 value) {
      float azimuthCos = cos(uAngles.x);
      float azimuthSin = sin(uAngles.x);
      float elevationCos = cos(uAngles.y);
      float elevationSin = sin(uAngles.y);
      float x = value.x * azimuthCos - value.y * azimuthSin;
      float y = value.x * azimuthSin + value.y * azimuthCos;
      return vec3(x, y * elevationCos - value.z * elevationSin,
        y * elevationSin + value.z * elevationCos);
    }
    void main() {
      vec3 point = rotateVector(aPosition - uCenter);
      vec2 halfResolution = uResolution * 0.5;
      gl_Position = vec4(point.x * uFit * uZoom / halfResolution.x,
        -point.y * uFit * uZoom / halfResolution.y,
        -point.z * uDepthScale, 1.0);
      float facingA = rotateVector(aNormalA).z;
      float facingB = rotateVector(aNormalB).z;
      vVisible = aFeature > 0.5
        || (facingA < -0.0001 && facingB > 0.0001)
        || (facingB < -0.0001 && facingA > 0.0001) ? 1.0 : 0.0;
    }`.trimStart();
  const fragmentSource = `
    ${isWebGL2 ? "#version 300 es" : ""}
    precision mediump float;
    uniform vec3 uColor;
    uniform float uAlpha;
    ${isWebGL2 ? "in" : "varying"} float vVisible;
    ${isWebGL2 ? "out vec4 outColor;" : ""}
    void main() {
      if (vVisible < 0.5) discard;
      ${isWebGL2 ? "outColor" : "gl_FragColor"} = vec4(uColor, uAlpha);
    }`.trimStart();

  function compileShader(type, source) {
    const shader = context.createShader(type);
    if (!shader) throw new Error("WebGL-Kantenshader konnte nicht angelegt werden.");
    context.shaderSource(shader, source);
    context.compileShader(shader);
    if (!context.getShaderParameter(shader, context.COMPILE_STATUS)) {
      const error = context.getShaderInfoLog(shader);
      context.deleteShader(shader);
      throw new Error(`WebGL-Kantenshader konnte nicht kompiliert werden: ${error}`);
    }
    return shader;
  }

  const vertexShader = compileShader(context.VERTEX_SHADER, vertexSource);
  const fragmentShader = compileShader(context.FRAGMENT_SHADER, fragmentSource);
  const program = context.createProgram();
  if (!program) throw new Error("WebGL-Kantenprogramm konnte nicht angelegt werden.");
  context.attachShader(program, vertexShader);
  context.attachShader(program, fragmentShader);
  context.linkProgram(program);
  context.deleteShader(vertexShader);
  context.deleteShader(fragmentShader);
  if (!context.getProgramParameter(program, context.LINK_STATUS)) {
    const error = context.getProgramInfoLog(program);
    context.deleteProgram(program);
    throw new Error(`WebGL-Kantenprogramm konnte nicht verknüpft werden: ${error}`);
  }
  return {
    program,
    position: context.getAttribLocation(program, "aPosition"),
    normalA: context.getAttribLocation(program, "aNormalA"),
    normalB: context.getAttribLocation(program, "aNormalB"),
    feature: context.getAttribLocation(program, "aFeature"),
    resolution: context.getUniformLocation(program, "uResolution"),
    center: context.getUniformLocation(program, "uCenter"),
    fit: context.getUniformLocation(program, "uFit"),
    depthScale: context.getUniformLocation(program, "uDepthScale"),
    zoom: context.getUniformLocation(program, "uZoom"),
    angles: context.getUniformLocation(program, "uAngles"),
    color: context.getUniformLocation(program, "uColor"),
    alpha: context.getUniformLocation(program, "uAlpha"),
  };
}

if (gl) {
  try {
    renderer = createRenderer(gl);
    edgeRenderer = createEdgeRenderer(gl);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.clearColor(0, 0, 0, 0);
  } catch (error) {
    message(error.message, true);
  }
} else {
  message("3D-Vorschau nicht verfügbar: Bitte WebGL in deinem Browser aktivieren.", true);
}

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
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.round(bounds.width * ratio));
  canvas.height = Math.max(1, Math.round(bounds.height * ratio));
  if (gl) gl.viewport(0, 0, canvas.width, canvas.height);
  draw();
}

function uploadMesh(data) {
  if (!gl || !renderer) {
    throw new Error("3D-Vorschau konnte nicht initialisiert werden.");
  }
  const { vertices, triangles } = data;
  const positions = new Float32Array(vertices.length * 3);
  const normals = new Float32Array(vertices.length * 3);
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < vertices.length; i++) {
    const [x, y, z] = vertices[i];
    const offset = i * 3;
    positions[offset] = x;
    positions[offset + 1] = y;
    positions[offset + 2] = z;
    minX = Math.min(minX, x); minY = Math.min(minY, y); minZ = Math.min(minZ, z);
    maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); maxZ = Math.max(maxZ, z);
  }
  const IndexArray = vertices.length > 65535 ? Uint32Array : Uint16Array;
  if (IndexArray === Uint32Array && !gl.getExtension("OES_element_index_uint") && !gl.getParameter(gl.VERSION).includes("WebGL 2")) {
    throw new Error("Dieses CAD-Netz benötigt WebGL 2 für seine Darstellung.");
  }
  const indices = new IndexArray(triangles.length * 3);
  for (let i = 0; i < triangles.length; i++) {
    const [a, b, c] = triangles[i];
    indices[i * 3] = a;
    indices[i * 3 + 1] = b;
    indices[i * 3 + 2] = c;
    const ax = positions[a * 3], ay = positions[a * 3 + 1], az = positions[a * 3 + 2];
    const bx = positions[b * 3], by = positions[b * 3 + 1], bz = positions[b * 3 + 2];
    const cx = positions[c * 3], cy = positions[c * 3 + 1], cz = positions[c * 3 + 2];
    const abx = bx - ax, aby = by - ay, abz = bz - az;
    const acx = cx - ax, acy = cy - ay, acz = cz - az;
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    for (const vertex of [a, b, c]) {
      normals[vertex * 3] += nx;
      normals[vertex * 3 + 1] += ny;
      normals[vertex * 3 + 2] += nz;
    }
  }
  for (let i = 0; i < normals.length; i += 3) {
    const length = Math.hypot(normals[i], normals[i + 1], normals[i + 2]) || 1;
    normals[i] /= length;
    normals[i + 1] /= length;
    normals[i + 2] /= length;
  }
  const positionBuffer = gl.createBuffer();
  const normalBuffer = gl.createBuffer();
  const indexBuffer = gl.createBuffer();
  if (!positionBuffer || !normalBuffer || !indexBuffer) {
    throw new Error("Nicht genügend Grafikspeicher für die CAD-Vorschau.");
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, normals, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
  return {
    positionBuffer, normalBuffer, indexBuffer, indices,
    positions,
    indexCount: indices.length, edgeBuffer: null, edgeCount: 0,
    indexType: indices instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
    center: [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2],
    extent: Math.max((maxX - minX) / 2, (maxY - minY) / 2, (maxZ - minZ) / 2, 1e-6),
    vertexCount: vertices.length,
  };
}

function ensureOutlineEdges(mesh) {
  if (mesh.edgeBuffer) return;
  const vertexKeys = new Array(mesh.vertexCount);
  const keyTolerance = mesh.extent * 1e-6;
  for (let i = 0; i < mesh.vertexCount; i++) {
    const offset = i * 3;
    vertexKeys[i] = [
      Math.round(mesh.positions[offset] / keyTolerance),
      Math.round(mesh.positions[offset + 1] / keyTolerance),
      Math.round(mesh.positions[offset + 2] / keyTolerance),
    ].join(",");
  }
  const edgesByKey = new Map();
  const creaseThreshold = Math.cos(30 * Math.PI / 180);
  function addEdge(from, to, nx, ny, nz) {
    const fromKey = vertexKeys[from], toKey = vertexKeys[to];
    const key = fromKey < toKey ? `${fromKey}|${toKey}` : `${toKey}|${fromKey}`;
    const edge = edgesByKey.get(key);
    if (!edge) {
      edgesByKey.set(key, {
        from, to, nx, ny, nz,
        secondNormal: null,
        count: 1,
        feature: false,
      });
      return;
    }
    edge.count++;
    if (nx * edge.nx + ny * edge.ny + nz * edge.nz < creaseThreshold) edge.feature = true;
    if (edge.count === 2) edge.secondNormal = [nx, ny, nz];
    else edge.feature = true;
  }
  for (let i = 0; i < mesh.indices.length; i += 3) {
    const [a, b, c] = [mesh.indices[i], mesh.indices[i + 1], mesh.indices[i + 2]];
    const ax = mesh.positions[a * 3], ay = mesh.positions[a * 3 + 1], az = mesh.positions[a * 3 + 2];
    const bx = mesh.positions[b * 3], by = mesh.positions[b * 3 + 1], bz = mesh.positions[b * 3 + 2];
    const cx = mesh.positions[c * 3], cy = mesh.positions[c * 3 + 1], cz = mesh.positions[c * 3 + 2];
    const abx = bx - ax, aby = by - ay, abz = bz - az;
    const acx = cx - ax, acy = cy - ay, acz = cz - az;
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    const length = Math.hypot(nx, ny, nz);
    if (!length) continue;
    const normal = [nx / length, ny / length, nz / length];
    addEdge(a, b, ...normal);
    addEdge(b, c, ...normal);
    addEdge(c, a, ...normal);
  }
  const edgeVertices = new Float32Array(edgesByKey.size * 2 * 10);
  let vertexOffset = 0;
  for (const edge of edgesByKey.values()) {
    const normalB = edge.secondNormal || [edge.nx, edge.ny, edge.nz];
    const feature = edge.count === 1 || edge.feature ? 1 : 0;
    for (const vertex of [edge.from, edge.to]) {
      const positionOffset = vertex * 3;
      edgeVertices.set(mesh.positions.subarray(positionOffset, positionOffset + 3), vertexOffset);
      edgeVertices.set([edge.nx, edge.ny, edge.nz], vertexOffset + 3);
      edgeVertices.set(normalB, vertexOffset + 6);
      edgeVertices[vertexOffset + 9] = feature;
      vertexOffset += 10;
    }
  }
  mesh.edgeBuffer = gl.createBuffer();
  if (!mesh.edgeBuffer) throw new Error("Nicht genügend Grafikspeicher für die Gitteransicht.");
  gl.bindBuffer(gl.ARRAY_BUFFER, mesh.edgeBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, edgeVertices, gl.STATIC_DRAW);
  if (gl.getError() !== gl.NO_ERROR) {
    gl.deleteBuffer(mesh.edgeBuffer);
    mesh.edgeBuffer = null;
    throw new Error("Nicht genügend Grafikspeicher für die Gitteransicht.");
  }
  mesh.edgeVertexCount = edgeVertices.length / 10;
  mesh.indices = null;
  mesh.positions = null;
}

function updateAxisIndicator() {
  const cosineAzimuth = Math.cos(view.azimuth);
  const sineAzimuth = Math.sin(view.azimuth);
  const cosineElevation = Math.cos(view.elevation);
  const sineElevation = Math.sin(view.elevation);
  const axes = [
    ["x", cosineAzimuth, -sineAzimuth * cosineElevation],
    ["y", -sineAzimuth, -cosineAzimuth * cosineElevation],
    ["z", 0, sineElevation],
  ];
  for (const [axis, x, y] of axes) {
    const line = $(`#axis-${axis}`);
    const label = $(`#axis-label-${axis}`);
    const endX = 34 + x * 22;
    const endY = 37 + y * 22;
    line.setAttribute("x2", endX.toFixed(2));
    line.setAttribute("y2", endY.toFixed(2));
    label.setAttribute("x", (endX + Math.sign(x || 1) * 5).toFixed(2));
    label.setAttribute("y", (endY + Math.sign(y || -1) * 5).toFixed(2));
    label.setAttribute("text-anchor", x < -0.15 ? "end" : "start");
  }
}

function disposeMeshes() {
  meshGeneration++;
  for (const kind of Object.keys(meshRequests)) delete meshRequests[kind];
  if (gl) {
    for (const mesh of Object.values(cached)) {
      gl.deleteBuffer(mesh.positionBuffer);
      gl.deleteBuffer(mesh.normalBuffer);
      gl.deleteBuffer(mesh.indexBuffer);
      if (mesh.edgeBuffer) gl.deleteBuffer(mesh.edgeBuffer);
    }
  }
  cached = {};
}

let framePending = false;
function draw() {
  if (!gl || !renderer) return;
  if (framePending) return;
  framePending = true;
  requestAnimationFrame(() => {
    framePending = false;
    updateAxisIndicator();
    const width = canvas.width, height = canvas.height;
    gl.viewport(0, 0, width, height);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const meshes = currentMode === "part"
      ? [["part", cached.part, [0.64, 0.875, 0.435]]]
      : currentMode === "mold"
        ? [["lower", cached.lower, [0.75, 0.79, 0.75]], ["upper", cached.upper, [0.66, 0.72, 0.67]]]
        : [
          ["part", cached.part, [0.64, 0.875, 0.435]],
          ["lower", cached.lower, [0.75, 0.79, 0.75]],
          ["upper", cached.upper, [0.66, 0.72, 0.67]],
        ];
    const visible = meshes.filter(([, mesh]) => mesh);
    if (!visible.length || !width || !height) return;
    if (display.grid) {
      try {
        for (const [, mesh] of visible) ensureOutlineEdges(mesh);
      } catch (error) {
        display.grid = false;
        const gridButton = $("#grid-toggle");
        gridButton.classList.remove("selected");
        gridButton.setAttribute("aria-pressed", "false");
        message(error.message, true);
      }
    }
    gl.useProgram(renderer.program);
    const center = visible[0][1].center;
    const extent = Math.max(...visible.map(([, mesh]) => {
      const dx = mesh.center[0] - center[0], dy = mesh.center[1] - center[1], dz = mesh.center[2] - center[2];
      return Math.hypot(dx, dy, dz) + mesh.extent;
    }));
    gl.uniform2f(renderer.resolution, width, height);
    gl.uniform3f(renderer.center, center[0], center[1], center[2]);
    gl.uniform1f(renderer.fit, Math.min(width, height) * 0.37 / extent);
    gl.uniform1f(renderer.depthScale, 0.85 / extent);
    gl.uniform1f(renderer.zoom, view.zoom);
    gl.uniform1f(renderer.alpha, display.transparent ? 0.34 : 1);
    gl.uniform2f(renderer.angles, view.azimuth, view.elevation);
    if (display.transparent) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
    } else {
      gl.disable(gl.BLEND);
      gl.depthMask(true);
    }
    gl.enableVertexAttribArray(renderer.position);
    gl.enableVertexAttribArray(renderer.normal);
    for (const [, mesh, color] of visible) {
      gl.uniform3fv(renderer.color, color);
      gl.bindBuffer(gl.ARRAY_BUFFER, mesh.positionBuffer);
      gl.vertexAttribPointer(renderer.position, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, mesh.normalBuffer);
      gl.vertexAttribPointer(renderer.normal, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.indexBuffer);
      gl.drawElements(gl.TRIANGLES, mesh.indexCount, mesh.indexType, 0);
    }
    if (display.grid) {
      gl.depthFunc(gl.LEQUAL);
      gl.depthMask(false);
      gl.useProgram(edgeRenderer.program);
      gl.uniform2f(edgeRenderer.resolution, width, height);
      gl.uniform3f(edgeRenderer.center, center[0], center[1], center[2]);
      gl.uniform1f(edgeRenderer.fit, Math.min(width, height) * 0.37 / extent);
      gl.uniform1f(edgeRenderer.depthScale, 0.85 / extent);
      gl.uniform1f(edgeRenderer.zoom, view.zoom);
      gl.uniform2f(edgeRenderer.angles, view.azimuth, view.elevation);
      gl.uniform1f(edgeRenderer.alpha, display.transparent ? 0.9 : 0.72);
      gl.enableVertexAttribArray(edgeRenderer.position);
      gl.enableVertexAttribArray(edgeRenderer.normalA);
      gl.enableVertexAttribArray(edgeRenderer.normalB);
      gl.enableVertexAttribArray(edgeRenderer.feature);
      for (const [, mesh, color] of visible) {
        gl.uniform3f(edgeRenderer.color, Math.min(1, color[0] * 1.3), Math.min(1, color[1] * 1.3), Math.min(1, color[2] * 1.3));
        gl.bindBuffer(gl.ARRAY_BUFFER, mesh.edgeBuffer);
        gl.vertexAttribPointer(edgeRenderer.position, 3, gl.FLOAT, false, 40, 0);
        gl.vertexAttribPointer(edgeRenderer.normalA, 3, gl.FLOAT, false, 40, 12);
        gl.vertexAttribPointer(edgeRenderer.normalB, 3, gl.FLOAT, false, 40, 24);
        gl.vertexAttribPointer(edgeRenderer.feature, 1, gl.FLOAT, false, 40, 36);
        gl.drawArrays(gl.LINES, 0, mesh.edgeVertexCount);
      }
    }
    gl.depthMask(true);
  });
}

async function loadMesh(kind) {
  if (cached[kind]) return;
  if (meshRequests[kind]) return meshRequests[kind];
  const generation = meshGeneration;
  const pendingRequest = (async () => {
    const response = await request(`/api/mesh/${kind}`);
    const data = await response.json();
    if (generation !== meshGeneration) return;
    cached[kind] = uploadMesh(data);
    draw();
  })();
  meshRequests[kind] = pendingRequest;
  try {
    await pendingRequest;
  } finally {
    if (meshRequests[kind] === pendingRequest) delete meshRequests[kind];
  }
}

function showModel(model, name) {
  $("#model-name").textContent = name;
  $("#empty-state").classList.add("hidden");
  $("#dimensions").textContent = Object.entries(model.bounds_mm).map(([axis, value]) => `${axis.toUpperCase()} ${value.toFixed(1)}`).join(" · ") + " mm";
  $("#solid-count").textContent = `${model.solid_count} Volumenkörper · V ${(model.volume_mm3 / 1000).toFixed(1)} cm³`;
  $("#apply-transform").disabled = false;
  $("#build").disabled = false;
  $("#show-part").disabled = false;
  $("#show-mold").disabled = true;
  $("#show-both").disabled = true;
  $("#transparent-toggle").disabled = false;
  $("#grid-toggle").disabled = false;
  $("#split").value = $("#split").value || 0;
  setViewMode("part");
}

async function importFile(file) {
  if (!file) return;
  message("STEP-Datei wird geprüft …");
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await (await request("/api/import", { method: "POST", body: form })).json();
    disposeMeshes();
    showModel(result.model, file.name);
    $("#split").value = "0";
    await refreshStatus();
    message("STEP importiert und geprüft");
  } catch (error) { message(error.message, true); }
}

async function refreshStatus() {
  const response = await request("/api/status");
  const current = await response.json();
  if (current.part) {
    showModel(current.part, current.source_name);
    await loadMesh("part");
  }
  $("#wall").value = current.parameters.wall_mm;
  $("#split").value = current.parameters.split_z ?? 0;
  for (const [key, value] of Object.entries(current.transform || {})) {
    const input = document.getElementById(key === "x" || key === "y" || key === "z" ? `t${key}` : key);
    if (input) input.value = value;
  }
  if (current.mold_ready) {
    $("#show-mold").disabled = false;
    $("#show-both").disabled = false;
    for (const button of document.querySelectorAll("[data-export]")) button.disabled = false;
  } else {
    $("#show-mold").disabled = true;
    $("#show-both").disabled = true;
  }
}

$("#step-file").addEventListener("change", event => importFile(event.target.files[0]));
$("#demo").addEventListener("click", async () => {
  try {
    message("Referenzkörper wird erzeugt …");
    const response = await request("/api/demo", { method: "POST" });
    const result = await response.json();
    disposeMeshes();
    showModel(result.model, "Referenzkörper.step");
    $("#split").value = "0";
    await loadMesh("part");
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
    disposeMeshes();
    showModel(result.model, $("#model-name").textContent);
    await loadMesh("part");
    $("#show-mold").disabled = true;
    $("#show-both").disabled = true;
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
    $("#show-both").disabled = false;
    for (const button of document.querySelectorAll("[data-export]")) button.disabled = false;
    await setViewMode("mold");
    message("Form gültig berechnet");
  } catch (error) { message(error.message, true); }
});

async function setViewMode(mode) {
  currentMode = mode;
  for (const [id, buttonMode] of [["show-part", "part"], ["show-mold", "mold"], ["show-both", "both"]]) {
    const button = $(`#${id}`);
    const selected = mode === buttonMode;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  try {
    if (mode === "part" || mode === "both") await loadMesh("part");
    if (mode === "mold" || mode === "both") await Promise.all([loadMesh("lower"), loadMesh("upper")]);
    draw();
  } catch (error) { message(error.message, true); }
}

for (const [id, mode] of [["show-part", "part"], ["show-mold", "mold"], ["show-both", "both"]]) {
  $(`#${id}`).addEventListener("click", () => setViewMode(mode));
}

for (const [id, property] of [["transparent-toggle", "transparent"], ["grid-toggle", "grid"]]) {
  $(`#${id}`).addEventListener("click", event => {
    display[property] = !display[property];
    event.currentTarget.classList.toggle("selected", display[property]);
    event.currentTarget.setAttribute("aria-pressed", String(display[property]));
    draw();
  });
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
    await request("/api/project", { method: "POST", body: form });
    disposeMeshes();
    await refreshStatus();
    message("Projekt geöffnet — Form neu berechnen");
  } catch (error) { message(error.message, true); }
});

canvas.addEventListener("pointerdown", event => { view.dragging = true; view.lastX = event.clientX; view.lastY = event.clientY; canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener("pointermove", event => {
  if (!view.dragging) return;
  view.azimuth -= (event.clientX - view.lastX) * 0.008;
  view.elevation -= (event.clientY - view.lastY) * 0.008;
  view.lastX = event.clientX; view.lastY = event.clientY; draw();
});
canvas.addEventListener("pointerup", () => { view.dragging = false; });
canvas.addEventListener("wheel", event => { event.preventDefault(); view.zoom = Math.max(0.3, Math.min(5, view.zoom * (event.deltaY < 0 ? 1.1 : 0.9))); draw(); }, { passive: false });
window.addEventListener("resize", resize);
resize();
refreshStatus().catch(error => message(error.message, true));

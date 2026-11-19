import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { fetchArrayBuffer } from "./api.js";

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const DRACO_PATH = "/static/vendor/three/draco/";
const PREVIEW_WIDTH = 640;
const PREVIEW_HEIGHT = 480;
const PREVIEW_FPS = 30;

let sharedDracoLoader = null;

function getDracoLoader() {
  if (!sharedDracoLoader) {
    sharedDracoLoader = new DRACOLoader();
    sharedDracoLoader.setDecoderPath(DRACO_PATH);
  }
  return sharedDracoLoader;
}

function makeGltfLoader() {
  const loader = new GLTFLoader();
  loader.setDRACOLoader(getDracoLoader());
  return loader;
}

async function loadGltf(model, { onProgress, signal }) {
  const loader = makeGltfLoader();

  if (model.kind === "gltf") {
    return new Promise((resolve, reject) => {
      loader.load(
        model.asset_url,
        resolve,
        (event) => {
          if (!onProgress) return;
          if (event.lengthComputable && event.total > 0) {
            onProgress(event.loaded / event.total);
          } else {
            onProgress(null);
          }
        },
        reject,
      );
    });
  }

  const buffer = await fetchArrayBuffer(model.asset_url, { onProgress, signal });
  return new Promise((resolve, reject) => {
    loader.parse(buffer, "", resolve, reject);
  });
}

function disposeObject(root) {
  root.traverse((object) => {
    if (object.geometry) {
      object.geometry.dispose();
    }
    const materials = Array.isArray(object.material)
      ? object.material
      : object.material
        ? [object.material]
        : [];
    for (const material of materials) {
      for (const value of Object.values(material)) {
        if (value && value.isTexture) {
          value.dispose();
        }
      }
      material.dispose();
    }
  });
}

function addPreviewLights(scene) {
  scene.add(new THREE.HemisphereLight(0xfff1dc, 0x202a22, 1.75));

  const key = new THREE.DirectionalLight(0xfff2df, 2.7);
  key.position.set(3.5, 5.5, 4.5);
  scene.add(key);

  const fill = new THREE.DirectionalLight(0xb9d2ff, 1.15);
  fill.position.set(-4.5, 2.5, -3);
  scene.add(fill);

  const rim = new THREE.DirectionalLight(0xffd5a0, 1.2);
  rim.position.set(0, 3.5, -6);
  scene.add(rim);
}

class PreviewEngine {
  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.width = PREVIEW_WIDTH;
    this.canvas.height = PREVIEW_HEIGHT;
    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas,
        antialias: true,
        alpha: true,
        powerPreference: "low-power",
        preserveDrawingBuffer: true,
      });
    } catch {
      throw new Error("WebGL não está disponível neste navegador.");
    }

    this.renderer.setPixelRatio(1);
    this.renderer.setSize(PREVIEW_WIDTH, PREVIEW_HEIGHT, false);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.98;

    this.views = new Set();
    this.raf = null;
    this.lastFrame = performance.now();
    this.lost = false;

    this.canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.lost = true;
      for (const view of this.views) {
        view.reportError(
          new Error("O contexto WebGL foi perdido. Recarregue a página."),
        );
      }
    });
  }

  add(view) {
    this.views.add(view);
  }

  remove(view) {
    this.views.delete(view);
  }

  request(view) {
    if (this.lost) return;
    view.needsFrame = true;
    this.schedule();
  }

  schedule() {
    if (this.raf !== null || this.lost || this.views.size === 0) return;
    this.raf = requestAnimationFrame((time) => {
      this.raf = null;
      this._draw(time);
    });
  }

  _draw(time) {
    if (!this.views.size || this.lost) return;

    const elapsed = time - this.lastFrame;
    if (elapsed < 1000 / PREVIEW_FPS) {
      this.schedule();
      return;
    }

    const delta = Math.min(Math.max(elapsed / 1000, 0), 0.1);
    this.lastFrame = time;
    let keepDrawing = false;

    for (const view of this.views) {
      if (!view.ready) continue;
      const animating = view.active && !view.paused;
      if (!view.needsFrame && !animating) continue;

      if (animating && view.autoRotate && view.pivot && !REDUCED_MOTION) {
        view.pivot.rotation.y += delta * 0.38;
      }
      this.renderer.render(view.scene, view.camera);
      view.copyFrame();
      view.needsFrame = false;
      if (animating) keepDrawing = true;
    }

    if (keepDrawing) this.schedule();
  }
}

class PreviewViewer {
  constructor(engine, container, model, options = {}) {
    this.engine = engine;
    this.container = container;
    this.model = model;
    this.autoRotate = options.autoRotate !== false;
    this.onProgress = options.onProgress || (() => {});
    this.onReady = options.onReady || (() => {});
    this.onError = options.onError || (() => {});

    this.disposed = false;
    this.ready = false;
    this.active = false;
    this.paused = false;
    this.needsFrame = true;
    this.pivot = null;
    this.modelRoot = null;
    this.abortController = new AbortController();

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      38,
      PREVIEW_WIDTH / PREVIEW_HEIGHT,
      0.01,
      1000,
    );

    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("aria-hidden", "true");
    this.ctx = this.canvas.getContext("2d");
    this.container.appendChild(this.canvas);

    addPreviewLights(this.scene);
    this.resizeObserver = new ResizeObserver(() => this._resize());
    this.resizeObserver.observe(this.container);
    this._resize();

    this.engine.add(this);
    this.engine.request(this);
  }

  _resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    const width = Math.max(1, Math.round(this.container.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.container.clientHeight * ratio));
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    this.engine.request(this);
  }

  copyFrame() {
    if (this.disposed || this.canvas.width <= 0 || this.canvas.height <= 0) {
      return;
    }
    const width = this.canvas.width;
    const height = this.canvas.height;
    const source = this.engine.canvas;
    const scale = Math.max(width / source.width, height / source.height);
    const drawWidth = source.width * scale;
    const drawHeight = source.height * scale;
    this.ctx.clearRect(0, 0, width, height);
    this.ctx.drawImage(
      source,
      (width - drawWidth) / 2,
      (height - drawHeight) / 2,
      drawWidth,
      drawHeight,
    );
  }

  setActive(active) {
    this.active = Boolean(active);
    if (this.active) this.engine.request(this);
  }

  setPaused(paused) {
    this.paused = Boolean(paused);
    if (!this.paused) this.engine.request(this);
  }

  reportError(error) {
    if (!this.disposed) this.onError(error);
  }

  async load() {
    try {
      const gltf = await loadGltf(this.model, {
        onProgress: (ratio) => {
          if (!this.disposed) this.onProgress(ratio);
        },
        signal: this.abortController.signal,
      });

      if (this.disposed) {
        disposeObject(gltf.scene);
        return;
      }

      this._mount(gltf.scene);
      this.ready = true;
      this.engine.request(this);
      this.onReady(gltf);
    } catch (error) {
      if (this.disposed || error.name === "AbortError") return;
      this.onError(error);
    }
  }

  _mount(root) {
    this.modelRoot = root;
    this.pivot = new THREE.Group();
    this.pivot.add(root);
    this.scene.add(this.pivot);

    root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) {
      throw new Error("O modelo não contém geometria visível.");
    }

    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    root.position.sub(center);
    this._frame(size);
  }

  _frame(size) {
    const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov) * 0.5;
    const fitHeight = radius / Math.sin(halfFov);
    const fitWidth = fitHeight / Math.max(this.camera.aspect, 0.1);
    const distance = Math.max(fitHeight, fitWidth) * 1.2;

    const direction = new THREE.Vector3(1, 0.55, 1.15).normalize();
    this.camera.position.copy(direction.multiplyScalar(distance));
    this.camera.near = Math.max(distance / 1000, 0.001);
    this.camera.far = Math.max(distance * 100, 10);
    this.camera.updateProjectionMatrix();
    this.camera.lookAt(0, 0, 0);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.abortController.abort();
    this.resizeObserver?.disconnect();
    this.engine.remove(this);
    if (this.modelRoot) {
      disposeObject(this.modelRoot);
      this.modelRoot = null;
    }
    this.canvas.remove();
  }
}

let previewEngine = null;

export function createPreviewViewer(container, model, options = {}) {
  if (!previewEngine) previewEngine = new PreviewEngine();
  return new PreviewViewer(previewEngine, container, model, options);
}

export class ModelViewer {
  constructor(container, model, options = {}) {
    this.container = container;
    this.model = model;
    this.interactive = Boolean(options.interactive);
    this.autoRotate = Boolean(options.autoRotate);
    this.onProgress = options.onProgress || (() => {});
    this.onReady = options.onReady || (() => {});
    this.onError = options.onError || (() => {});

    this.disposed = false;
    this.ready = false;
    this.pivot = null;
    this.modelRoot = null;
    this.abortController = new AbortController();
    this.raf = null;
    this.paused = false;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.01, 1000);

    this._initRenderer();
    this._initLights();
    this._initControls();
    this._initResize();
    this._startLoop();
  }

  _initRenderer() {
    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: this.interactive ? "high-performance" : "low-power",
      });
    } catch {
      throw new Error("WebGL não está disponível neste navegador.");
    }

    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, this.interactive ? 2 : 1.5),
    );
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.98;

    const canvas = this.renderer.domElement;
    canvas.setAttribute("aria-hidden", "true");
    this.container.appendChild(canvas);

    this.onContextLost = (event) => {
      event.preventDefault();
      if (!this.disposed) {
        this.onError(
          new Error("O contexto WebGL foi perdido. Recarregue a página."),
        );
      }
    };
    canvas.addEventListener("webglcontextlost", this.onContextLost);
  }

  _initLights() {
    this.scene.add(new THREE.HemisphereLight(0xfff1dc, 0x202a22, 1.75));

    const key = new THREE.DirectionalLight(0xfff2df, 2.7);
    key.position.set(3.5, 5.5, 4.5);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xb9d2ff, 1.15);
    fill.position.set(-4.5, 2.5, -3);
    this.scene.add(fill);

    const rim = new THREE.DirectionalLight(0xffd5a0, 1.2);
    rim.position.set(0, 3.5, -6);
    this.scene.add(rim);

    if (this.interactive) {
      this.pmrem = new THREE.PMREMGenerator(this.renderer);
      this.envTarget = this.pmrem.fromScene(new RoomEnvironment(), 0.04);
      this.scene.environment = this.envTarget.texture;
      this.scene.environmentIntensity = 0.55;
    }
  }

  _initControls() {
    if (!this.interactive) return;
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.075;
    this.controls.rotateSpeed = 0.82;
    this.controls.zoomSpeed = 0.9;
    this.controls.panSpeed = 0.8;
    this.controls.target.set(0, 0, 0);
  }

  _initResize() {
    this.resizeObserver = new ResizeObserver(() => this._resize());
    this.resizeObserver.observe(this.container);
    this._resize();
  }

  _resize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  _startLoop() {
    this.lastFrame = performance.now();
    const loop = (time) => {
      this.raf = requestAnimationFrame(loop);
      const delta = Math.min(
        Math.max((time - this.lastFrame) / 1000, 0),
        0.05,
      );
      this.lastFrame = time;
      if (this.paused) return;
      if (this.controls) {
        this.controls.update();
      }
      if (this.autoRotate && this.pivot && !REDUCED_MOTION) {
        this.pivot.rotation.y += delta * 0.38;
      }
      this.renderer.render(this.scene, this.camera);
    };
    this.raf = requestAnimationFrame(loop);
  }

  setPaused(paused) {
    this.paused = Boolean(paused);
    this.lastFrame = performance.now();
  }

  async load() {
    try {
      const gltf = await loadGltf(this.model, {
        onProgress: (ratio) => {
          if (!this.disposed) this.onProgress(ratio);
        },
        signal: this.abortController.signal,
      });

      if (this.disposed) {
        disposeObject(gltf.scene);
        return;
      }

      this._mount(gltf.scene);
      this.ready = true;
      this.onReady(gltf);
    } catch (error) {
      if (this.disposed || error.name === "AbortError") return;
      this.onError(error);
    }
  }

  _mount(root) {
    this.modelRoot = root;
    this.pivot = new THREE.Group();
    this.pivot.add(root);
    this.scene.add(this.pivot);

    root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) {
      throw new Error("O modelo não contém geometria visível.");
    }

    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    root.position.sub(center);
    this._frame(size);
  }

  _frame(size) {
    const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1;
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov) * 0.5;
    const fitHeight = radius / Math.sin(halfFov);
    const fitWidth = fitHeight / Math.max(this.camera.aspect, 0.1);
    const distance = Math.max(fitHeight, fitWidth) * 1.2;

    const direction = new THREE.Vector3(1, 0.55, 1.15).normalize();
    this.camera.position.copy(direction.multiplyScalar(distance));
    this.camera.near = Math.max(distance / 1000, 0.001);
    this.camera.far = Math.max(distance * 100, 10);
    this.camera.updateProjectionMatrix();

    if (this.controls) {
      this.controls.target.set(0, 0, 0);
      this.controls.minDistance = distance * 0.25;
      this.controls.maxDistance = distance * 6;
      this.controls.update();
    } else {
      this.camera.lookAt(0, 0, 0);
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.abortController.abort();
    if (this.raf !== null) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    this.resizeObserver?.disconnect();
    this.controls?.dispose();

    if (this.modelRoot) {
      disposeObject(this.modelRoot);
      this.modelRoot = null;
    }

    this.envTarget?.dispose();
    this.pmrem?.dispose();

    if (this.onContextLost) {
      this.renderer.domElement.removeEventListener(
        "webglcontextlost",
        this.onContextLost,
      );
    }
    this.renderer.dispose();
    this.renderer.forceContextLoss?.();
    this.renderer.domElement.remove();
  }
}

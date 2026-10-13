// 具材の見た目の調整ページ。パラメタをいじると、その場で見た目が変わる。
// パラメタの定義と、見た目の付け方は look.js（ジェネレーターと共有）
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import GUI from "lil-gui";
import { DEFAULTS, STORAGE_KEY, loadLook, normalizeLook, sample, rng, lookInstance, setupLighting } from "./look.js";
import { addLookControls, downloadLook } from "./look-gui.js";

const MODEL_DIR = "../../02_assets/models-web/";
const GRID = { cols: 4, rows: 3, gap: 13 };

// ---------- three.js ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);
const { sun } = setupLighting(scene, renderer);

const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 1.2;

function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  // 右の調整パネル（幅300px）にかからないよう、画面の中心を左へ150pxずらす
  camera.setViewOffset(innerWidth + 300, innerHeight, 300, 0, innerWidth, innerHeight);
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

// ---------- モデル読み込み ----------
const manifest = await (await fetch(MODEL_DIR + "models.json")).json();
const names = Object.keys(manifest).filter((n) => n !== "mannequin");
const loader = new GLTFLoader();
const models = {};
await Promise.all(
  names.map(async (name) => {
    const gltf = await loader.loadAsync(MODEL_DIR + manifest[name].file);
    // 頂点カラーの倍率を戻す（main.js の applyColorScale と同じ）
    const scale = manifest[name].colorScale;
    const done = new Set(); // 部品（玉ねぎのかけら）どうしでマテリアルを共有しているので、1回だけ掛ける
    if (scale) gltf.scene.traverse((o) => {
      if (o.isMesh && o.geometry.attributes.color && !done.has(o.material)) {
        o.material.color.multiplyScalar(scale);
        done.add(o.material);
      }
    });
    models[name] = gltf.scene;
  }),
);

// ---------- 調整の値 ----------
const look = await loadLook();
function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(look));
  } catch {}
}

const state = { name: names.includes("sauce-ketchup") ? "sauce-ketchup" : names[0], view: "one", seed: 1 };

// ---------- 表示 ----------
const world = new THREE.Group();
scene.add(world);
let instances = [];

function build() {
  world.clear();
  instances = [];
  const entry = look.ingredients[state.name];
  const count = state.view === "one" ? 1 : GRID.cols * GRID.rows;
  for (let i = 0; i < count; i++) {
    const inst = lookInstance(models[state.name]);
    if (count > 1) {
      const c = i % GRID.cols, r = Math.floor(i / GRID.cols);
      inst.object.position.set((c - (GRID.cols - 1) / 2) * GRID.gap, 0, (r - (GRID.rows - 1) / 2) * GRID.gap);
      inst.object.rotation.y = i * 2.4;
    }
    world.add(inst.object);
    instances.push(inst);
  }
  update();
  frame();
}

// パラメタを反映する。1個のときは基準値そのもの、並べるときは揺らぎを入れる
function update() {
  const entry = look.ingredients[state.name];
  instances.forEach((inst, i) => {
    const v = instances.length === 1 ? { ...entry.base } : sample(entry, rng(state.seed * 1000 + i));
    inst.apply(v);
  });
}

function frame() {
  const one = state.view === "one";
  const d = one ? 32 : 115;
  controls.target.set(0, one ? 1 : 0, 0);
  camera.position.set(d * 0.7, d * 0.55, d * 0.7);
}

// ---------- 左のUI ----------
const $ = (id) => document.getElementById(id);
function button(label, pressed, onClick) {
  const b = document.createElement("button");
  b.textContent = label;
  b.setAttribute("aria-pressed", String(pressed));
  b.onclick = () => { onClick(); b.blur(); };
  return b;
}
function renderLeft() {
  $("ingredients").replaceChildren(...names.map((n) => button(n, state.name === n, () => { state.name = n; build(); renderLeft(); buildGui(); })));
  $("view").replaceChildren(
    button("1個（基準値）", state.view === "one", () => { state.view = "one"; build(); renderLeft(); }),
    button("並べる（揺らぎ）", state.view === "grid", () => { state.view = "grid"; build(); renderLeft(); }),
  );
}
$("reseed").onclick = (e) => { state.seed++; update(); e.currentTarget.blur(); };
$("export").onclick = () => downloadLook(look);
$("import").onclick = () => $("file").click();
$("file").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const loaded = normalizeLook(JSON.parse(await f.text()));
  look.ingredients = loaded.ingredients;
  save();
  update();
  buildGui();
  e.target.value = "";
};
$("reset").onclick = () => {
  look.ingredients[state.name] = structuredClone(DEFAULTS[state.name]);
  save();
  update();
  buildGui();
};

// ---------- 右の調整パネル ----------
let gui;
function buildGui() {
  gui?.destroy();
  gui = new GUI({ title: `${state.name} の見た目` });
  const entry = look.ingredients[state.name];
  const changed = () => { save(); update(); };

  addLookControls(gui, entry, (kind) => {
    changed();
    // 揺らぎをいじったら、並べる表示にして見せる
    if (kind === "range" && state.view !== "grid") { state.view = "grid"; build(); renderLeft(); }
  });
  const env = gui.addFolder("光（この画面だけ）");
  env.close();
  env.add(scene, "environmentIntensity", 0, 2, 0.01).name("まわりの光");
  env.add(sun, "intensity", 0, 4, 0.01).name("太陽の光");
  env.add(controls, "autoRotate").name("回す");
}

// ---------- 元のスキャンと比べる ----------
addEventListener("keydown", (e) => {
  if (e.key === "o" || e.key === "O") instances.forEach((i) => i.showOriginal(true));
});
addEventListener("keyup", (e) => {
  if (e.key === "o" || e.key === "O") instances.forEach((i) => i.showOriginal(false));
});

renderLeft();
buildGui();
build();
renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});


// 100万円バーガー・ジェネレーター（試作）
// 当日仕入れる具材を全部1つにまとめた「1個100万円のバーガー」を積む。
// 具材の総数は固定のまま、「1層に並べる数」と「積み方」で形が変わる。1層1個なら塔、全部1層なら円盤。
// 具材は数千個あるので、InstancedMesh（同じ形を、位置だけ変えて、まとめて描く仕組み）で描く。
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadLook, makeLookMaterial, applyLook, setupLighting, bakeParts } from "./look.js";

// スキャンした具材の軽い版（8,300個並べるので）。代替モデルに戻すなら "../../02_assets/models/"
const MODEL_DIR = "../../02_assets/models-web/lite/";
const PRICE = 1_000_000;
// 仕入れ（玉ねぎ 15kg は、1段10gで 1500段分）
const STOCK = { "bun-bottom": 1500, "bun-top": 1500, patty: 2000, cheese: 1800, onion: 1500 };
const STOCK_LABEL = "バンズ 1,500ペア／パティ 2,000枚／チーズ 1,800枚／玉ねぎ 15kg（10g×1,500）";
const PEOPLE = STOCK["bun-bottom"]; // バンズのペアの数＝食べる人の数
const MAX_WIDTH = 2000;
const SPACING = 10.6; // 並べる間隔cm（直径10cm＋すき間）
const ORDERS = [
  { key: "alt", name: "交互" },
  { key: "block", name: "まとめて" },
  { key: "random", name: "ランダム" },
];

const state = { width: 1, order: "alt", seed: 1, copy: true };

// ---------- three.js ----------
const renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);
const camera = new THREE.PerspectiveCamera(30, 1, 1, 1e6);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
// 照明と映り込みは、調整ページ（tune.html）と同じにする（look.js）
const { sun } = setupLighting(scene, renderer);

function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

// 頂点カラーの倍率を戻す（glTFの頂点カラーは0〜1なので、スキャンの継ぎ目を明るくする倍率は半分にして入れてある。merge_scans.py）
function applyColorScale(scene, scale) {
  if (!scale) return;
  const done = new Set(); // 部品（玉ねぎのかけら）どうしでマテリアルを共有しているので、1回だけ掛ける
  scene.traverse((o) => {
    if (o.isMesh && o.geometry.attributes.color && !done.has(o.material)) {
      o.material.color.multiplyScalar(scale);
      done.add(o.material);
    }
  });
}

// ---------- モデル読み込み ----------
const manifest = await (await fetch(MODEL_DIR + "models.json")).json();
const loader = new GLTFLoader();
const models = {};
await Promise.all(
  [...Object.keys(STOCK), "mannequin"].map(async (name) => {
    const gltf = await loader.loadAsync(MODEL_DIR + manifest[name].file);
    applyColorScale(gltf.scene, manifest[name].colorScale);
    models[name] = { scene: gltf.scene, h: manifest[name].stackHeight };
  }),
);

// 見た目は、調整ページで決めた基準値を使う（8,300個を一度に描くので、揺らぎは入れない）。
// 「透け」は描くのが重い（画面をもう1回描く）ので、ここでは切る
const look = await loadLook();
for (const name of Object.keys(STOCK)) {
  const entry = look.ingredients[name];
  if (!entry) continue;
  const made = new Map(); // かけらどうしで共有しているマテリアルは、1つだけ作る
  models[name].scene.traverse((o) => {
    if (!o.isMesh) return;
    if (!made.has(o.material)) {
      const m = makeLookMaterial(o.material);
      applyLook(m, { ...entry.base, transmission: 0 });
      made.set(o.material, m);
    }
    o.material = made.get(o.material);
  });
  // 玉ねぎのかけらは、マテリアルごとに1つの形にまとめる（インスタンスの種類が180→3に減って軽くなる）
  models[name].scene = bakeParts(models[name].scene);
}

// 具材ごとに、中のメッシュ1つにつき InstancedMesh を1つ作る（数は仕入れの数）
const instanced = {};
for (const [name, count] of Object.entries(STOCK)) {
  const root = models[name].scene;
  root.updateMatrixWorld(true);
  instanced[name] = [];
  root.traverse((o) => {
    if (!o.isMesh) return;
    const im = new THREE.InstancedMesh(o.geometry, o.material, count);
    im.frustumCulled = false;
    im.userData.local = o.matrixWorld.clone();
    scene.add(im);
    instanced[name].push(im);
  });
}
const mannequin = models.mannequin.scene;
scene.add(mannequin);

// ---------- 並べる位置（六角形に詰めて、中心から近い順） ----------
const SPOTS = (() => {
  const pts = [];
  const n = Math.ceil(Math.sqrt(MAX_WIDTH)) + 4;
  for (let i = -n; i <= n; i++)
    for (let j = -n; j <= n; j++) {
      const x = (i + j / 2) * SPACING, z = j * SPACING * Math.sqrt(3) / 2;
      pts.push({ x, z, d: Math.hypot(x, z) });
    }
  return pts.sort((a, b) => a.d - b.d || Math.atan2(a.z, a.x) - Math.atan2(b.z, b.x)).slice(0, MAX_WIDTH);
})();

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), s | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- 層の計画 ----------
function plan({ width, order, seed }) {
  const rand = rng(seed);
  const layersOf = (name) => {
    const out = [];
    for (let left = STOCK[name]; left > 0; left -= width) out.push({ name, n: Math.min(width, left) });
    return out;
  };
  const P = layersOf("patty"), C = layersOf("cheese"), O = layersOf("onion");
  let mid;
  if (order === "alt") {
    mid = [];
    while (P.length || C.length || O.length) for (const q of [P, C, O]) if (q.length) mid.push(q.shift());
  } else if (order === "block") {
    mid = [...P, ...C, ...O];
  } else {
    mid = [...P, ...C, ...O];
    for (let i = mid.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [mid[i], mid[j]] = [mid[j], mid[i]];
    }
  }
  return [...layersOf("bun-bottom"), ...mid, ...layersOf("bun-top")];
}

// ---------- 積む ----------
const tmp = new THREE.Matrix4(), item = new THREE.Matrix4(), rotM = new THREE.Matrix4();
function build() {
  const rand = rng(state.seed * 7919 + state.width);
  const layers = plan(state);
  const used = Object.fromEntries(Object.keys(STOCK).map((k) => [k, 0]));
  let y = 0;
  for (const layer of layers) {
    const spin = rand() * Math.PI * 2;
    const cs = Math.cos(spin), sn = Math.sin(spin);
    for (let k = 0; k < layer.n; k++) {
      const p = SPOTS[k];
      const x = p.x * cs - p.z * sn, z = p.x * sn + p.z * cs;
      rotM.makeRotationY(rand() * Math.PI * 2);
      item.makeTranslation(x, y, z).multiply(rotM);
      const i = used[layer.name]++;
      for (const im of instanced[layer.name]) im.setMatrixAt(i, tmp.multiplyMatrices(item, im.userData.local));
    }
    y += models[layer.name].h;
  }
  for (const list of Object.values(instanced))
    for (const im of list) im.instanceMatrix.needsUpdate = true;

  const radius = SPOTS[Math.min(state.width, MAX_WIDTH) - 1].d + 5.3;
  return { height: y, radius, layers: layers.length };
}

let shape;
function rebuild({ refit = true } = {}) {
  shape = build();
  // マネキンは、バーガーの真横に立たせる（手前に置くと、遠近で大きく見えてしまう）
  mannequin.position.set(shape.radius + 45, 0, 0);
  mannequin.rotation.y = -0.5;
  if (refit) fit();
  renderUI();
}

function fit() {
  const { height, radius } = shape;
  const left = -radius, right = shape.radius + 70;
  const w = right - left, h = Math.max(height, 160);
  const fov = (camera.fov * Math.PI) / 180;
  const dist = (Math.max(h, w / camera.aspect) / 2 / Math.tan(fov / 2)) * 1.35 + radius;
  controls.target.set((left + right) / 2, h / 2, 0);
  const dir = new THREE.Vector3(0.25, 0.18, 1).normalize();
  camera.position.copy(controls.target).addScaledVector(dir, dist);
  controls.maxDistance = dist * 4;
  sun.position.set(radius + 200, height + 400, radius + 300);
}

// ---------- UI ----------
const $ = (id) => document.getElementById(id);
const fmt = (n) => n.toLocaleString("en-US");
const len = (cm) => (cm >= 100 ? `${(cm / 100).toFixed(cm >= 1000 ? 1 : 2)} m` : `${cm.toFixed(0)} cm`);
const sliderToWidth = (v) => Math.max(1, Math.round(MAX_WIDTH ** (v / 1000)));
const widthToSlider = (w) => Math.round((Math.log(w) / Math.log(MAX_WIDTH)) * 1000);

function renderUI() {
  $("width").value = widthToSlider(state.width);
  $("widthValue").textContent = `${fmt(state.width)} 個`;
  $("order").replaceChildren(
    ...ORDERS.map((o) => {
      const b = document.createElement("button");
      b.textContent = o.name;
      b.setAttribute("aria-pressed", String(state.order === o.key));
      b.onclick = () => { state.order = o.key; state.seed++; b.blur(); rebuild({ refit: false }); };
      return b;
    }),
  );
  $("copyToggle").setAttribute("aria-pressed", String(state.copy));
  $("copy").hidden = !state.copy;
  $("stock").textContent = STOCK_LABEL;
  const total = Object.values(STOCK).reduce((a, b) => a + b, 0);
  $("price").textContent = `¥${fmt(PRICE)}`;
  $("spec").textContent =
    `高さ ${len(shape.height)}・直径 ${len(shape.radius * 2)}・${fmt(shape.layers)} 層・具材 ${fmt(total)} 個` +
    `　／　${fmt(PEOPLE)} 人で割り勘すると 1人 ¥${fmt(Math.round(PRICE / PEOPLE))}`;
}

$("width").addEventListener("input", (e) => { state.width = sliderToWidth(+e.target.value); rebuild(); });
$("random").onclick = (e) => { randomize(); e.currentTarget.blur(); };
$("copyToggle").onclick = (e) => { state.copy = !state.copy; e.currentTarget.blur(); renderUI(); };
function randomize() {
  state.width = sliderToWidth(Math.floor(Math.random() * 1001));
  state.order = ORDERS[Math.floor(Math.random() * ORDERS.length)].key;
  state.seed = Math.floor(Math.random() * 1e9);
  rebuild();
}
addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT" || ev.repeat) return;
  if (ev.key === "r" || ev.key === "R") randomize();
  if (ev.key === "c" || ev.key === "C") { state.copy = !state.copy; renderUI(); }
});

state.width = 37; // 最初は、塔と円盤のあいだくらい
rebuild();

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});


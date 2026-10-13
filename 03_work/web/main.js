// BUILD BURGER ダイナミックアイデンティティ・ジェネレーター（試作）
// 具材の3Dモデルは 02_assets/models（Blenderの .blend から書き出したGLB）から読む。
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// スキャンした具材（02_assets/blender/make_web_models.py で作る）。代替モデルに戻すなら "../../02_assets/models/"
const MODEL_DIR = "../../02_assets/models-web/";
const MAX_TIERS = 10;
const SAUCES = [
  { key: null, name: "なし", short: "—" },
  { key: "sauce-ketchup", name: "ケチャップ", short: "K" },
  { key: "sauce-relish", name: "レリッシュ", short: "R" },
  { key: "sauce-burger", name: "バーガーソース", short: "B" },
];
const BREADS = [
  { key: "bun", name: "バンズ", bottom: "bun-bottom", top: "bun-top" },
  { key: "patty", name: "パティ", bottom: "patty", top: "patty" },
];

// ---------- 状態 ----------
const state = {
  tiers: [{ cheese: true, onion: true }],
  bread: 0,
  sauceTop: 1,
  sauceBottom: 0,
  grid: false,
};

// ---------- 組み合わせの数と通し番号 ----------
// 段の組み合わせ: Σ 4^n (n=1..10)。×ソース16 ×挟むもの2
const tierCombos = (n) => 4 ** n;
const TIER_TOTAL = Array.from({ length: MAX_TIERS }, (_, i) => tierCombos(i + 1)).reduce((a, b) => a + b);
const TOTAL = TIER_TOTAL * 16 * BREADS.length;
function serialOf(s) {
  let t = 0;
  for (let n = 1; n < s.tiers.length; n++) t += tierCombos(n);
  t += s.tiers.reduce((acc, x) => acc * 4 + (x.cheese ? 2 : 0) + (x.onion ? 1 : 0), 0);
  return (s.bread * 16 + s.sauceTop * 4 + s.sauceBottom) * TIER_TOTAL + t + 1;
}
const fmt = (n) => n.toLocaleString("en-US");

// ---------- three.js ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 1.2;
scene.add(new THREE.HemisphereLight(0xffffff, 0xd8c8b0, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(20, 40, 25);
scene.add(sun);
const fill = new THREE.DirectionalLight(0xffffff, 0.6);
fill.position.set(-30, 10, -20);
scene.add(fill);

const world = new THREE.Group();
scene.add(world);

function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

// ---------- モデル読み込み ----------
const models = {};
const manifest = await (await fetch(MODEL_DIR + "models.json")).json();
const loader = new GLTFLoader();
await Promise.all(
  Object.entries(manifest).map(async ([name, m]) => {
    const gltf = await loader.loadAsync(MODEL_DIR + m.file);
    models[name] = { scene: gltf.scene, h: m.stackHeight };
  }),
);

// ---------- バーガーを組み立てる ----------
// 下から順に、具材を積む。layers の各要素に、閉じた位置 y と、分解したときの段番号を持たせる
function buildBurger(s) {
  const group = new THREE.Group();
  const layers = [];
  let y = 0;
  const put = (name, { flip = false, rot = 0 } = {}) => {
    const m = models[name];
    const obj = m.scene.clone();
    obj.rotation.y = rot;
    if (flip) obj.rotation.x = Math.PI; // 上のバンズの裏に塗ったソースは、下向きに垂れる
    const baseY = flip ? y + m.h : y;
    obj.position.y = baseY;
    group.add(obj);
    layers.push({ obj, baseY, i: layers.length, sauce: name.startsWith("sauce") });
    y += m.h;
  };
  const bread = BREADS[s.bread];
  put(bread.bottom);
  if (SAUCES[s.sauceBottom].key) put(SAUCES[s.sauceBottom].key);
  s.tiers.forEach((t, i) => {
    put("patty", { rot: i * 1.3 });
    if (t.cheese) put("cheese", { rot: 0.4 + i * 0.7 });
    if (t.onion) put("onion", { rot: i * 2.1 });
  });
  if (SAUCES[s.sauceTop].key) put(SAUCES[s.sauceTop].key, { flip: true });
  put(bread.top, { rot: 0.5 });
  group.userData = { layers, height: y };
  return group;
}

let burgers = [];
function rebuild() {
  world.clear();
  burgers = [];
  if (!state.grid) {
    const b = buildBurger(state);
    world.add(b);
    burgers.push(b);
    frame(b.userData.height, 1);
  } else {
    // 並べる: 組み合わせの物量を見せる
    const cols = 9, rows = 5, gap = 14;
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        const b = buildBurger(randomState(true));
        b.position.set((c - (cols - 1) / 2) * gap, 0, (r - (rows - 1) / 2) * gap);
        b.rotation.y = Math.random() * Math.PI * 2;
        world.add(b);
        burgers.push(b);
      }
    frame(20, 4.2);
  }
  renderUI();
}

function frame(height, zoom) {
  const d = Math.max(40, height * 2.4) * zoom;
  controls.target.set(0, state.grid ? 5 : height / 2, 0);
  camera.position.set(d * 0.75, height / 2 + d * 0.45, d * 0.75);
}

function randomState(forGrid = false) {
  const n = 1 + Math.floor(Math.random() ** (forGrid ? 1.6 : 1.2) * MAX_TIERS);
  return {
    tiers: Array.from({ length: n }, () => ({ cheese: Math.random() < 0.6, onion: Math.random() < 0.5 })),
    bread: Math.random() < 0.1 ? 1 : 0,
    sauceTop: Math.floor(Math.random() * 4),
    sauceBottom: Math.floor(Math.random() * 4),
    grid: state.grid,
  };
}

// ---------- 分解（ソースを見る） ----------
let explode = 0, explodeTarget = 0, dblLatched = false;
function applyExplode(t) {
  const e = t * t * (3 - 2 * t);
  for (const b of burgers)
    for (const l of b.userData.layers) l.obj.position.y = l.baseY + l.i * 2.2 * e;
}
addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "BUTTON" && ev.code === "Space") ev.preventDefault();
  if (ev.code === "Space") { explodeTarget = 1; ev.preventDefault(); }
  if (ev.repeat) return;
  if (ev.key === "r" || ev.key === "R") randomize();
  if (ev.key === "g" || ev.key === "G") toggleGrid();
});
addEventListener("keyup", (ev) => {
  if (ev.code === "Space" && !dblLatched) explodeTarget = 0;
});
renderer.domElement.addEventListener("dblclick", () => {
  dblLatched = !dblLatched;
  explodeTarget = dblLatched ? 1 : 0;
});

// ---------- UI ----------
const $ = (id) => document.getElementById(id);
const btn = (label, pressed, onClick) => {
  const b = document.createElement("button");
  b.textContent = label;
  if (pressed !== null) b.setAttribute("aria-pressed", String(pressed));
  b.addEventListener("click", () => { onClick(); b.blur(); });
  return b;
};

function renderUI() {
  $("count").textContent = `${state.tiers.length} 段`;
  const tiers = $("tiers");
  tiers.replaceChildren(
    ...state.tiers.map((t, i) => {
      const row = document.createElement("div");
      row.className = "tier";
      const n = document.createElement("span");
      n.textContent = i + 1;
      row.append(
        n,
        btn("チーズ", t.cheese, () => { t.cheese = !t.cheese; rebuild(); }),
        btn("玉ねぎ", t.onion, () => { t.onion = !t.onion; rebuild(); }),
      );
      return row;
    }).reverse(), // 上の段を上に並べる
  );
  $("bread").replaceChildren(...BREADS.map((b, i) => btn(b.name, state.bread === i, () => { state.bread = i; rebuild(); })));
  const sauceRow = (label, key) => {
    const wrap = document.createElement("div");
    const l = document.createElement("div");
    l.textContent = label;
    const row = document.createElement("div");
    row.className = "row";
    row.append(...SAUCES.map((s, i) => btn(s.name, state[key] === i, () => { state[key] = i; rebuild(); })));
    wrap.append(l, row);
    return wrap;
  };
  $("sauce").replaceChildren(sauceRow("上バンズ", "sauceTop"), sauceRow("下バンズ", "sauceBottom"));
  $("gridToggle").setAttribute("aria-pressed", String(state.grid));
  $("sauceToggle").setAttribute("aria-pressed", String(!$("sauce").hidden));

  const s = state;
  $("serial").textContent = state.grid ? `${fmt(TOTAL)} 通り` : `No. ${fmt(serialOf(s))}`;
  $("spec").textContent = state.grid
    ? "ランダムに 45 個"
    : `/ ${fmt(TOTAL)}　${s.tiers.length}段・${s.tiers.map((t) => (t.cheese ? "C" : "-") + (t.onion ? "O" : "-")).join(" ")}・ソース 上${SAUCES[s.sauceTop].short} 下${SAUCES[s.sauceBottom].short}`;
}

function randomize() {
  if (state.grid) return rebuild();
  Object.assign(state, randomState());
  rebuild();
}
function toggleGrid() {
  state.grid = !state.grid;
  rebuild();
}

$("minus").onclick = () => { if (state.tiers.length > 1) { state.tiers.pop(); rebuild(); } };
$("plus").onclick = () => { if (state.tiers.length < MAX_TIERS) { state.tiers.push({ cheese: true, onion: true }); rebuild(); } };
$("sauceToggle").onclick = (e) => { $("sauce").hidden = !$("sauce").hidden; e.currentTarget.blur(); renderUI(); };
$("random").onclick = (e) => { randomize(); e.currentTarget.blur(); };
$("gridToggle").onclick = (e) => { toggleGrid(); e.currentTarget.blur(); };

rebuild();

// ---------- ループ ----------
let last = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  explode += Math.sign(explodeTarget - explode) * Math.min(Math.abs(explodeTarget - explode), dt * 3);
  applyExplode(explode);
  controls.update();
  renderer.render(scene, camera);
});

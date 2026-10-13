// BUILD BURGER ダイナミックアイデンティティ・ジェネレーター（試作）
// 具材の3Dモデルは 02_assets/models-web（スキャンから作ったGLB）から読む。見た目のパラメタは look.js・look.json（調整は tune.html）
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import GUI from "lil-gui";
import { STORAGE_KEY, DEFAULTS, loadLook, lookInstance, sample, rng, setupLighting } from "./look.js";
import { addLookControls, downloadLook, injectGuiStyle } from "./look-gui.js";

// スキャンした具材（02_assets/blender/make_web_models.py で作る）。代替モデルに戻すなら "../../02_assets/models/"
const MODEL_DIR = "../../02_assets/models-web/";
// 「並べる」では、軽い版（100万円バーガー用）を使う。45個のバーガーを一度に描くので
const LITE_DIR = "../../02_assets/models-web/lite/";
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
// 照明と映り込みは、調整ページ（tune.html）と同じにする（look.js）
setupLighting(scene, renderer);

const world = new THREE.Group();
scene.add(world);

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
const loader = new GLTFLoader();
async function loadModels(dir) {
  const out = {};
  const manifest = await (await fetch(dir + "models.json")).json();
  await Promise.all(
    Object.entries(manifest).map(async ([name, m]) => {
      const gltf = await loader.loadAsync(dir + m.file);
      applyColorScale(gltf.scene, m.colorScale);
      out[name] = { scene: gltf.scene, h: m.stackHeight };
    }),
  );
  return out;
}
const [models, liteModels] = await Promise.all([loadModels(MODEL_DIR), loadModels(LITE_DIR)]);

// ---------- 見た目（調整ページで決めた値） ----------
let look = await loadLook();
// 調整ページを別のタブで開いていじると、すぐ反映する
addEventListener("storage", async (e) => {
  if (e.key !== STORAGE_KEY) return;
  look = await loadLook();
  variantCache = {};
  rebuild({ keepView: true });
  if (gui) openGui(); // パネルが古い値を指したままにならないように、作り直す
});

// ---------- バーガーを組み立てる ----------
// 下から順に、どの具材をどう置くかの並び（具材名・裏返すか・回す角度）
function layersOf(s) {
  const out = [];
  const bread = BREADS[s.bread];
  out.push({ name: bread.bottom });
  if (SAUCES[s.sauceBottom].key) out.push({ name: SAUCES[s.sauceBottom].key });
  s.tiers.forEach((t, i) => {
    out.push({ name: "patty", rot: i * 1.3 });
    if (t.cheese) out.push({ name: "cheese", rot: 0.4 + i * 0.7 });
    if (t.onion) out.push({ name: "onion", rot: i * 2.1 });
  });
  if (SAUCES[s.sauceTop].key) out.push({ name: SAUCES[s.sauceTop].key, flip: true }); // 上のバンズの裏に塗ったソースは、下向きに垂れる
  out.push({ name: bread.top, rot: 0.5 });
  return out;
}

// 1個のバーガー。具材の見た目は、通し番号を種にして「基準値 ± 揺らぎの幅」から決める（同じ番号なら、いつも同じ見た目）。
// layers の各要素に、閉じた位置 y と、分解したときの段番号を持たせる
function buildBurger(s) {
  const group = new THREE.Group();
  const layers = [];
  const rand = rng(serialOf(s));
  let y = 0;
  for (const { name, flip = false, rot = 0 } of layersOf(s)) {
    const m = models[name];
    const entry = look.ingredients[name];
    let obj, h = m.h;
    if (entry) {
      const v = sample(entry, rand);
      const inst = lookInstance(m.scene);
      inst.apply(v);
      obj = inst.object;
      h = m.h * v.scaleY; // 厚みの揺らぎは、積む高さにも効かせる
    } else {
      obj = m.scene.clone();
    }
    obj.rotation.y = rot;
    if (flip) obj.rotation.x = Math.PI;
    const baseY = flip ? y + h : y;
    obj.position.y = baseY;
    group.add(obj);
    layers.push({ obj, baseY, i: layers.length });
    y += h;
  }
  group.userData = { layers, height: y };
  return group;
}

// 「並べる」用の、具材ごとの見た目の型。45個のバーガーの1,600ほどの具材を1つずつ違う見た目にすると、
// 1コマごとに1,600回描くことになって重い。そこで具材ごとに VARIANTS 種類の型を作り、各段にはそのどれかを当てる。
// 同じ型の具材は、インスタンス描画（同じ形を位置だけ変えて1回で描く）でまとめて描く
const VARIANTS = 6;
let variantCache = {};
function variantsOf(name) {
  if (variantCache[name]) return variantCache[name];
  const m = liteModels[name];
  const entry = look.ingredients[name];
  const list = [];
  for (let k = 0; k < (entry ? VARIANTS : 1); k++) {
    let obj, h = m.h;
    if (entry) {
      const v = sample(entry, rng(9000 + k * 131 + name.length * 17));
      v.transmission = 0; // 「透け」は、画面をもう1回描くので重い
      const inst = lookInstance(m.scene);
      inst.apply(v);
      obj = inst.object;
      h = m.h * v.scaleY;
    } else {
      obj = m.scene.clone();
    }
    obj.updateMatrixWorld(true);
    const meshes = [];
    obj.traverse((o) => o.isMesh && meshes.push({ geometry: o.geometry, material: o.material, local: o.matrixWorld.clone() }));
    list.push({ meshes, h });
  }
  return (variantCache[name] = list);
}

let burgers = []; // 1個表示のバーガー
let gridItems = []; // 並べる表示の、インスタンス1つずつ（分解するときに位置を計算し直す）
let gridBurgers = []; // 並べる45個（見た目を調整している間は、同じ45個のまま見た目だけ更新する）
function buildGrid({ reuse = false } = {}) {
  const cols = 9, rows = 5, gap = 14;
  if (!reuse || gridBurgers.length !== cols * rows)
    gridBurgers = Array.from({ length: cols * rows }, () => ({ s: randomState(true), turn: Math.random() * Math.PI * 2 }));
  const groups = new Map(); // 型の部品ごとに、置く場所を集める
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) {
      const { s, turn: burgerTurn } = gridBurgers[r * cols + c];
      const rand = rng(serialOf(s));
      const pre = new THREE.Matrix4().compose(
        new THREE.Vector3((c - (cols - 1) / 2) * gap, 0, (r - (rows - 1) / 2) * gap),
        new THREE.Quaternion().setFromEuler(new THREE.Euler(0, burgerTurn, 0)),
        new THREE.Vector3(1, 1, 1),
      );
      let y = 0;
      layersOf(s).forEach(({ name, flip = false, rot = 0 }, i) => {
        const variants = variantsOf(name);
        const k = Math.floor(rand() * variants.length);
        const { meshes, h } = variants[k];
        const turn = new THREE.Matrix4().makeRotationY(rot);
        if (flip) turn.multiply(new THREE.Matrix4().makeRotationX(Math.PI));
        meshes.forEach((mesh, mi) => {
          const key = `${name}/${k}/${mi}`;
          if (!groups.has(key)) groups.set(key, { mesh, items: [] });
          groups.get(key).items.push({ pre, baseY: flip ? y + h : y, i, post: turn.clone().multiply(mesh.local) });
        });
        y += h;
      });
    }
  gridItems = [];
  for (const { mesh, items } of groups.values()) {
    const im = new THREE.InstancedMesh(mesh.geometry, mesh.material, items.length);
    im.frustumCulled = false;
    items.forEach((it, idx) => gridItems.push({ ...it, im, idx }));
    world.add(im);
  }
  applyExplode(explode);
}

// keepView: 見た目の調整中など。カメラの位置と、並べる45個をそのままにする
function rebuild({ keepView = false } = {}) {
  world.clear();
  renderer.setPixelRatio(state.grid ? 1 : Math.min(devicePixelRatio, 2)); // 並べるときは、描く細かさを下げて軽くする
  resize();
  burgers = [];
  gridItems = [];
  if (!state.grid) {
    const b = buildBurger(state);
    world.add(b);
    burgers.push(b);
    if (!keepView) frame(b.userData.height, 1);
  } else {
    // 並べる: 組み合わせの物量を見せる
    buildGrid({ reuse: keepView });
    if (!keepView) frame(20, 4.2);
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
const tmpM = new THREE.Matrix4(), lift = new THREE.Matrix4();
function applyExplode(t) {
  const e = t * t * (3 - 2 * t);
  for (const b of burgers)
    for (const l of b.userData.layers) l.obj.position.y = l.baseY + l.i * 2.2 * e;
  if (!gridItems.length) return;
  const touched = new Set();
  for (const it of gridItems) {
    lift.makeTranslation(0, it.baseY + it.i * 2.2 * e, 0);
    tmpM.multiplyMatrices(it.pre, lift).multiply(it.post);
    it.im.setMatrixAt(it.idx, tmpM);
    touched.add(it.im);
  }
  for (const im of touched) im.instanceMatrix.needsUpdate = true;
}
addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT") return; // 調整パネルの入力欄に打っているときは、ショートカットを効かせない
  if (ev.target.tagName === "BUTTON" && ev.code === "Space") ev.preventDefault();
  if (ev.code === "Space") { explodeTarget = 1; ev.preventDefault(); }
  if (ev.repeat) return;
  if (ev.key === "r" || ev.key === "R") randomize();
  if (ev.key === "g" || ev.key === "G") toggleGrid();
  if (ev.key === "p" || ev.key === "P") toggleGui();
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

// ---------- 見た目の調整パネル（P で出し入れ） ----------
// 調整ページ（tune.html）と同じパネル。値はこのブラウザに保存され、調整ページと共通
let gui = null;
const tuneState = { name: "onion" };
let pendingRebuild;
function lookChanged() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(look));
  } catch {}
  clearTimeout(pendingRebuild); // スライダーを動かしている間は、少し待ってからまとめて作り直す
  pendingRebuild = setTimeout(() => {
    variantCache = {};
    rebuild({ keepView: true });
  }, 120);
}
function openGui() {
  injectGuiStyle();
  gui?.destroy();
  gui = new GUI({ title: "具材の見た目（P で閉じる）" });
  gui.add(tuneState, "name", Object.keys(look.ingredients)).name("具材").onChange(() => setTimeout(openGui));
  addLookControls(gui, look.ingredients[tuneState.name], lookChanged);
  const tools = gui.addFolder("保存");
  tools.add({ run: () => downloadLook(look) }, "run").name("look.json を書き出す");
  tools.add({ run: () => {
    look.ingredients[tuneState.name] = structuredClone(DEFAULTS[tuneState.name]);
    lookChanged();
    setTimeout(openGui);
  } }, "run").name("この具材を初期値に戻す");
  $("guiToggle").setAttribute("aria-pressed", "true");
}
function toggleGui() {
  if (gui) {
    gui.destroy();
    gui = null;
    $("guiToggle").setAttribute("aria-pressed", "false");
  } else openGui();
}
$("guiToggle").onclick = (e) => { toggleGui(); e.currentTarget.blur(); };

rebuild();

// ---------- ループ ----------
let last = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  const next = explode + Math.sign(explodeTarget - explode) * Math.min(Math.abs(explodeTarget - explode), dt * 3);
  if (next !== explode) {
    explode = next;
    applyExplode(explode); // 分解の量が変わったときだけ、位置を計算し直す
  }
  controls.update();
  renderer.render(scene, camera);
});


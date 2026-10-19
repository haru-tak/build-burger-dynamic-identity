// 具材の見た目（質感）のパラメタ。調整ページ（tune.html）とジェネレーターで共有する。
//
// - パラメタは、具材ごとに「基準値（base）」と「揺らぎの幅（range）」を持つ
// - 1つのバーガーの具材の見た目は、基準値 ± 揺らぎの幅 の中から、乱数で決める（sample）
// - 調整した値は look.json に保存する: { version: 1, ingredients: { 具材名: { base: {...}, range: {...} } } }
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

// パラメタの一覧。min〜max はスライダーの範囲。group は調整パネルでの見出し
export const PARAMS = [
  { key: "hue", label: "色相（ずらす）", min: -0.5, max: 0.5, step: 0.005, group: "色" },
  { key: "saturation", label: "彩度", min: 0, max: 2.5, step: 0.01, group: "色" },
  { key: "brightness", label: "明るさ", min: 0, max: 2.5, step: 0.01, group: "色" },
  { key: "contrast", label: "コントラスト", min: 0.3, max: 2, step: 0.01, group: "色" },
  { key: "tintAmount", label: "色を足す（量）", min: 0, max: 1, step: 0.01, group: "色" },
  { key: "roughness", label: "ざらつき", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "clearcoat", label: "つやの膜", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "clearcoatRoughness", label: "つやの膜のざらつき", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "transmission", label: "透け（上げると後ろの具材が透けて暗く見える）", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "thickness", label: "透ける厚み（cm）", min: 0, max: 3, step: 0.01, group: "質感" },
  { key: "sheen", label: "毛羽・粉っぽさ", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "bump", label: "細かい凹凸（0.25で元のまま）", min: 0, max: 1, step: 0.01, group: "質感" },
  { key: "envMapIntensity", label: "映り込み", min: 0, max: 4, step: 0.01, group: "質感" },
  { key: "scaleXZ", label: "横の大きさ", min: 0.6, max: 1.4, step: 0.01, group: "形" },
  // 表面を、ノイズで押し出したり引っ込めたりする。模様はバーガーごとの乱数でずれるので、1つずつ形が変わる
  { key: "displace", label: "形のでこぼこ（量・cm）", min: 0, max: 0.6, step: 0.005, group: "形" },
  { key: "displaceScale", label: "形のでこぼこ（細かさ）", min: 0.1, max: 4, step: 0.01, group: "形" },
  { key: "scaleY", label: "厚み", min: 0.4, max: 1.8, step: 0.01, group: "形" },
];
// 色を足すときの色（揺らがない）
export const COLOR_PARAMS = [{ key: "tint", label: "色を足す（色）", group: "色" }];

const NEUTRAL = {
  hue: 0, saturation: 1, brightness: 1, contrast: 1, tintAmount: 0, tint: "#ffffff",
  roughness: 0.7, clearcoat: 0, clearcoatRoughness: 0.1, transmission: 0, thickness: 0.5,
  sheen: 0, bump: 0.25, envMapIntensity: 1, scaleXZ: 1, scaleY: 1, displace: 0, displaceScale: 1,
};
const NO_RANGE = Object.fromEntries(PARAMS.map((p) => [p.key, 0]));

// 初期値（たたき台。調整ページで人が追い込む）
const base = (o) => ({ ...NEUTRAL, ...o });
const range = (o) => ({ ...NO_RANGE, ...o });
export const DEFAULTS = {
  "bun-bottom": {
    base: base({ roughness: 0.85, sheen: 0.25, bump: 0.25, saturation: 1.1, envMapIntensity: 0.6 }),
    range: range({ hue: 0.01, brightness: 0.08, scaleXZ: 0.04, scaleY: 0.08 }),
  },
  "bun-top": {
    base: base({ roughness: 0.75, sheen: 0.25, bump: 0.25, saturation: 1.15, envMapIntensity: 0.6 }),
    range: range({ hue: 0.01, brightness: 0.08, scaleXZ: 0.04, scaleY: 0.08 }),
  },
  patty: {
    base: base({ roughness: 0.7, clearcoat: 0.1, clearcoatRoughness: 0.5, bump: 0.4, contrast: 1.15, envMapIntensity: 0.6 }),
    range: range({ brightness: 0.1, scaleXZ: 0.05, scaleY: 0.1 }),
  },
  cheese: {
    base: base({ roughness: 0.55, clearcoat: 0.15, clearcoatRoughness: 0.45, transmission: 0.05, envMapIntensity: 0.7, saturation: 1.2, tint: "#ffb000", tintAmount: 0.15 }),
    range: range({ hue: 0.01, brightness: 0.06, scaleXZ: 0.03 }),
  },
  onion: {
    base: base({ roughness: 0.5, clearcoat: 0.1, clearcoatRoughness: 0.4, transmission: 0, thickness: 0.8, envMapIntensity: 0.7,
      saturation: 0.7, brightness: 1.15, tint: "#efe4c8", tintAmount: 0.25, bump: 0.2, displace: 0.2, displaceScale: 1.3 }),
    range: range({ brightness: 0.1, scaleXZ: 0.08, scaleY: 0.15, displace: 0.06, displaceScale: 0.3 }),
  },
  "sauce-ketchup": {
    base: base({ roughness: 0.3, clearcoat: 0.45, clearcoatRoughness: 0.2, transmission: 0.04, thickness: 0.4,
      saturation: 1.6, brightness: 0.9, contrast: 1.1, tint: "#b8140c", tintAmount: 0.35, envMapIntensity: 1, displace: 0.08, displaceScale: 0.6 }),
    range: range({ brightness: 0.08, scaleXZ: 0.1, scaleY: 0.2, displace: 0.04 }),
  },
  "sauce-relish": {
    base: base({ roughness: 0.4, clearcoat: 0.35, clearcoatRoughness: 0.25, transmission: 0.03, thickness: 0.4,
      hue: 0.06, saturation: 1.4, tint: "#5e8a1e", tintAmount: 0.5, bump: 0.5, envMapIntensity: 0.9, displace: 0.08, displaceScale: 0.9 }),
    range: range({ hue: 0.015, brightness: 0.08, scaleXZ: 0.1, scaleY: 0.2, displace: 0.04 }),
  },
  "sauce-burger": {
    base: base({ roughness: 0.45, clearcoat: 0.3, clearcoatRoughness: 0.3, saturation: 0.9, brightness: 1.25,
      tint: "#f0a070", tintAmount: 0.45, envMapIntensity: 0.9, displace: 0.08, displaceScale: 0.6 }),
    range: range({ brightness: 0.08, scaleXZ: 0.1, scaleY: 0.2, displace: 0.04 }),
  },
};

// 照明のパラメタ（look.json の lighting）。料理写真のように、暖かい主光・縁を光らせる逆光・影で立体感を出す
export const LIGHT_PARAMS = [
  { key: "exposure", label: "全体の明るさ（露出）", min: 0.2, max: 3, step: 0.01 },
  { key: "keyIntensity", label: "主光の強さ", min: 0, max: 10, step: 0.01 },
  { key: "keyAngle", label: "主光の向き（カメラから°）", min: -180, max: 180, step: 1 },
  { key: "keyHeight", label: "主光の高さ（°）", min: 5, max: 89, step: 1 },
  { key: "rimIntensity", label: "逆光（縁の光）の強さ", min: 0, max: 10, step: 0.01 },
  { key: "rimHeight", label: "逆光の高さ（°）", min: 5, max: 89, step: 1 },
  { key: "fillIntensity", label: "まわりの柔らかい光", min: 0, max: 4, step: 0.01 },
  { key: "envIntensity", label: "映り込みの光", min: 0, max: 2, step: 0.01 },
  { key: "shadow", label: "床の影の濃さ", min: 0, max: 1, step: 0.01 },
];
export const LIGHT_COLORS = [
  { key: "keyColor", label: "主光の色" },
  { key: "rimColor", label: "逆光の色" },
  { key: "fillSky", label: "上からの光の色" },
  { key: "fillGround", label: "下からの照り返しの色" },
];
// 色の変換（トーンマッピング）。Neutral は暖色がくすみにくい。ACES は暗く・地味になりやすい
export const TONE_MAPPINGS = { Neutral: THREE.NeutralToneMapping, AgX: THREE.AgXToneMapping, ACES: THREE.ACESFilmicToneMapping };
export const DEFAULT_LIGHTING = {
  toneMapping: "Neutral", exposure: 1.05,
  keyIntensity: 3.2, keyColor: "#ffd6a6", keyAngle: -40, keyHeight: 38,
  rimIntensity: 2.6, rimColor: "#fff0d6", rimHeight: 28,
  fillIntensity: 0.7, fillSky: "#fff4e4", fillGround: "#8a5a3a",
  envIntensity: 0.3, shadow: 0.3,
};

export function defaultLook() {
  return { version: 1, lighting: { ...DEFAULT_LIGHTING }, ingredients: structuredClone(DEFAULTS) };
}

// 調整した値を読む。このブラウザに保存した値 → look.json（チームで共有する値）→ 初期値 の順
export const STORAGE_KEY = "build-burger-look";
export async function loadLook() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return normalizeLook(JSON.parse(saved));
  } catch {}
  try {
    const res = await fetch(new URL("./look.json", import.meta.url), { cache: "no-store" });
    if (res.ok) return normalizeLook(await res.json());
  } catch {}
  return defaultLook();
}

// 照明と映り込み。調整ページとジェネレーターで同じにする（調整ページで見た質感が、そのままジェネレーターでも同じに見えるように）
export function setupLighting(scene, renderer) {
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture; // 液体のつやは、これが映り込むことで出る

  const key = new THREE.DirectionalLight(); // 主光: 暖かい色で斜め前から。影を落とす
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  const rim = new THREE.DirectionalLight(); // 逆光: 後ろから当てて、具材の縁を光らせる
  const hemi = new THREE.HemisphereLight(); // 上からの光と、下からの照り返し
  // 白い床。影だけが見える
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: 0.3 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(key, key.target, rim, rim.target, hemi, ground);

  let L = { ...DEFAULT_LIGHTING };
  let reach = 30; // 光を置く距離
  const dir = (az, el) => new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  return {
    key, rim, hemi, ground,
    // 照明のパラメタを反映する
    apply(lighting) {
      L = { ...DEFAULT_LIGHTING, ...lighting };
      renderer.toneMapping = TONE_MAPPINGS[L.toneMapping] ?? THREE.NeutralToneMapping;
      renderer.toneMappingExposure = L.exposure;
      key.color.set(L.keyColor);
      key.intensity = L.keyIntensity;
      rim.color.set(L.rimColor);
      rim.intensity = L.rimIntensity;
      hemi.color.set(L.fillSky);
      hemi.groundColor.set(L.fillGround);
      hemi.intensity = L.fillIntensity;
      scene.environmentIntensity = L.envIntensity;
      ground.material.opacity = L.shadow;
      ground.visible = L.shadow > 0;
    },
    // 影を落とす範囲を、見せているものの大きさに合わせる（radius: 横の半径、height: 高さ。cm）
    fit(radius, height) {
      const r = Math.max(radius, height) * 1.2 + 2;
      const cam = key.shadow.camera;
      cam.left = cam.bottom = -r;
      cam.right = cam.top = r;
      cam.near = 0.1;
      cam.far = r * 6;
      cam.updateProjectionMatrix();
      // 影の細かい乱れ（シャドウアクネ）を防ぐ。影の画像の1マスの大きさに合わせて、面から浮かせて影を計算する。
      // 主光はカメラに合わせて毎コマ動くので、乱れがあるとチラチラして見える
      const texel = (2 * r) / key.shadow.mapSize.x;
      key.shadow.normalBias = texel * 3;
      key.shadow.bias = -texel * 0.5 / cam.far;
      reach = r * 3;
      ground.scale.setScalar(r * 40);
    },
    // カメラが回っても、カメラから見て同じ向きから光が当たるようにする（毎コマ呼ぶ）
    update(camera, target) {
      const camAz = Math.atan2(camera.position.x - target.x, camera.position.z - target.z);
      const toRad = THREE.MathUtils.degToRad;
      key.position.copy(target).addScaledVector(dir(camAz + toRad(L.keyAngle), toRad(L.keyHeight)), reach);
      key.target.position.copy(target);
      rim.position.copy(target).addScaledVector(dir(camAz + Math.PI + toRad(25), toRad(L.rimHeight)), reach);
      rim.target.position.copy(target);
    },
  };
}

// 足りない項目を初期値で埋める（パラメタを増やしたあとに、古い look.json を読んでも動くように）
export function normalizeLook(look) {
  const out = defaultLook();
  Object.assign(out.lighting, look?.lighting ?? {});
  for (const [name, v] of Object.entries(look?.ingredients ?? {})) {
    if (!out.ingredients[name]) out.ingredients[name] = { base: base({}), range: range({}) };
    Object.assign(out.ingredients[name].base, v.base ?? {});
    Object.assign(out.ingredients[name].range, v.range ?? {});
  }
  return out;
}

// 基準値 ± 揺らぎの幅 の中から、1つ選ぶ。rand は 0〜1 を返す関数
export function sample(entry, rand) {
  const v = { ...entry.base };
  for (const p of PARAMS) {
    const r = entry.range[p.key] ?? 0;
    if (r) v[p.key] = THREE.MathUtils.clamp(v[p.key] + (rand() * 2 - 1) * r, p.min, p.max);
  }
  v.seed = rand() * 100; // 形のでこぼこの模様のずれ（1つずつ形が変わる）
  return v;
}

// 番号から決まる乱数（同じ番号なら、いつも同じ並び）
export function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), s | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- マテリアル ----------

// テクスチャの色を、色相・彩度・明るさ・コントラスト・足す色で補正するシェーダーの差し込み
const GLSL_FUNCS = /* glsl */ `
uniform float uHue, uSat, uVal, uContrast, uTintAmt;
uniform vec3 uTint;
vec3 bbRgb2hsv(vec3 c) {
  vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
  vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
  vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
  float d = q.x - min(q.w, q.y);
  float e = 1.0e-10;
  return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}
vec3 bbHsv2rgb(vec3 c) {
  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}
`;
const GLSL_ADJUST = /* glsl */ `
{
  vec3 c = diffuseColor.rgb;
  vec3 hsv = bbRgb2hsv(c);
  hsv.x = fract(hsv.x + uHue);
  hsv.y = clamp(hsv.y * uSat, 0.0, 1.0);
  hsv.z *= uVal;
  c = bbHsv2rgb(hsv);
  c = max((c - 0.18) * uContrast + 0.18, 0.0);
  float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));
  c = mix(c, uTint * max(lum, 0.0) * 2.0, uTintAmt);
  diffuseColor.rgb = c;
}
`;

// 形のでこぼこ（頂点を法線の向きに押し出す）。陰影の向き（法線）も、押し出した形に合わせて計算し直す
const GLSL_VERT_FUNCS = /* glsl */ `
uniform float uDisp, uDispScale, uSeed;
float bbHash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float bbNoise(vec3 x) {
  vec3 i = floor(x), f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(bbHash(i), bbHash(i + vec3(1, 0, 0)), f.x), mix(bbHash(i + vec3(0, 1, 0)), bbHash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(bbHash(i + vec3(0, 0, 1)), bbHash(i + vec3(1, 0, 1)), f.x), mix(bbHash(i + vec3(0, 1, 1)), bbHash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float bbDisp(vec3 p) {
  vec3 q = p * uDispScale + uSeed;
  return (bbNoise(q) + 0.5 * bbNoise(q * 2.03 + 7.1) - 0.75) * uDisp;
}
`;
const GLSL_VERT_NORMAL = /* glsl */ `
if (uDisp > 0.0) {
  float e = 0.05;
  vec3 n = normalize(objectNormal);
  float d0 = bbDisp(position);
  vec3 g = vec3(bbDisp(position + vec3(e, 0, 0)), bbDisp(position + vec3(0, e, 0)), bbDisp(position + vec3(0, 0, e))) - d0;
  g /= e;
  objectNormal = normalize(n - (g - dot(g, n) * n));
}
`;
const GLSL_VERT_POSITION = /* glsl */ `
if (uDisp > 0.0) transformed += normalize(normal) * bbDisp(position);
`;

// 元のスキャンのマテリアルから、調整できるマテリアルを作る
export function makeLookMaterial(original) {
  const m = new THREE.MeshPhysicalMaterial({
    map: original.map ?? null,
    color: original.color.clone(),
    vertexColors: original.vertexColors,
    side: original.side,
    ior: 1.4,
  });
  // 細かい凹凸: 法線マップ（make_web_models.py で、面を減らす前の形から焼き込んだもの）があればそれを使う。
  // なければ、テクスチャの濃淡から作る（バンプ）
  if (original.normalMap) {
    m.normalMap = original.normalMap;
    m.userData.normalScale = original.normalScale.clone();
  } else {
    m.bumpMap = original.map ?? null;
  }
  m.userData.uniforms = {
    uHue: { value: 0 }, uSat: { value: 1 }, uVal: { value: 1 }, uContrast: { value: 1 },
    uTintAmt: { value: 0 }, uTint: { value: new THREE.Color(1, 1, 1) },
    uDisp: { value: 0 }, uDispScale: { value: 1 }, uSeed: { value: 0 },
  };
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, m.userData.uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\n" + GLSL_VERT_FUNCS)
      .replace("#include <beginnormal_vertex>", "#include <beginnormal_vertex>\n" + GLSL_VERT_NORMAL)
      .replace("#include <begin_vertex>", "#include <begin_vertex>\n" + GLSL_VERT_POSITION);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\n" + GLSL_FUNCS)
      .replace("#include <map_fragment>", "#include <map_fragment>\n" + GLSL_ADJUST);
  };
  m.customProgramCacheKey = () => "bb-look";
  return m;
}

// パラメタの値を、マテリアルに反映する
export function applyLook(material, v) {
  const u = material.userData.uniforms;
  u.uHue.value = v.hue;
  u.uSat.value = v.saturation;
  u.uVal.value = v.brightness;
  u.uContrast.value = v.contrast;
  u.uTintAmt.value = v.tintAmount;
  u.uTint.value.set(v.tint).convertSRGBToLinear();
  u.uDisp.value = v.displace;
  u.uDispScale.value = v.displaceScale;
  u.uSeed.value = v.seed ?? 0;
  material.roughness = v.roughness;
  material.clearcoat = v.clearcoat;
  material.clearcoatRoughness = v.clearcoatRoughness;
  material.transmission = v.transmission;
  material.thickness = v.thickness;
  material.sheen = v.sheen;
  material.sheenRoughness = 0.6;
  material.sheenColor.setRGB(1, 0.95, 0.85);
  // 「でこぼこ」は、法線マップがあればその強さ（0.25 で元のまま）、なければバンプの強さ
  if (material.normalMap) material.normalScale.copy(material.userData.normalScale).multiplyScalar(v.bump * 4);
  else material.bumpScale = v.bump * 3;
  material.envMapIntensity = v.envMapIntensity;
}

// 具材のモデル（GLBの scene）を複製して、調整できるマテリアルに差し替える。
// 戻り値の apply(v) で、パラメタを反映する（形の大きさも変える）
export function lookInstance(scene) {
  const obj = scene.clone(true);
  const made = new Map(); // 元のマテリアル → 調整できるマテリアル。部品どうしで共有しているものは、1つだけ作る（軽くするため）
  obj.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = o.receiveShadow = true;
    o.userData.originalMaterial = o.material;
    if (!made.has(o.material)) made.set(o.material, makeLookMaterial(o.material));
    o.material = made.get(o.material);
  });
  const mats = [...made.values()];
  return {
    object: obj,
    apply(v) {
      for (const m of mats) applyLook(m, v);
      obj.scale.set(v.scaleXZ, v.scaleY, v.scaleXZ);
    },
    // 元のスキャンの見た目と切り替える（比べる用）
    showOriginal(on) {
      obj.traverse((o) => {
        if (!o.isMesh) return;
        if (on && !o.userData.lookMaterial) {
          o.userData.lookMaterial = o.material;
          o.material = o.userData.originalMaterial;
        } else if (!on && o.userData.lookMaterial) {
          o.material = o.userData.lookMaterial;
          o.userData.lookMaterial = null;
        }
      });
    },
  };
}

// 部品の多いモデルを、マテリアルごとに1つの形にまとめる。位置・向き・大きさは、まとめる前と同じ
export function bakeParts(obj) {
  obj.updateMatrixWorld(true);
  const inv = obj.matrixWorld.clone().invert();
  const byMat = new Map();
  obj.traverse((o) => {
    if (!o.isMesh) return;
    const g = o.geometry.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld));
    if (!byMat.has(o.material)) byMat.set(o.material, []);
    byMat.get(o.material).push(g);
  });
  const out = new THREE.Group();
  for (const [m, gs] of byMat) {
    const merged = mergeGeometries(gs, false);
    if (!merged) return obj; // まとめられないときは、そのまま使う
    out.add(new THREE.Mesh(merged, m));
  }
  out.position.copy(obj.position);
  out.quaternion.copy(obj.quaternion);
  out.scale.copy(obj.scale);
  return out;
}

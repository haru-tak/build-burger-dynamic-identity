// 具材の見た目の調整パネル（lil-gui）。調整ページ（tune.html）とジェネレーター（index.html）で共有する
import { PARAMS, COLOR_PARAMS } from "./look.js";

// 1つの具材の「基準値」「揺らぎの幅」のスライダーを、gui に足す。
// onChange(kind) は値が変わるたびに呼ばれる（kind は "base" か "range"）
export function addLookControls(gui, entry, onChange) {
  const baseFolder = gui.addFolder("基準値");
  const groups = {};
  for (const p of [...COLOR_PARAMS, ...PARAMS]) {
    const g = (groups[p.group] ??= baseFolder.addFolder(p.group));
    if (p.min === undefined) g.addColor(entry.base, p.key).name(p.label).onChange(() => onChange("base"));
    else g.add(entry.base, p.key, p.min, p.max, p.step).name(p.label).onChange(() => onChange("base"));
  }
  const rangeFolder = gui.addFolder("揺らぎの幅（±）");
  rangeFolder.close();
  const rgroups = {};
  for (const p of PARAMS) {
    const g = (rgroups[p.group] ??= rangeFolder.addFolder(p.group));
    g.add(entry.range, p.key, 0, (p.max - p.min) / 2, p.step).name(p.label).onChange(() => onChange("range"));
  }
  return { baseFolder, rangeFolder };
}

// 調整した値を look.json としてダウンロードする
export function downloadLook(look) {
  const blob = new Blob([JSON.stringify(look, null, 2) + "\n"], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "look.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

// パネルを白黒にそろえるCSS（ページの <style> に入れる代わりに、ここで足す）
export function injectGuiStyle({ width = 300 } = {}) {
  if (document.getElementById("look-gui-style")) return;
  const st = document.createElement("style");
  st.id = "look-gui-style";
  st.textContent = `
    .lil-gui {
      --background-color: #fff; --text-color: #000; --title-background-color: #fff; --title-text-color: #000;
      --widget-color: #eee; --hover-color: #ddd; --focus-color: #ccc; --number-color: #000; --string-color: #000;
      --font-family: "Yu Gothic", "YuGothic", "游ゴシック", "Hiragino Sans", sans-serif; --font-size: 12px; --name-width: 42%;
      border-left: 1px solid #000;
    }
    .lil-gui.root { position: fixed; top: 0; right: 0; width: ${width}px; max-height: 100%; overflow-y: auto; z-index: 10; }
    .lil-gui .title { font-weight: 700; }
  `;
  document.head.append(st);
}

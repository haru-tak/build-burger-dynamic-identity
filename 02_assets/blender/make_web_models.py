# 組み合わせたスキャン（02_assets/models-merged/*.glb）を、Webアプリ用にして 02_assets/models-web/ に書き出す。
# 先に trim_scans.py → merge_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/make_web_models.py
#   （1つだけ: ... -P make_web_models.py -- patty）
#
# - アプリ（03_work/web）は、models.json の約束で読む: 単位cm、底の中心が原点、stackHeight が積む厚み
# - 2種類書き出す
#   models-web/      ジェネレーター用
#   models-web/lite/ 100万円バーガー用・ジェネレーターの「並べる」用（たくさん並べるので軽くする）
# - マネキンは、代替モデル（02_assets/models/mannequin.glb）をそのまま使う
#
# 整った形（SHAPES）
#   スキャンのボコボコや溶けたような形をなくすため、スキャンの輪郭と高さに合わせた整った形を作り、そこへスキャンの色を写し取る。
#
# テクスチャの焼き直し（ベイク）
#   元のスキャンのテクスチャは、机や紙も写った8K画像で、具材が写っているのはその一部だけ。そのまま縮めると具材がぼやける。
#   そこで、面を減らした具材に新しく展開図（UV）を作り、元の細かいモデルの色を、その展開図の画像に写し取る。
#   - 色: 元の色に、継ぎ目の明るさ合わせ（頂点カラー × COLOR_SCALE。merge_scans.py）も掛けて焼き込む
#   - 法線マップ: 面を減らす前の細かい凹凸を、陰影の向きの画像として焼き込む（面が少なくても、細かい凹凸が見える）

import bmesh
import bpy
import json
import math
import os
import shutil
import sys

import numpy as np
from mathutils import Matrix

here = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.normpath(os.path.join(here, "..", "models-merged"))
out_dir = os.path.normpath(os.path.join(here, "..", "models-web"))
placeholder_dir = os.path.normpath(os.path.join(here, "..", "models"))

# アプリの具材名 → 使うスキャン
USE = {
    "bun-bottom": "bans-shita-01",
    "bun-top": "bans-ue-01",
    "patty": "patty-01",
    "cheese": "cheese-square",
    "onion": "onion",  # かけらに割った版（fracture_onion.py → onion-chopped）は使わない。凸凹はWebで付ける（look.js）
    "sauce-ketchup": "ketchup",
    "sauce-relish": "relish",
    "sauce-burger": "sauce",
}
# merge_scans.py の頂点カラーの倍率（継ぎ目の明るさ合わせ）。ベイクのときに色へ焼き込む
COLOR_SCALE = 2.0
# 積む厚み = 実際の高さ × この値（スキャンは表面がでこぼこなので、少し重ねると隙間が出ない）
OVERLAP = 0.85

LEVELS = [
    ("", 15000, 2048),  # (サブフォルダ, 面の数, テクスチャの大きさpx)。整った形（SHAPES）にする具材は、面の数は形の作り方で決まる
    ("lite", 400, 512),
]

# 整った形（スキャンのボコボコや、溶けたような形をなくす）。スキャンの輪郭と高さに合わせて作り、スキャンの色を写し取る。
# None の具材は、スキャンの形のまま面を減らす（刻み玉ねぎは、そもそも不揃いな山なので）
#   kind: dome（上バンズ）/ puck（縁の丸い円盤）/ slab（チーズの薄い板）/ puddle（ソースの液だまり）
#   harmonics: 輪郭のなめらかさ（少ないほど丸く、多いほど元の輪郭の角や凹みが残る）
#   height: 高さ（cm）。None ならスキャンの高さ
SHAPES = {
    "bun-top": {"kind": "dome", "harmonics": 5, "height": None},
    "bun-bottom": {"kind": "puck", "harmonics": 5, "height": None, "edge_bottom": 0.35, "edge_top": 0.5, "dome": 0.15},
    "patty": {"kind": "puck", "harmonics": 6, "height": None, "edge_bottom": 0.35, "edge_top": 0.4, "dome": 0.12},
    "cheese": {"kind": "slab", "harmonics": 18, "height": 0.28},
    "onion": None,
    "sauce-ketchup": {"kind": "puddle", "harmonics": 8, "height": 0.45},
    "sauce-relish": {"kind": "puddle", "harmonics": 8, "height": 0.55},
    "sauce-burger": {"kind": "puddle", "harmonics": 8, "height": 0.45},
}
CLEAN_CAGE = 1.0  # 整った形とスキャンは数mmずれるので、ベイクで探す距離を長めにする（cm）
BAKE_SAMPLES = 4
CAGE = 0.3  # ベイクで、細かいモデルを探す距離（cm）

only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
USE_SHAPES = False  # True にすると、整った形（SHAPES）にする。2026-10-14、テクスチャがノイズっぽく見えるため、スキャンの形に戻した


def load(scan_name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(src_dir, f"{scan_name}.glb"))
    ob = next(o for o in bpy.data.objects if o.type == "MESH")
    for o in [o for o in bpy.data.objects if o is not ob]:
        bpy.data.objects.remove(o)
    ob.data.transform(Matrix.Scale(100, 4) @ ob.matrix_world)  # m → cm
    ob.parent = None
    ob.matrix_world = Matrix()
    # GLBはテクスチャの継ぎ目で頂点が分かれているので、つなぎ直す。分かれたまま面を減らすと、穴だらけになる
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bm.to_mesh(ob.data)
    bm.free()
    return ob


def bake_source_materials(high):
    """元のモデルのマテリアルを、ベイク用に「テクスチャの色 × 頂点カラー × COLOR_SCALE」を出すようにする"""
    attr = high.data.color_attributes.active_color
    for m in high.data.materials:
        if not (m and m.use_nodes):
            continue
        nt = m.node_tree
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        base_in = bsdf.inputs["Base Color"]
        src = base_in.links[0].from_socket if base_in.links else None
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = attr.name if attr else ""
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "MULTIPLY"
        scale = nt.nodes.new("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        scale.inputs["Scale"].default_value = COLOR_SCALE if attr else 1.0
        if src is not None and src.node.type != "VERTEX_COLOR":
            nt.links.new(src, mul.inputs[0])
            nt.links.new(vc.outputs["Color"], mul.inputs[1])
            nt.links.new(mul.outputs["Vector"], scale.inputs[0])
        else:
            # 継ぎ目の帯（頂点カラーだけの色）や、色が値だけのマテリアル
            if src is not None:
                nt.links.new(src, scale.inputs[0])
            else:
                scale.inputs[0].default_value = base_in.default_value[:3]
        nt.links.new(scale.outputs["Vector"], base_in)


def outline(high, harmonics):
    """スキャンを上から見た輪郭を「角度（1°ごと）→ 中心からの距離」にし、細かいガタつきを取り除く（フーリエ級数の低い成分だけ残す）"""
    co = np.array([v.co[:] for v in high.data.vertices])
    lo, hi = co[:, :2].min(axis=0), co[:, :2].max(axis=0)
    c = (lo + hi) / 2
    d = co[:, :2] - c
    ang = (np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 360).astype(int) % 360
    r = np.full(360, np.nan)
    np.fmax.at(r, ang, np.hypot(d[:, 0], d[:, 1]))
    ok = ~np.isnan(r)
    idx = np.arange(360)
    r = np.interp(idx, idx[ok], r[ok], period=360)
    f = np.fft.rfft(r)
    f[harmonics + 1:] = 0
    return c, np.fft.irfft(f, n=360), co[:, 2].max()


def profile_of(shape, H, R):
    """断面の形: 下の中心から上の中心までの点 (ρ, z) の並び。ρ は輪郭に対する割合（0〜1）、z は cm。R は平均の半径（cm）"""
    kind = shape["kind"]
    pts = []

    def arc(cx, cz, rx, rz, a0, a1, n):
        for t in np.linspace(a0, a1, n):
            pts.append((cx + rx * math.cos(t), cz + rz * math.sin(t)))

    if kind == "dome":
        # 平らな底 → 小さく丸い縁 → 丸い肩のドーム（超楕円）
        e = 0.12 * H
        pts += [(0, 0), (0.5, 0), (1 - e / R, 0)]
        arc(1 - e / R, e, e / R, e, -math.pi / 2, 0, 5)
        for t in np.linspace(0, math.pi / 2, 18)[1:]:
            pts.append((math.cos(t) ** (2 / 2.6), e + (H - e) * math.sin(t) ** (2 / 2.6)))
        pts[-1] = (0, H)
        return pts
    if kind in ("puck", "slab", "puddle"):
        if kind == "puck":
            eb, et, dome = shape["edge_bottom"] * H, shape["edge_top"] * H, shape["dome"] * H
        elif kind == "slab":
            eb = et = 0.45 * H
            dome = 0
        else:  # puddle: 底はほぼ角、縁は表面張力で大きく丸く、真ん中は少し盛り上がる
            eb, et, dome = 0.08 * H, 0.75 * H, 0.15 * H
        top = H - dome
        pts += [(0, 0), (0.5, 0), (1 - eb / R, 0)]
        arc(1 - eb / R, eb, eb / R, eb, -math.pi / 2, 0, 5)
        if top - et > eb:
            pts.append((1, top - et))
        arc(1 - et / R, top - et, et / R, et, 0, math.pi / 2, 8)
        r0 = 1 - et / R
        for k in np.linspace(0, 1, 8)[1:]:
            rho = r0 * (1 - k)
            pts.append((rho, top + dome * (1 - (rho / r0) ** 2)))
        return pts
    raise ValueError(kind)


def make_clean(high, shape, angles):
    """スキャンの輪郭と高さに合わせた、整った形を作る"""
    c, r, h_scan = outline(high, shape["harmonics"])
    H = shape["height"] or h_scan
    R = r.mean()
    prof = profile_of(shape, H, R)
    bm = bmesh.new()
    rows = []
    for rho, z in prof:
        if rho <= 1e-6:
            rows.append([bm.verts.new((c[0], c[1], z))] * angles)
            continue
        row = []
        for j in range(angles):
            a = j / angles * 360
            rr = np.interp(a, np.arange(360), r, period=360) * rho
            row.append(bm.verts.new((c[0] + rr * math.cos(math.radians(a)), c[1] + rr * math.sin(math.radians(a)), z)))
        rows.append(row)
    for ra, rb in zip(rows, rows[1:]):
        for j in range(angles):
            vs = list(dict.fromkeys([ra[j], ra[(j + 1) % angles], rb[(j + 1) % angles], rb[j]]))
            if len(vs) >= 3:
                bm.faces.new(vs)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("clean")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("clean", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def fill_empty(img):
    """ベイクで色が見つからなかった所（真っ黒のまま）を、まわりの色で埋める。
    画像を半分ずつ小さくしながら「色のある所の平均」を作り、大きい方へ戻すときに、空いている所だけをそれで埋める（push-pull）。
    スキャンの穴の所や、展開図のすき間が埋まる（すき間を埋めておくと、遠くから見たときに縁がにじまない）"""
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    rgb = px[:, :, :3].astype(np.float64)
    valid = rgb.sum(axis=2) > 0.004

    def fill(c, m):
        if min(c.shape[:2]) <= 1:
            mean = c[m].mean(axis=0) if m.any() else np.zeros(3)
            out = c.copy()
            out[~m] = mean
            return out
        hh, ww = c.shape[0] // 2 * 2, c.shape[1] // 2 * 2
        cs = c[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2, 3)
        ms = m[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2)
        cnt = ms.sum(axis=(1, 3))
        down = (cs * ms[..., None]).sum(axis=(1, 3)) / np.maximum(cnt, 1)[..., None]
        down = fill(down, cnt > 0)
        up = np.repeat(np.repeat(down, 2, axis=0), 2, axis=1)
        # 戻すときにぼかして、ブロック状のカクカクを消す
        for _ in range(2):
            pad = np.pad(up, ((1, 1), (1, 1), (0, 0)), mode="edge")
            up = sum(pad[dy:dy + up.shape[0], dx:dx + up.shape[1]] for dy in range(3) for dx in range(3)) / 9
        out = c.copy()
        hole = ~m[:hh, :ww]
        out[:hh, :ww][hole] = up[hole]
        return out

    px[:, :, :3] = fill(rgb, valid)
    px[:, :, 3] = 1
    img.pixels.foreach_set(px.ravel())


def remove_highlights(img, size=24, brighter=1.25, sat_drop=0.7):
    """スキャンのときの光の反射（まわりより明るく、色の薄い点）を、まわりの色で塗りつぶす。
    液体（ソース・チーズ）は表面が光るので、白や黄色の点がテクスチャに焼き込まれている。
    つやは three.js の質感（つやの膜・映り込み）で出すので、テクスチャからは消しておく"""
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    rgb = px[:, :, :3].astype(np.float64)

    def box(a, r):  # 箱型にぼかす（累積和で速く）
        c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r), (0, 0)), mode="edge"), axis=0), axis=1)
        k = 2 * r + 1
        return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)

    local = box(rgb, size)
    lum, llum = rgb.mean(axis=2), local.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    lsat = local.max(axis=2) - local.min(axis=2)
    spot = (lum > llum * brighter) & (sat < lsat * sat_drop + 0.02)
    # 光った点の少し外側まで、なめらかに置き換える
    m = box(spot[..., None].astype(np.float64), 3)[..., 0]
    m = np.clip(m * 3, 0, 1)[..., None]
    px[:, :, :3] = rgb * (1 - m) + local * m
    img.pixels.foreach_set(px.ravel())


def keep_top_only(high):
    """チーズ・ソースは、2つのスキャンを背中合わせに貼った作り（merge_scans.py の fit）で、底のすぐ近くに縫い合わせの帯が
    水平に広がっている。底から色を探すと、その帯や、帯のすき間から見える裏側を拾って、放射状の筋になる。
    裏面はもともと実物の裏ではないので、表面のスキャン（マテリアル0番。merge_scans.py で先に読んだ方）だけから写し取る。
    底からは、表面のスキャンを下から見ることになるので、上と同じ模様が自然につながる"""
    bm = bmesh.new()
    bm.from_mesh(high.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != 0], context="FACES")
    bm.to_mesh(high.data)
    bm.free()


def bake_onto(high, low, app_name, tex, cage, top_only, smooth=True):
    """細かいモデル high の色と凹凸を、Web用のモデル low に焼き込む（新しい展開図・色の画像・法線マップ）。終わったら high は消す。
    smooth=False なら、low の陰影の付け方（角を立てるかどうか）はそのままにする"""
    # 新しい展開図
    low.data.uv_layers.new(name="UVMap")
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")
    if smooth:
        bpy.ops.object.shade_smooth()

    # 焼き込み先の画像と、Web用のマテリアル
    color_img = bpy.data.images.new(f"{app_name}-color", tex, tex)
    normal_img = bpy.data.images.new(f"{app_name}-normal", tex, tex)
    normal_img.colorspace_settings.name = "Non-Color"
    mat = bpy.data.materials.new(app_name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.7
    cn = nt.nodes.new("ShaderNodeTexImage")
    cn.image = color_img
    nn = nt.nodes.new("ShaderNodeTexImage")
    nn.image = normal_img
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    low.data.materials.append(mat)

    # ベイク（細かいモデル high → Web用 low）
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = BAKE_SAMPLES
    bake = sc.render.bake
    bake.use_selected_to_active = True
    bake.cage_extrusion = cage
    bake.max_ray_distance = cage * 2
    bake.margin = 8
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low

    nt.nodes.active = cn
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_clear=True)
    nt.nodes.active = nn
    bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", use_clear=True)
    fill_empty(color_img)
    fill_empty(normal_img)
    if top_only:
        remove_highlights(color_img)

    # 焼き込んだ画像をつなぐ（色 → 基本色、法線マップ → 法線）
    nt.links.new(cn.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(nn.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    color_img.pack()
    normal_img.pack()
    bpy.data.objects.remove(high)


def build(app_name, scan_name, faces, tex):
    high = load(scan_name)
    high.name = "high"
    bake_source_materials(high)

    shape = SHAPES.get(app_name) if USE_SHAPES else None
    top_only = (SHAPES.get(app_name) or {}).get("kind") in ("slab", "puddle")
    if shape:
        # 整った形（輪郭の細かさは、軽い版では粗くする）
        low = make_clean(high, shape, angles=128 if faces > 1000 else 32)
        low.name = low.data.name = app_name
    else:
        # スキャンの形のまま、面を減らす
        low = high.copy()
        low.data = high.data.copy()
        low.name = low.data.name = app_name
        bpy.context.scene.collection.objects.link(low)
        if len(low.data.polygons) > faces:
            mod = low.modifiers.new("decimate", "DECIMATE")
            mod.ratio = faces / len(low.data.polygons)
            bpy.context.view_layer.objects.active = low
            bpy.ops.object.modifier_apply(modifier=mod.name)
        for a in list(low.data.color_attributes):
            low.data.color_attributes.remove(a)
        while low.data.uv_layers:
            low.data.uv_layers.remove(low.data.uv_layers[0])
        low.data.materials.clear()

    if top_only:
        keep_top_only(high)
    bake_onto(high, low, app_name, tex, CLEAN_CAGE if (shape or top_only) else CAGE, top_only)
    height = max(v.co.z for v in low.data.vertices)
    return low, round(float(height * OVERLAP), 3)


def main():
    for sub, faces, tex in LEVELS:
        d = os.path.join(out_dir, sub)
        os.makedirs(d, exist_ok=True)
        manifest_path = os.path.join(d, "models.json")
        manifest = json.load(open(manifest_path)) if only and os.path.exists(manifest_path) else {}
        for app_name, scan_name in USE.items():
            if only and app_name not in only:
                continue
            ob, h = build(app_name, scan_name, faces, tex)
            bpy.ops.object.select_all(action="DESELECT")
            ob.select_set(True)
            path = os.path.join(d, f"{app_name}.glb")
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_yup=True,
                                      export_image_format="JPEG", export_jpeg_quality=90)
            manifest[app_name] = {"file": f"{app_name}.glb", "stackHeight": h, "scan": scan_name}
            print(f"## {sub or 'full'} {app_name} ← {scan_name}: {len(ob.data.polygons)}面 {tex}px 厚み{h}cm "
                  f"{os.path.getsize(path) / 1024:.0f}KB")
        shutil.copy(os.path.join(placeholder_dir, "mannequin.glb"), os.path.join(d, "mannequin.glb"))
        manifest["mannequin"] = {"file": "mannequin.glb", "stackHeight": 160.0}
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")


# ほかのスクリプト（make_hybrid_models.py）から、関数だけを使うときは実行しない
if __name__ == "__main__":
    main()

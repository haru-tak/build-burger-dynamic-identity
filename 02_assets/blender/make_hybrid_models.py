# モデル版の形（02_assets/models、最初にBlenderで作ったローポリ）に、スキャンの色と凹凸を写し取る。
# 結果は 02_assets/models-hybrid/。先に trim_scans.py → merge_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/make_hybrid_models.py
#   （1つだけ: ... -P make_hybrid_models.py -- patty）
#
# やり方
# 1. モデル版の具材（形）と、スキャンを組み合わせたモデル（色）を読み、スキャンをモデル版の大きさに合わせて伸び縮みさせて重ねる
# 2. 色を写し取る。具材によって2通り
#    - 表面に沿って写す（make_web_models.py と同じ焼き込み）: パティ・下バンズ・玉ねぎ
#    - 上と下から投影する（PLANAR）: 上バンズ・チーズ・ソース。モデル版とスキャンの形が違いすぎて、表面に沿って探すと
#      色が見つからない所が出るので、スキャンを真上・真下から見た色を、モデルに上下から当てる。モデル全体が必ず覆われる
# 3. 色をモデル版の色に寄せる（スキャンはくすんでいるので）。模様（明るさの細かい変化）は残し、色味だけをモデル版に近づけ、
#    彩度も少し上げる（COLOR_MATCH・SATURATION）
# 4. ごま（KEEP_PLAIN）は、テクスチャを貼らずにモデル版の色のまま残す
# 形も積む厚みもモデル版のまま（models.json の stackHeight をそのまま使う）

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
# 焼き込みの関数は make_web_models.py のものを使う（実行部分は動かさない）
lib = {"__name__": "make_web_models_lib", "__file__": os.path.join(here, "make_web_models.py")}
exec(open(lib["__file__"]).read(), lib)

model_dir = os.path.normpath(os.path.join(here, "..", "models"))
out_dir = os.path.normpath(os.path.join(here, "..", "models-hybrid"))
TEX = 1024  # 形がローポリで小さいので、スキャン版（2048）より小さくてよい
CAGE = 0.8  # 表面に沿って写すとき、色を探す距離（cm）
FLAT = {"onion"}  # 角を立てたままにする具材（玉ねぎは、さいの目の角が見えるように）
PLANAR = {"bun-top", "cheese", "sauce-ketchup", "sauce-relish", "sauce-burger"}  # 上・下・横から投影する具材
SIDE_LIMIT = 0.5  # 面の向き（上下の成分）がこれより小さい、立った面は横から当てる
KEEP_PLAIN = ()  # この名前で始まるマテリアルの面は、テクスチャを貼らずにモデル版の色のまま
REMOVE = ("sesame",)  # この名前で始まるマテリアルの部品は、取り除く（ごまはいらない。2026-10-19）
ALIGN_SQUARE = {"cheese"}  # スキャンを、四角の向きがモデル版とそろうように回す（斜めに置いて撮ったチーズ）
SIDE_FROM_TOP = {"bun-top"}  # 縁（立った面）に、横から取った色ではなく、上から見た色の縁近くを外へ引き延ばして当てる
COLOR_MATCH = 0.85  # 色をモデル版の色に寄せる強さ（0: スキャンのまま、1: 色味は完全にモデル版）
SATURATION = 1.15  # 最後に彩度を上げる倍率

only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def srgb(c):
    return np.clip(np.asarray(c[:3], dtype=np.float64), 0, 1) ** (1 / 2.2)


def material_color(m):
    """マテリアルの基本色（sRGB）"""
    bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if m and m.use_nodes else None
    return srgb(bsdf.inputs["Base Color"].default_value if bsdf else m.diffuse_color)


def load_model(app_name):
    """モデル版の具材を読んで、部品を1つにまとめる（単位cm、底の中心が原点）。
    戻り値: (形 low, 面ごとの元のマテリアル名, 面ごとの元の色)"""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(model_dir, f"{app_name}.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        o.data = o.data.copy()
        o.data.transform(o.matrix_world)
        o.parent = None
        o.matrix_world = Matrix()
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    low = bpy.context.view_layer.objects.active
    low.name = low.data.name = app_name
    remove_parts(low)
    mats = low.data.materials
    face_mat = [mats[p.material_index].name if mats else "" for p in low.data.polygons]
    face_col = np.array([material_color(mats[p.material_index]) if mats else (0.5, 0.5, 0.5) for p in low.data.polygons])
    for a in list(low.data.color_attributes):
        low.data.color_attributes.remove(a)
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])
    low.data.materials.clear()
    return low, face_mat, face_col


def remove_parts(ob):
    """REMOVE の名前のマテリアルの面（ごまなど）を取り除く"""
    mats = ob.data.materials
    drop = {i for i, m in enumerate(mats) if m and m.name.startswith(REMOVE)}
    if not drop:
        return
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in drop], context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(ob.data)
    bm.free()


def align_square(high):
    """上から見た外枠がいちばん小さくなる向きに回す（四角いものの辺が、x・y の向きにそろう）"""
    co = np.array([v.co[:] for v in high.data.vertices])
    best = min(range(0, 90), key=lambda deg: np.prod(np.ptp(co[:, :2] @ np.array(
        [[math.cos(math.radians(deg)), -math.sin(math.radians(deg))], [math.sin(math.radians(deg)), math.cos(math.radians(deg))]]), axis=0)))
    high.data.transform(Matrix.Rotation(math.radians(best), 4, "Z"))
    return best


def bbox(ob):
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return co.min(axis=0), co.max(axis=0)


def fit(high, low):
    """スキャン（high）を、モデル版（low）の大きさに合わせて伸び縮みさせ、重ねる"""
    hmin, hmax = bbox(high)
    lmin, lmax = bbox(low)
    scale = (lmax - lmin) / np.maximum(hmax - hmin, 1e-6)
    hc, lc = (hmin + hmax) / 2, (lmin + lmax) / 2
    high.data.transform(Matrix.Translation((lc[0], lc[1], lmin[2])) @ Matrix.Diagonal((*scale, 1))
                        @ Matrix.Translation((-hc[0], -hc[1], -hmin[2])))


def pixels(img):
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4).astype(np.float64)


def set_pixels(img, px):
    img.pixels.foreach_set(np.clip(px, 0, 1).astype(np.float32).ravel())


def box(a, r):
    """箱型にぼかす（累積和で速く）"""
    c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r), (0, 0)), mode="edge"), axis=0), axis=1)
    k = 2 * r + 1
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def grade(rgb, target, local):
    """模様は残して、色味を target に寄せ、彩度を上げる。local は模様をならした色（その場所の平均の色）"""
    ratio = np.clip(target / np.maximum(local, 1e-3), 0.2, 5)
    out = rgb * (1 + COLOR_MATCH * (ratio - 1))
    lum = out.mean(axis=2, keepdims=True)
    return lum + (out - lum) * SATURATION


def setup_bake(cage, max_dist):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = lib["BAKE_SAMPLES"]
    b = sc.render.bake
    b.use_selected_to_active = True
    b.cage_extrusion = cage
    b.max_ray_distance = max_dist
    b.margin = 8


def bake_view(high, lmin, lmax, zlo, zhi, from_top, size):
    """スキャンを真上（from_top）か真下から見た色の画像を作る。モデルの外枠（lmin〜lmax）の範囲を、画像いっぱいに"""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=32, y_segments=32, size=0.5)
    w, d = lmax[0] - lmin[0], lmax[1] - lmin[1]
    cx, cy = (lmin[0] + lmax[0]) / 2, (lmin[1] + lmax[1]) / 2
    bmesh.ops.transform(bm, verts=bm.verts, matrix=Matrix.Translation((cx, cy, zlo if from_top else zhi)) @ Matrix.Diagonal((w, d, 1, 1)))
    if not from_top:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)  # 下から見るときは、面を下向きにする
    uv = bm.loops.layers.uv.new("UVMap")
    for f in bm.faces:
        for lp in f.loops:
            lp[uv].uv = ((lp.vert.co.x - lmin[0]) / w, (lp.vert.co.y - lmin[1]) / d)
    # 面の反対側（上から見るなら、ずっと上）から、面に向かって光線を飛ばし、最初に当たったスキャンの色を取る
    H = zhi - zlo
    return bake_plane(bm, high, size, cage=H, max_dist=H * 2)


def bake_side(high, center, radius, zlo, zhi, size):
    """スキャンを横から（まわりを囲む筒から、内側へ）見た色の画像を作る。横は角度（0〜360°）、縦は高さ"""
    seg, rows = 96, 16
    bm = bmesh.new()
    grid = [[bm.verts.new((center[0] + radius * math.cos(2 * math.pi * j / seg), center[1] + radius * math.sin(2 * math.pi * j / seg),
                            zlo + (zhi - zlo) * i / rows)) for j in range(seg + 1)] for i in range(rows + 1)]
    uv = bm.loops.layers.uv.new("UVMap")
    for i in range(rows):
        for j in range(seg):
            f = bm.faces.new((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]))  # 外向きの面
            for lp, (a, b) in zip(f.loops, ((j, i), (j + 1, i), (j + 1, i + 1), (j, i + 1))):
                lp[uv].uv = (a / seg, b / rows)
    # 面は外向き（角度の向き × 上向き）。光線は面から内側へ飛ぶ
    return bake_plane(bm, high, size, cage=0.01, max_dist=radius * 1.5)


def bake_plane(bm, high, size, cage, max_dist):
    """bm の面（展開図つき）に、スキャンの色を焼き込んだ画像を返す。光線は面から、面の裏側の向きへ飛ぶ"""
    me = bpy.data.meshes.new("view")
    bm.to_mesh(me)
    bm.free()
    plane = bpy.data.objects.new("view", me)
    bpy.context.scene.collection.objects.link(plane)
    img = bpy.data.images.new("view", size, size)
    mat = bpy.data.materials.new("view")
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    mat.node_tree.nodes.active = node
    me.materials.append(mat)
    setup_bake(cage, max_dist)
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    plane.select_set(True)
    bpy.context.view_layer.objects.active = plane
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_clear=True)
    lib["fill_empty"](img)
    px = pixels(img)[:, :, :3]
    bpy.data.objects.remove(plane)
    bpy.data.images.remove(img)
    return px


def planar(high, low, app_name, face_mat, face_col, top_only):
    """上と下から投影して色を貼る。テクスチャは、左半分が上から見た色、右半分が下から見た色"""
    lmin, lmax = bbox(low)
    hmin, hmax = bbox(high)
    zlo, zhi = min(lmin[2], hmin[2]) - 0.5, max(lmax[2], hmax[2]) + 0.5
    me = low.data
    nz = np.array([p.normal.z for p in me.polygons])
    # 面の向きで、どちらから当てるかを決める: 上を向いた面は上から、下は下から、横（立った面）は横から
    side = np.abs(nz) < SIDE_LIMIT
    up = ~side & (nz >= 0)
    down = ~side & (nz < 0)
    area = np.array([p.area for p in me.polygons])
    plain = np.array([n.startswith(KEEP_PLAIN) for n in face_mat])
    center = ((lmin + lmax) / 2)[:2]
    co = np.array([v.co[:] for v in me.vertices])
    radius = max(np.hypot(co[:, 0] - center[0], co[:, 1] - center[1]).max(), 0.1) + 0.3

    def target_of(sel):
        sel = sel & ~plain
        return (face_col[sel] * area[sel, None]).sum(axis=0) / max(area[sel].sum(), 1e-9) if sel.any() else None

    views = []
    for kind in ("top", "bottom", "side"):
        if kind == "side":
            rgb = bake_side(high, center, radius, zlo, zhi, TEX)
        else:
            rgb = bake_view(high, lmin, lmax, zlo, zhi, kind == "top", TEX)
        # 色をモデル版に寄せる（その向きの面の、モデル版の色の平均へ）
        target = target_of({"top": up, "bottom": down, "side": side}[kind])
        if target is None:
            target = rgb.reshape(-1, 3).mean(axis=0)
        views.append(grade(rgb, target, box(rgb, 24)))
    atlas = bpy.data.images.new(f"{app_name}-color", TEX * 3, TEX)
    px = np.ones((TEX, TEX * 3, 4))
    for k, v in enumerate(views):
        px[:, TEX * k:TEX * (k + 1), :3] = v
    set_pixels(atlas, px)
    if top_only:
        lib["remove_highlights"](atlas)

    # 形に、3方向から当てる展開図を作る（テクスチャは、左から 上・下・横 の3枚）
    uv = me.uv_layers.new(name="UVMap")
    w, d = lmax[0] - lmin[0], lmax[1] - lmin[1]
    side_z = co[np.unique(np.concatenate([list(me.polygons[i].vertices) for i in np.nonzero(side)[0]]))][:, 2] if side.any() else np.zeros(1)
    zs0, zs1 = side_z.min(), max(side_z.max(), side_z.min() + 1e-6)
    for p in me.polygons:
        if side[p.index] and app_name in SIDE_FROM_TOP:
            # 縁の下ほど外周寄り（0.92）、上ほど内側寄り（0.67）の、上から見た色を当てる
            for li in p.loop_indices:
                c = me.vertices[me.loops[li].vertex_index].co
                a = math.atan2(c.y - center[1], c.x - center[0])
                f = 0.92 - 0.25 * min(max((c.z - zs0) / (zs1 - zs0), 0), 1)
                x = center[0] + f * w / 2 * math.cos(a)
                y = center[1] + f * d / 2 * math.sin(a)
                uv.data[li].uv = (((x - lmin[0]) / w) / 3, (y - lmin[1]) / d)
        elif side[p.index]:
            angs = []
            for li in p.loop_indices:
                c = me.vertices[me.loops[li].vertex_index].co
                angs.append(math.atan2(c.y - center[1], c.x - center[0]) % (2 * math.pi))
            if max(angs) - min(angs) > math.pi:  # 0°をまたぐ面は、つながるように 360° 足す
                angs = [a + 2 * math.pi if a < math.pi else a for a in angs]
            us = [a / (2 * math.pi) for a in angs]
            for li, u in zip(p.loop_indices, us):
                c = me.vertices[me.loops[li].vertex_index].co
                uv.data[li].uv = ((2 + min(max(u, 0), 1)) / 3, (c.z - zlo) / (zhi - zlo))
        else:
            off = 0 if up[p.index] else 1
            for li in p.loop_indices:
                c = me.vertices[me.loops[li].vertex_index].co
                uv.data[li].uv = ((off + (c.x - lmin[0]) / w) / 3, (c.y - lmin[1]) / d)
    mat = bpy.data.materials.new(app_name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.7
    cn = nt.nodes.new("ShaderNodeTexImage")
    cn.image = atlas
    nt.links.new(cn.outputs["Color"], bsdf.inputs["Base Color"])
    me.materials.append(mat)
    atlas.pack()
    bpy.data.objects.remove(high)


def match_colors(low, ref, app_name):
    """表面に沿って写したテクスチャの色を、場所ごとにモデル版の色（ref のマテリアルの色）へ寄せる"""
    nt = low.data.materials[0].node_tree
    color_node = next(n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image and n.image.name.endswith("-color"))
    img = color_node.image
    w, h = img.size
    target = bpy.data.images.new(f"{app_name}-target", w, h)
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = target
    nt.nodes.active = tn
    setup_bake(0.05, 0.1)  # 同じ形どうしなので、すぐ近くを探す
    bpy.ops.object.select_all(action="DESELECT")
    ref.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_clear=True)
    lib["fill_empty"](target)
    rgb = pixels(img)[:, :, :3]
    tgt = box(pixels(target)[:, :, :3], 2)
    px = pixels(img)
    px[:, :, :3] = grade(rgb, tgt, box(rgb, 16))
    set_pixels(img, px)
    img.pack()
    nt.nodes.remove(tn)
    bpy.data.images.remove(target)
    bpy.data.objects.remove(ref)


def keep_plain(low, face_mat, face_col):
    """ごまなどは、テクスチャを貼らずにモデル版の色のマテリアルにする"""
    names = sorted({n for n in face_mat if n.startswith(KEEP_PLAIN)})
    for name in names:
        col = face_col[face_mat.index(name)] ** 2.2
        m = bpy.data.materials.new(f"{low.name}-{name}")
        m.use_nodes = True
        m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*col, 1)
        m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.5
        low.data.materials.append(m)
        idx = len(low.data.materials) - 1
        for p in low.data.polygons:
            if face_mat[p.index] == name:
                p.material_index = idx


def build(app_name, scan_name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    high = lib["load"](scan_name)
    high.name = "high"
    lib["bake_source_materials"](high)
    # モデル版の形（表面に沿って写す具材は、色を寄せる目標にするため、元のマテリアルのままの複製 ref も読む）
    low, face_mat, face_col = load_model(app_name)
    if app_name in ALIGN_SQUARE:
        print(f"## {app_name}: スキャンを {align_square(high)}° 回して、四角の向きをそろえた")
    fit(high, low)
    top_only = (lib["SHAPES"].get(app_name) or {}).get("kind") in ("slab", "puddle")
    if top_only:
        lib["keep_top_only"](high)
    if app_name in PLANAR:
        planar(high, low, app_name, face_mat, face_col, top_only)
    else:
        ref = load_model_ref(app_name)
        lib["bake_onto"](high, low, app_name, TEX, CAGE, top_only, smooth=app_name not in FLAT)
        match_colors(low, ref, app_name)
    keep_plain(low, face_mat, face_col)
    return low


def load_model_ref(app_name):
    """モデル版の具材を、元のマテリアルのまま読む（色を寄せる目標）"""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(model_dir, f"{app_name}.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        o.data = o.data.copy()
        o.data.transform(o.matrix_world)
        o.parent = None
        o.matrix_world = Matrix()
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ref = bpy.context.view_layer.objects.active
    ref.name = "ref"
    remove_parts(ref)
    return ref


def main():
    os.makedirs(out_dir, exist_ok=True)
    src_manifest = json.load(open(os.path.join(model_dir, "models.json")))
    manifest_path = os.path.join(out_dir, "models.json")
    manifest = json.load(open(manifest_path)) if only and os.path.exists(manifest_path) else {}
    for app_name, scan_name in lib["USE"].items():
        if only and app_name not in only:
            continue
        ob = build(app_name, scan_name)
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        path = os.path.join(out_dir, f"{app_name}.glb")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_yup=True,
                                  export_image_format="JPEG", export_jpeg_quality=90)
        manifest[app_name] = {"file": f"{app_name}.glb", "stackHeight": src_manifest[app_name]["stackHeight"],
                              "shape": "models", "scan": scan_name}
        how = "上下から投影" if app_name in PLANAR else "表面に沿って"
        print(f"## {app_name} ← 形: models / 色: {scan_name}（{how}）: {len(ob.data.polygons)}面 "
              f"{os.path.getsize(path) / 1024:.0f}KB")
    shutil.copy(os.path.join(model_dir, "mannequin.glb"), os.path.join(out_dir, "mannequin.glb"))
    manifest["mannequin"] = {"file": "mannequin.glb", "stackHeight": 160.0}
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


main()

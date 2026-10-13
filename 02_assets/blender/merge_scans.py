# 切り抜いた具材（02_assets/models-trimmed/*.glb）を、上面と下面の2つを組み合わせて、上からも下からも見られる1つのモデルにする。
# 結果は 02_assets/models-merged/ のGLBと、blender/scans-merged.blend に書き出す。先に trim_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/merge_scans.py
#   （1つだけ: ... -P merge_scans.py -- patty-01）
#
# 組み合わせ方
# 1. 下面にするスキャンを裏返す（スキャンは、どれも置いた状態で上から撮っているので）
#    - pair（表と裏のスキャン）: 高さの真ん中で切り、上面は上半分、下面は下半分だけを使う（紙に接していた側は写っていない）
#    - fit（別々の2つ）・self（1つを裏返して使う）: 紙の面で背中合わせにする。下面は実物にない形なので、高さを BOTTOM_Z 倍につぶす
#    - self は、回転ではなく鏡に映すように裏返す。輪郭がぴったり同じになる
# 2. 縁を整える: 縁から飛び出したトゲ状の面を取り、縁の線をなめらかにする（ギザギザだとおいしそうに見えない）
# 3. 継ぎ目の輪郭の形から、下面の z 軸まわりの角度を計算する
#    輪郭を「角度ごとの、中心からの距離」の波形にして、2つの波形が一番重なる回転を探す（fit は大きさも合わせる）
# 4. 上面と下面を、2つの輪郭の中間の形に寄せる。継ぎ目に近いほど強く、遠い所（上面のてっぺん・下面の底）は変えない
# 5. 寄せた2本の輪郭を、細い帯の面で縫い合わせる。残った小さな穴はふさぐ（空間に穴が開かない、閉じた形にする）
#    継ぎ目は、形をならし（pair）、帯の色を上下のテクスチャのグラデーションにし、上下の明るさの違いを継ぎ目の近くでそろえる
# 6. 底を高さ0、輪郭の中心を原点にそろえる（単位はm。Scaniverseのまま）

import bpy
import bmesh
import math
import os
import sys

import numpy as np
from mathutils import Matrix, Vector

here = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.normpath(os.path.join(here, "..", "models-trimmed"))
out_dir = os.path.normpath(os.path.join(here, "..", "models-merged"))
os.makedirs(out_dir, exist_ok=True)

# (名前, 上面, 下面, 組み合わせ方)
PAIRS = [
    # 下バンズは、ura（裏）の面を上にする（ユーザーの指定。2026-10-13）
    ("bans-shita-01", "bans-shita-ura-01", "bans-shita-omote-01", "pair"),
    ("bans-shita-02", "bans-shita-ura-02", "bans-shita-omote-02", "pair"),
    ("bans-ue-01", "bans-ue-omote-01", "bans-ue-ura-01", "pair"),
    ("bans-ue-02", "bans-ue-omote-02", "bans-ue-ura-02", "pair"),
    ("patty-01", "patty-omote-01", "patty-ura-01", "pair"),
    ("patty-cheese-01", "patty-cheese-omote-01", "patty-cheese-ura-01", "pair"),
    ("patty-cheese-02", "patty-cheese-omote-02", "patty-cheese-ura-02", "pair"),
    # 別々の2つを、上面と下面にする
    ("cheese-square", "cheese-02-a", "cheese-02-b", "fit"),
    ("ketchup", "ketchup-01", "ketchup-02", "fit"),
    ("relish", "relish-01", "relish-02", "fit"),
    ("sauce", "sauce-01", "sauce-02", "fit"),
    # 溶けたチーズと玉ねぎは、1つのスキャンを鏡のように裏返して下面にする
    ("cheese-melted", "cheese-01-b", "cheese-01-b", "self"),
    ("onion", "onion-01", "onion-01", "self"),
]

BOTTOM_Z = 0.4  # fit・self の下面の高さを、この倍率につぶす
SPIKE_PASSES = 4  # 縁のトゲ（縁の辺を2本以上持つ面）を取り除く回数
SMOOTH_STEPS = 30  # 縁の線をなめらかにする回数（多いほど、なめらか）
ISLAND_MIN = 0.02  # 全体のこの割合より小さい、離れた破片は捨てる
BINS = 360  # 輪郭の波形の細かさ（1°ごと）
MORPH_LIMIT = (0.8, 1.25)  # 輪郭を寄せるときの、伸び縮みの倍率の範囲
SEAM_SMOOTH = 0.003  # 表と裏のスキャン（pair）で、継ぎ目の上下この範囲（m）の形をならす
SEAM_SMOOTH_STEPS = 8  # ならす回数
SEAM_BLEND = 0.008  # 継ぎ目の上下この範囲（m）で、上下の色を少しずつそろえる
SEAM_TINT = (0.7, 2.0)  # 色をそろえるときの倍率の範囲（1より大きいと明るくする）
# glTFの頂点カラーは0〜1に切り捨てられるので、明るくする倍率（1より大きい）が入らない。
# そこで頂点カラーには「色 ÷ COLOR_SCALE」を入れ、アプリで読み込むときにマテリアルの色を COLOR_SCALE 倍して戻す
# （models.json の colorScale。Blenderのプレビューでは、継ぎ目の付近が半分の明るさに見える）
COLOR_SCALE = 2.0
SEAM_PROFILE = 0.002  # 継ぎ目の色を調べる範囲（m）

only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


# ---------- 読み込み ----------

def load(name, tag):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(src_dir, f"{name}.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    ob = next(o for o in new if o.type == "MESH")
    for o in new:
        if o is not ob:
            bpy.data.objects.remove(o)
    ob.parent = None
    ob.data = ob.data.copy()  # 同じスキャンを2回読む（溶けたチーズ）ときに、メッシュを共有しない
    ob.data.transform(ob.matrix_world)
    ob.matrix_world = Matrix()
    ob.name = tag
    return ob


def zrange(ob):
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    z = co.reshape(-1, 3)[:, 2]
    return z.min(), z.max()


def cut(ob, z, keep_above):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, z),
                           plane_no=(0, 0, 1), clear_inner=keep_above, clear_outer=not keep_above)
    bm.to_mesh(ob.data)
    bm.free()


# ---------- 縁 ----------

def boundary_loops(bm):
    """縁（面が片側にしかない辺）をたどって、輪の頂点の並びを返す"""
    left = {e for e in bm.edges if e.is_boundary}
    loops = []
    while left:
        e = left.pop()
        start, cur = e.verts
        loop = [start, cur]
        while True:
            nxt = [x for x in cur.link_edges if x in left]
            if not nxt:
                break
            left.discard(nxt[0])
            cur = nxt[0].other_vert(cur)
            if cur is start:
                break
            loop.append(cur)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def perimeter(loop):
    p = np.array([v.co[:] for v in loop])
    return np.linalg.norm(p - np.roll(p, 1, axis=0), axis=1).sum()


def tidy(ob):
    """縁を整える: 継ぎ目の頂点をつなぎ、離れた破片とトゲを取り、縁の線をなめらかにする"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)  # GLBはテクスチャの継ぎ目で頂点が分かれている

    # 離れた小さな破片
    bm.faces.ensure_lookup_table()
    seen, islands = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, isl = [f], []
        seen.add(f)
        while stack:
            g = stack.pop()
            isl.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        islands.append(isl)
    total = len(bm.faces)
    small = [f for isl in islands if len(isl) < total * ISLAND_MIN for f in isl]
    bmesh.ops.delete(bm, geom=small, context="FACES")

    # トゲ: 縁の辺を2本以上持つ面（縁から飛び出した三角形）
    for _ in range(SPIKE_PASSES):
        spikes = [f for f in bm.faces if sum(e.is_boundary for e in f.edges) >= 2]
        if not spikes:
            break
        bmesh.ops.delete(bm, geom=spikes, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")

    # 縁の線をなめらかにする（Taubin法: なめらかにする→少し戻す、をくり返す。ただ平均すると輪が縮む）
    for loop in boundary_loops(bm):
        p = np.array([v.co[:] for v in loop])
        for _ in range(SMOOTH_STEPS):
            for lam in (0.5, -0.53):
                p = p + lam * ((np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0)) / 2 - p)
        for v, q in zip(loop, p):
            v.co = q
    bm.to_mesh(ob.data)
    bm.free()


def seam(ob):
    """継ぎ目の輪郭（いちばん長い縁）の点の並び"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    loops = boundary_loops(bm)
    loop = max(loops, key=perimeter)
    p = np.array([v.co[:] for v in loop])
    bm.free()
    return p


# ---------- 輪郭の波形 ----------

def profile(loop, center):
    """輪郭を「角度（1°ごと）→ 中心からの距離」の波形にする"""
    dense = []
    for a, b in zip(loop, np.roll(loop, -1, axis=0)):
        for t in np.linspace(0, 1, 6, endpoint=False):
            dense.append(a + (b - a) * t)
    d = np.array(dense)[:, :2] - center
    ang = (np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 360).astype(int) % BINS
    r = np.full(BINS, np.nan)
    np.fmax.at(r, ang, np.hypot(d[:, 0], d[:, 1]))
    ok = ~np.isnan(r)
    idx = np.arange(BINS)
    r = np.interp(idx, idx[ok], r[ok], period=BINS)
    k = np.ones(5) / 5  # 少しならす
    return np.convolve(np.r_[r[-2:], r, r[:2]], k, mode="valid")


def morph(ob, center, r_from, r_to, seam_z):
    """継ぎ目の輪郭が r_from から r_to になるように、横に伸び縮みさせる。継ぎ目から離れるほど弱める"""
    me = ob.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    d = np.abs(co[:, 2] - seam_z)
    t = np.clip(1 - d / max(d.max(), 1e-6), 0, 1)
    w = t * t * (3 - 2 * t)
    rel = co[:, :2] - center
    ang = np.degrees(np.arctan2(rel[:, 1], rel[:, 0])) % 360
    # 輪郭が深くくびれている角度では倍率が極端になり、面が外へ引き伸ばされるので、範囲を押さえる
    k = np.interp(ang, np.arange(BINS), np.clip(r_to / r_from, *MORPH_LIMIT), period=BINS)
    co[:, :2] = center + rel * (1 + (k - 1) * w)[:, None]
    me.vertices.foreach_set("co", co.ravel())
    me.update()


# ---------- 縫い合わせ ----------

def stitch(ob, cap_mat_index):
    """上半分と下半分の継ぎ目（それぞれいちばん長い縁）を、細い帯の面で縫い合わせ、残った穴をふさぐ"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    half = bm.verts.layers.int.get("half")
    loops = boundary_loops(bm)
    ends = []
    for h in (0, 1):
        mine = [lp for lp in loops if sum(v[half] == h for v in lp) > len(lp) / 2]
        ends.append(max(mine, key=perimeter) if mine else None)
    stitched = 0
    seam_z = [None, None]
    if all(ends):
        A, B = ends
        seam_z = [float(np.mean([v.co.z for v in A])), float(np.mean([v.co.z for v in B]))]

        def ccw(loop):
            p = np.array([v.co[:2] for v in loop])
            area = np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
            return loop if area > 0 else loop[::-1]

        A, B = ccw(A), ccw(B)
        a0 = Vector(A[0].co)
        start = min(range(len(B)), key=lambda j: (B[j].co - a0).length)
        B = B[start:] + B[:start]

        center = np.array([v.co[:2] for v in A]).mean(axis=0)
        a_start = math.atan2(A[0].co.y - center[1], A[0].co.x - center[0])

        def param(loop):
            """輪の各点の、中心から見た角度（0〜1）。輪の長さで対応させると、片方だけ細かく波打っている所でずれるので、角度で対応させる。
            くびれなどで角度が戻る所は、戻らないように押さえる"""
            p = np.array([v.co[:2] for v in loop]) - center
            ang = np.unwrap(np.arctan2(p[:, 1], p[:, 0]))
            ang = ang - a_start
            ang -= 2 * math.pi * np.round(ang[0] / (2 * math.pi))
            t = np.maximum.accumulate(ang) / (2 * math.pi)
            return np.r_[np.clip(t, 0, 1), 1.0]

        ta, tb = param(A), param(B)
        i = j = 0
        na, nb = len(A), len(B)
        while i < na or j < nb:
            adv_a = j >= nb or (i < na and ta[i + 1] <= tb[j + 1])
            if adv_a:
                tri = (A[i % na], A[(i + 1) % na], B[j % nb])
                i += 1
            else:
                tri = (A[i % na], B[(j + 1) % nb], B[j % nb])
                j += 1
            if len(set(tri)) == 3:
                try:
                    f = bm.faces.new(tri)
                    f.material_index = cap_mat_index
                    stitched += 1
                except ValueError:
                    pass

    # 残った穴（スキャンの穴など）をふさぐ
    holes = [e for e in bm.edges if e.is_boundary]
    filled = bmesh.ops.holes_fill(bm, edges=holes, sides=0)["faces"]
    tris = bmesh.ops.triangulate(bm, faces=filled, quad_method="BEAUTY", ngon_method="BEAUTY")["faces"]
    for f in tris:
        f.material_index = cap_mat_index
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    return stitched, len(tris), seam_z


def blend_seam(ob, seam_z, cap_mat_index, smooth):
    """継ぎ目をなじませる
    1. （smooth のとき）継ぎ目の上下 SEAM_SMOOTH の形をならして、折れ目を消す
    2. 継ぎ目の帯と穴のふたの色を、上下それぞれのテクスチャから取った色にする（帯の中がグラデーションになる）
    3. 継ぎ目の近く（上下 SEAM_BLEND）だけ、暗い方の半分を、明るい方の色にそろえるように少しずつ明るくする
       （表と裏のスキャンは、撮ったときの明るさが違う。裏は影になって暗く写りやすく、継ぎ目を境に色がパッと変わる）
    色は頂点カラー（面の角ごとの色）にして、テクスチャに掛け合わせる。three.js でも同じように掛け合わされる"""
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    half = bm.verts.layers.int.get("half")
    uv = bm.loops.layers.uv.active

    if smooth:
        z0 = (seam_z[0] + seam_z[1]) / 2
        band = [v for v in bm.verts if abs(v.co.z - z0) < SEAM_SMOOTH]
        for _ in range(SEAM_SMOOTH_STEPS):
            bmesh.ops.smooth_vert(bm, verts=band, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)

    # マテリアルごとのテクスチャ（小さくして、リニアの色にする）
    imgs = {}
    for mi, m in enumerate(me.materials):
        img = next((n.image for n in (m.node_tree.nodes if m and m.node_tree else [])
                    if n.type == "TEX_IMAGE" and n.image), None)
        if img:
            small = img.copy()
            small.scale(512, 256)
            px = np.empty(512 * 256 * 4, dtype=np.float32)
            small.pixels.foreach_get(px)
            bpy.data.images.remove(small)
            imgs[mi] = px.reshape(256, 512, 4)[:, :, :3] ** 2.2

    # 頂点ごとの、テクスチャの色
    vcol = {}
    for f in bm.faces:
        px = imgs.get(f.material_index)
        if px is None:
            continue
        for lp in f.loops:
            if lp.vert not in vcol:
                u, v = lp[uv].uv
                vcol[lp.vert] = px[min(int(v % 1 * 256), 255), min(int(u % 1 * 512), 511)]

    co = np.array([v.co[:] for v in bm.verts])
    center = co[:, :2].mean(axis=0)
    nb = 36  # 10°ごと

    def bin_of(v):
        return int(math.degrees(math.atan2(v.co.y - center[1], v.co.x - center[0])) % 360 // 10) % nb

    # 継ぎ目のすぐそばの色を、角度ごとに集める（半分ごと）
    samples = [[[] for _ in range(nb)] for _ in range(2)]
    for v, c in vcol.items():
        h = v[half]
        if abs(v.co.z - seam_z[h]) < SEAM_PROFILE:
            samples[h][bin_of(v)].append(c)
    allc = np.array(list(vcol.values()))
    overall = np.median(allc, axis=0) if len(allc) else np.array([0.5, 0.4, 0.3])
    prof = np.array([[np.median(b, axis=0) if b else overall for b in samples[h]] for h in range(2)])
    # 角度ごとに、明るい方の半分の色に合わせる（暗い方は、スキャンのときに影になっていた所なので）
    lum = prof @ np.array([0.2126, 0.7152, 0.0722])
    target = np.where((lum[0] >= lum[1])[:, None], prof[0], prof[1])
    tint_max = np.clip(target / np.maximum(prof, 1e-4), *SEAM_TINT)

    col = bm.loops.layers.float_color.new("seam")
    for f in bm.faces:
        plain = f.material_index == cap_mat_index or f.material_index not in imgs
        for lp in f.loops:
            v = lp.vert
            h = v[half]
            t = min(max(1 - abs(v.co.z - seam_z[h]) / SEAM_BLEND, 0.0), 1.0)
            w = t * t * (3 - 2 * t)
            tint = 1 + (tint_max[h][bin_of(v)] - 1) * w
            c = (vcol.get(v, overall) * tint) if plain else tint
            lp[col] = (*np.clip(np.asarray(c) / COLOR_SCALE, 0, 1), 1.0)
    bm.to_mesh(me)
    bm.free()
    me.color_attributes.active_color = me.color_attributes["seam"]
    me.color_attributes.render_color_index = me.color_attributes.active_color_index


# ---------- 組み合わせ ----------

def merge(name, top_name, bot_name, mode):
    top = load(top_name, f"{name}-top")
    bot = load(bot_name, f"{name}-bottom")

    # 1. 裏返して、高さをそろえる
    if mode == "self":
        bot.data.transform(Matrix.Scale(-1, 4, (0, 0, 1)))  # 鏡に映すように裏返す
    else:
        bot.data.transform(Matrix.Rotation(math.pi, 4, "X"))
    if mode == "pair":
        # 裏返した下面の「紙に接していた面」は、実物の一番上。上面のスキャンの一番高い所に合わせる
        h_top = zrange(top)[1]
        bot.data.transform(Matrix.Translation((0, 0, h_top)))
        mid = (max(zrange(bot)[0], 0) + h_top) / 2
        cut(top, mid, keep_above=True)
        cut(bot, mid, keep_above=False)
    else:
        bot.data.transform(Matrix.Diagonal((1, 1, BOTTOM_Z, 1)))

    # 2. 縁を整える
    tidy(top)
    tidy(bot)

    # 3. 継ぎ目の輪郭の形から、角度（と大きさ）を求めて、下面を合わせる
    lt, lb = seam(top), seam(bot)
    ct, cb = lt[:, :2].mean(axis=0), lb[:, :2].mean(axis=0)
    rt, rb = profile(lt, ct), profile(lb, cb)
    s = rt.mean() / rb.mean() if mode == "fit" else 1.0
    if mode == "self":
        k = 0
    else:
        errs = [np.sum((rt - np.roll(rb * s, k)) ** 2) for k in range(BINS)]
        k = int(np.argmin(errs))
    theta = math.radians(k * 360 / BINS)
    bot.data.transform(Matrix.Translation((ct[0], ct[1], 0)) @ Matrix.Rotation(theta, 4, "Z")
                       @ Matrix.Diagonal((s, s, 1, 1)) @ Matrix.Translation((-cb[0], -cb[1], 0)))

    # 4. 2つの輪郭の中間の形に寄せる
    lb = seam(bot)
    rb2 = profile(lb, ct)
    r_mid = (rt + rb2) / 2
    gap_before = np.abs(rt - rb2).mean() * 1000
    morph(top, ct, rt, r_mid, lt[:, 2].mean())
    morph(bot, ct, rb2, r_mid, lb[:, 2].mean())

    # 5. 1つにまとめて、継ぎ目を縫い合わせる
    for ob, h in ((top, 0), (bot, 1)):
        attr = ob.data.attributes.new("half", "INT", "POINT")
        attr.data.foreach_set("value", [h] * len(ob.data.vertices))
    mat = bpy.data.materials.new(f"{name}-seam")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.8
    attr_node = mat.node_tree.nodes.new("ShaderNodeVertexColor")
    attr_node.layer_name = "seam"
    mat.node_tree.links.new(attr_node.outputs["Color"], bsdf.inputs["Base Color"])
    bpy.ops.object.select_all(action="DESELECT")
    top.select_set(True)
    bot.select_set(True)
    bpy.context.view_layer.objects.active = top
    bpy.ops.object.join()
    ob = top
    ob.name = ob.data.name = name
    ob.data.materials.append(mat)
    stitched, filled, seam_z = stitch(ob, len(ob.data.materials) - 1)
    if None not in seam_z:
        blend_seam(ob, seam_z, len(ob.data.materials) - 1, smooth=(mode == "pair"))
    ob.data.attributes.remove(ob.data.attributes["half"])

    # 6. 底を0、中心を原点に
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mn, mx = co.min(axis=0), co.max(axis=0)
    ob.data.transform(Matrix.Translation((-(mn[0] + mx[0]) / 2, -(mn[1] + mx[1]) / 2, -mn[2])))
    ob.data.update()
    size = (mx - mn) * 100
    print(f"## {name}: 回転{math.degrees(theta):.0f}° 大きさ×{s:.2f} 継ぎ目のずれ{gap_before:.1f}mm→寄せて縫う"
          f"（帯{stitched}面・穴ふさぎ{filled}面）  幅{size[0]:.1f}×奥行{size[1]:.1f}×高さ{size[2]:.1f}cm")
    return ob


bpy.ops.wm.read_factory_settings(use_empty=True)
pairs = [p for p in PAIRS if not only or p[0] in only]
for i, pair in enumerate(pairs):
    ob = merge(*pair)
    col = bpy.data.collections.new(ob.name)
    bpy.context.scene.collection.children.link(col)
    for c in ob.users_collection:
        c.objects.unlink(ob)
    col.objects.link(ob)

    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, f"{ob.name}.glb"), export_format="GLB",
                              use_selection=True, export_yup=True, export_vertex_color="ACTIVE")
    ob.location = ((i % 5) * 0.2, -(i // 5) * 0.2, 0)  # .blend の中では格子状に並べる

if not only:
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(here, "scans-merged.blend"))

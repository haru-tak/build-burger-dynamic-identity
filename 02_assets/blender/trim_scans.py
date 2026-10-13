# Scaniverseのスキャン（02_assets/models-scanned/*.glb）から、具材の部分だけを切り抜く。
# 元のファイルは変えない。結果は 02_assets/models-trimmed/ のGLBと、blender/scans-trimmed.blend に書き出す。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/trim_scans.py
#   （1ファイルだけ: ... -P trim_scans.py -- patty-omote-01）
#
# 見分け方（スキャンは「木の机の上の白い紙に、具材を置いた」状態）
# 1. 白い紙の部分（色が薄く、彩度が低い）に平面を当てはめて、紙の高さを求める
# 2. 面ごとに「紙より高い」か「紙の色ではない」なら、具材の候補にする
# 3. 机から紙を切り出す（紙の色で平らな面の、一番大きいかたまり。その外形の長方形を求める）
# 4. 紙の外形の内側にある候補を具材とする（机の切れ端は紙の外なので入らない）
# 5. 具材の中の小さな穴（ソースのテカリが白く写った所など）は埋め、ふちに残った紙ははがす
# 6. 紙の面が水平になるように回し、具材の底の中心を原点に置く（単位はm。Scaniverseのまま）

import bpy
import bmesh
import glob
import math
import os
import sys
from collections import deque

import numpy as np
from mathutils import Matrix, Vector

here = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.normpath(os.path.join(here, "..", "models-scanned"))
out_dir = os.path.normpath(os.path.join(here, "..", "models-trimmed"))
os.makedirs(out_dir, exist_ok=True)

HEIGHT_MIN = 0.0025  # 紙から2.5mm以上高ければ具材
PAPER_DC = 0.04  # 紙の色との差がこれ未満なら紙（平面を当てはめるのに使う）
COLOR_DC = 0.08  # 紙の色との差がこれ以上なら具材の候補
DARK = 0.35  # 紙に対する明るさがこれ未満なら具材の候補（焼けたパティなど）
KEEP_RATIO = 0.5  # 一番大きいかたまりの、この割合以上の面積があるかたまりは残す（チーズ2枚など）
HOLE_MAX_RATIO = 0.3  # 具材の面積に対して、これより小さい穴は埋める
MIN_TOP = 0.003  # かたまりの上の方（95%）が、紙からこれ以上（m）高くなければ具材ではない
PAPER_MARGIN = 0.008  # 紙のふちから、これより内側（m）だけを見る
PEEL_STEPS = 6  # ふちの紙をはがす回数
PEEL_HEIGHT = 0.006  # これより低い紙色の面だけをはがす（具材の側面を削らない）

only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def texture_of(ob):
    for m in ob.data.materials:
        if m and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image:
                    return n.image


def sample_colors(ob):
    """面ごとに「紙の色との差」と「紙に対する明るさ」を、テクスチャから求める"""
    img = texture_of(ob)
    small = img.copy()
    small.scale(2048, 1024)
    W, H = small.size
    px = np.empty(W * H * 4, dtype=np.float32)
    small.pixels.foreach_get(px)
    px = px.reshape(H, W, 4)
    bpy.data.images.remove(small)
    me = ob.data
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    x = np.clip((uv[:, 0] % 1) * W, 0, W - 1).astype(int)
    y = np.clip((uv[:, 1] % 1) * H, 0, H - 1).astype(int)
    loop_rgb = px[y, x, :3]
    starts = np.array([p.loop_start for p in me.polygons])
    totals = np.array([p.loop_total for p in me.polygons])
    rgb = np.add.reduceat(loop_rgb, starts) / totals[:, None]
    # 色味（明るさで割った色）で比べると、紙の上の影に左右されにくい
    bright = rgb.sum(axis=1)
    chroma = rgb / np.maximum(bright[:, None], 1e-4)
    # 紙は、スキャンの中で一番色味の少ない（白〜灰色に近い）広い部分。色味の少ない方の3割の中央値を、紙の色とする
    # （明るさで決めると、照明によっては机の方が明るく写って取り違える）
    tint = np.linalg.norm(chroma - 1 / 3, axis=1)
    top = tint <= np.percentile(tint, 30)
    paper_chroma = np.median(chroma[top], axis=0)
    paper_bright = np.median(bright[top])
    return np.linalg.norm(chroma - paper_chroma, axis=1), bright / paper_bright


def fit_plane(pts, tol=0.002, tries=300):
    """z = ax + by + c を当てはめる。RANSAC（ランダムに3点を選び、一番多くの点が乗る平面を探す）で外れ値に強くする"""
    rnd = np.random.default_rng(0)
    if len(pts) > 20000:
        pts = pts[rnd.choice(len(pts), 20000, replace=False)]
    best = None
    for _ in range(tries):
        p0, p1, p2 = pts[rnd.choice(len(pts), 3, replace=False)]
        nrm = np.cross(p1 - p0, p2 - p0)
        if abs(nrm[2]) < 1e-9 or abs(nrm[2]) / np.linalg.norm(nrm) < 0.9:  # 25°以上傾いた平面は紙ではない
            continue
        a, b = -nrm[0] / nrm[2], -nrm[1] / nrm[2]
        c = p0[2] - a * p0[0] - b * p0[1]
        inl = np.abs(pts[:, 2] - (a * pts[:, 0] + b * pts[:, 1] + c)) < tol
        if best is None or inl.sum() > best.sum():
            best = inl
    A = np.c_[pts[best, 0], pts[best, 1], np.ones(best.sum())]
    coef, *_ = np.linalg.lstsq(A, pts[best, 2], rcond=None)
    return coef


def erode(faces, adj, n):
    """ふちの面を n 周り削る"""
    faces = set(faces)
    for _ in range(n):
        faces = {f for f in faces if all(b in faces for b in adj[f])}
    return faces


def convex_hull(pts):
    """2次元の点の凸包（反時計回り）"""
    pts = sorted(map(tuple, np.round(pts, 5)))

    def half(ps):
        h = []
        for p in ps:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h

    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def inside_convex(pts, hull, margin):
    """凸包の各辺から margin 以上内側にある点か"""
    ok = np.ones(len(pts), bool)
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        e = b - a
        n = np.array([-e[1], e[0]]) / np.linalg.norm(e)  # 反時計回りなので左が内側
        ok &= (pts - a) @ n >= margin
    return ok


def components(faces, adj):
    seen, out = set(), []
    for f in faces:
        if f in seen:
            continue
        comp, q = [], deque([f])
        seen.add(f)
        while q:
            a = q.popleft()
            comp.append(a)
            for b in adj[a]:
                if b in faces and b not in seen:
                    seen.add(b)
                    q.append(b)
        out.append(comp)
    return out


def trim(path):
    name = os.path.splitext(os.path.basename(path))[0]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    ob = next(o for o in bpy.data.objects if o not in before and o.type == "MESH")
    for o in set(bpy.data.objects) - before - {ob}:
        bpy.data.objects.remove(o)
    ob.parent = None
    ob.data.transform(ob.matrix_world)
    ob.matrix_world = Matrix()
    ob.name = ob.data.name = name

    me = ob.data
    # GLBはテクスチャの継ぎ目で頂点が分かれているので、つなぎ直す（UVは面の角ごとに持つので崩れない）
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bm.to_mesh(me)
    bm.free()
    dc, rb = sample_colors(ob)
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)

    # 1. 紙の平面
    paper = (dc < PAPER_DC) & (rb > 0.6)
    paper_verts = np.unique(np.concatenate([list(me.polygons[i].vertices) for i in np.nonzero(paper)[0]]))
    a, b, c = fit_plane(co[paper_verts])
    height = co[:, 2] - (co[:, 0] * a + co[:, 1] * b + c)

    # 2. 具材の候補
    nf = len(me.polygons)
    face_h = np.array([height[list(p.vertices)].mean() for p in me.polygons])
    cand = (face_h > HEIGHT_MIN) | (dc > COLOR_DC) | (rb < DARK)

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    adj = [[] for _ in range(nf)]
    edge_faces = set()
    for e in bm.edges:
        lf = [f.index for f in e.link_faces]
        if len(lf) == 1:
            edge_faces.add(lf[0])
        for i in lf:
            adj[i].extend(j for j in lf if j != i)
    area = np.array([f.calc_area() for f in bm.faces])

    # 3. 机から紙を切り出す: 紙の色で低い面の、一番大きいかたまりを紙とする
    #    （ふちを2周り削ってから数えて、紙と机が細くつながっている所を切る）
    center = np.array([p.center[:2] for p in me.polygons])
    flat_paper = set(np.nonzero(~cand & (dc < PAPER_DC * 1.5))[0].tolist())
    core = erode(flat_paper, adj, 2)
    # スキャンの穴で紙が分かれることがあるので、一番大きいものの2割以上あるかたまりは、どれも紙とする
    pieces = components(core, adj)
    largest = max(area[cp].sum() for cp in pieces)
    sheet = [f for cp in pieces if area[cp].sum() >= largest * 0.2 for f in cp]
    hull = convex_hull(center[sheet])

    # 4. 紙の外形（長方形）の内側にある候補だけを、具材とする
    inside = inside_convex(center, hull, PAPER_MARGIN)
    comps = components(set(np.nonzero(cand & inside)[0].tolist()), adj)
    comps.sort(key=lambda cp: -area[cp].sum())
    if not comps:
        raise RuntimeError(f"{name}: 具材が見つからない")
    biggest = area[comps[0]].sum()
    comps = [cp for cp in comps if area[cp].sum() >= biggest * KEEP_RATIO]
    keep = set().union(*map(set, comps))

    # 5. 穴埋め: 残す面に囲まれた（端に触れない）小さなかたまりを足す
    rest = set(range(nf)) - keep
    for cp in components(rest, adj):
        cps = set(cp)
        surrounded = all(b in keep or b in cps for f in cp for b in adj[f])
        if surrounded and not edge_faces.intersection(cp) and area[cp].sum() < biggest * HOLE_MAX_RATIO:
            keep.update(cp)

    # 5. ふちに残った紙をはがす: 外側の面のうち、紙の色で低いものを、数回取り除く
    paperish = (dc < PAPER_DC * 1.5) & (rb > 0.7) & (face_h < PEEL_HEIGHT)
    for _ in range(PEEL_STEPS):
        rim = [f for f in keep if paperish[f] and (f in edge_faces or any(b not in keep for b in adj[f]))]
        if not rim:
            break
        keep.difference_update(rim)
    # はがしたあとに離れて残った小さな破片を消す。大きいかたまりが複数（チーズ2枚など）なら、別々に分ける
    # 紙の影など、ほとんど高さのない平らなかたまりは具材ではない（ソースでも5mmほどある）
    parts = [cp for cp in components(keep, adj) if np.percentile(face_h[cp], 95) >= MIN_TOP]
    parts.sort(key=lambda cp: -area[cp].sum())
    parts = [set(cp) for cp in parts if area[cp].sum() >= area[parts[0]].sum() * KEEP_RATIO]
    parts.sort(key=lambda cp: center[list(cp)].mean(axis=0)[0])  # 左から a, b, …
    bm.free()

    # 6. 紙を水平にして、底の中心を原点に（かたまりごとに別のオブジェクトにする）
    n = Vector((-a, -b, 1)).normalized()
    level = n.rotation_difference(Vector((0, 0, 1))).to_matrix().to_4x4() @ Matrix.Translation((0, 0, -c))
    obs = []
    for k, part in enumerate(parts):
        piece = ob if k == len(parts) - 1 else ob.copy()
        if piece is not ob:
            piece.data = ob.data.copy()
        piece.name = piece.data.name = name if len(parts) == 1 else f"{name}-{'abcdefgh'[k]}"
        pm = piece.data
        bmp = bmesh.new()
        bmp.from_mesh(pm)
        bmp.faces.ensure_lookup_table()
        bmesh.ops.delete(bmp, geom=[f for f in bmp.faces if f.index not in part], context="FACES")
        bmesh.ops.delete(bmp, geom=[v for v in bmp.verts if not v.link_faces], context="VERTS")
        bmp.to_mesh(pm)
        bmp.free()
        pm.transform(level)
        vs = np.empty(len(pm.vertices) * 3)
        pm.vertices.foreach_get("co", vs)
        vs = vs.reshape(-1, 3)
        mn, mx = vs.min(axis=0), vs.max(axis=0)
        pm.transform(Matrix.Translation((-(mn[0] + mx[0]) / 2, -(mn[1] + mx[1]) / 2, 0)))
        pm.update()
        size = (mx - mn) * 100
        print(f"## {piece.name}: {len(pm.polygons)}面 幅{size[0]:.1f}×奥行{size[1]:.1f}×高さ{mx[2] * 100:.1f}cm")
        obs.append(piece)
    return obs


bpy.ops.wm.read_factory_settings(use_empty=True)
files = sorted(glob.glob(os.path.join(src_dir, "*.glb")))
if only:
    files = [f for f in files if os.path.splitext(os.path.basename(f))[0] in only]
i = 0
for f in files:
    for ob in trim(f):
        col = bpy.data.collections.new(ob.name)
        bpy.context.scene.collection.children.link(col)
        for c in ob.users_collection:
            c.objects.unlink(ob)
        col.objects.link(ob)

        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, f"{ob.name}.glb"), export_format="GLB",
                                  use_selection=True, export_yup=True)
        # .blend の中では、格子状に並べる（書き出しは原点のまま済ませてある）
        ob.location = ((i % 6) * 0.25, -(i // 6) * 0.25, 0)
        i += 1

if not only:
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(here, "scans-trimmed.blend"))

# 切り抜いた具材（02_assets/models-trimmed/*.glb）を、上面と下面の2つを組み合わせて、上からも下からも見られる1つのモデルにする。
# 結果は 02_assets/models-merged/ のGLBと、blender/scans-merged.blend に書き出す。先に trim_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/merge_scans.py
#   （1つだけ: ... -P merge_scans.py -- patty-01）
#
# 組み合わせ方
# 1. 下面にするスキャンを、ひっくり返す（スキャンは、どれも置いた状態で上から撮っているので）
# 2. 上から見た輪郭が一番重なる向き・位置を探す。形の違う2つを組み合わせるもの（ソースなど）は、大きさも合わせる
# 3a. 表と裏のスキャン（pair）: 上面のスキャンは高さの真ん中より上、下面のスキャンは真ん中より下だけを残す
#     （どちらのスキャンも、紙に接していた側は写っていないので、そこを捨てる）。上面の輪郭からはみ出した所も切る
# 3b. 別々の2つ（fit）や、1つを裏返して使うもの（self）: 切らずに、紙の面で背中合わせに貼り合わせる。
#     下面は実物にはない形なので、厚くなりすぎないよう、高さを BOTTOM_Z 倍につぶす
# 4. 底を高さ0、輪郭の中心を原点にそろえる（単位はm。Scaniverseのまま）

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

# (名前, 上面, 下面, 組み合わせ方) 組み合わせ方は、上の 3a・3b を参照
PAIRS = [
    ("bans-shita-01", "bans-shita-omote-01", "bans-shita-ura-01", "pair"),
    ("bans-shita-02", "bans-shita-omote-02", "bans-shita-ura-02", "pair"),
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
    ("onion", "onion-01", "onion-02", "fit"),
    # 溶けたチーズは1枚だけなので、それ自身をひっくり返して下面にする
    ("cheese-melted", "cheese-01-b", "cheese-01-b", "self"),
]

GRID = 0.001  # 輪郭を比べるときのマス目（1mm）
HALF = 0.1  # 比べる範囲（±10cm）
BOTTOM_Z = 0.4  # fit・self の下面の高さを、この倍率につぶす
OUTLINE_MARGIN = 0  # 上面の輪郭から、このマス数（mm）までのはみ出しは残す

only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


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
    ob.name = f"{tag}"
    return ob


def verts(ob):
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def raster(xy):
    """上から見た輪郭を、1mmのマス目の白黒画像にする（すき間は2マス太らせて埋める）"""
    n = int(2 * HALF / GRID)
    ij = np.clip(((xy + HALF) / GRID).astype(int), 0, n - 1)
    m = np.zeros((n, n), bool)
    m[ij[:, 0], ij[:, 1]] = True
    for _ in range(2):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    return m


def place(xy, theta, s, target_center):
    c, sn = math.cos(theta), math.sin(theta)
    p = (xy - xy.mean(axis=0)) @ np.array([[c, sn], [-sn, c]]) * s
    return p + target_center


def best_fit(top_xy, bot_xy, fit_scale):
    """下面の輪郭を回して（と、大きさを変えて）、上面の輪郭と一番重なる向きを探す。重なり具合（IoU）も返す"""
    tm = raster(top_xy)
    tc = top_xy.mean(axis=0)
    scales = np.arange(0.8, 1.21, 0.05) if fit_scale else [1.0]

    def score(theta, s):
        bm = raster(place(bot_xy, theta, s, tc))
        return (tm & bm).sum() / (tm | bm).sum()

    best = max(((score(t, s), t, s) for t in np.radians(np.arange(0, 360, 4)) for s in scales))
    # 近くを細かく探し直す
    _, t0, s0 = best
    fine_s = np.arange(s0 - 0.04, s0 + 0.041, 0.02) if fit_scale else [1.0]
    best = max(((score(t, s), t, s) for t in t0 + np.radians(np.arange(-4, 4.1, 0.5)) for s in fine_s))
    return best


def clip_outside(ob, outline_xy):
    """上から見て、outline_xy の輪郭の外にはみ出した面を消す"""
    n = int(2 * HALF / GRID)
    mask = raster(outline_xy)
    for _ in range(OUTLINE_MARGIN):
        mask = mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    out = []
    for f in bm.faces:
        c = f.calc_center_median()
        i, j = int((c.x + HALF) / GRID), int((c.y + HALF) / GRID)
        if not (0 <= i < n and 0 <= j < n and mask[i, j]):
            out.append(f)
    bmesh.ops.delete(bm, geom=out, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(ob.data)
    bm.free()


def cut(ob, z, keep_above):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, z), plane_no=(0, 0, 1),
                           clear_inner=keep_above, clear_outer=not keep_above)
    bm.to_mesh(ob.data)
    bm.free()


def merge(name, top_name, bot_name, mode):
    top = load(top_name, f"{name}-top")
    bot = load(bot_name, f"{name}-bottom")

    # 1. 下面をひっくり返す（x軸まわりに180°）。紙に接していた面が、上（高さ0）に来る
    bot.data.transform(Matrix.Rotation(math.pi, 4, "X"))

    # 2. 輪郭を合わせる
    tv, bv = verts(top), verts(bot)
    iou, theta, s = best_fit(tv[:, :2], bv[:, :2], mode == "fit")
    bc, tc = bv[:, :2].mean(axis=0), tv[:, :2].mean(axis=0)
    bot.data.transform(Matrix.Translation((-bc[0], -bc[1], 0)))
    bot.data.transform(Matrix.Rotation(-theta, 4, "Z"))
    bot.data.transform(Matrix.Diagonal((s, s, 1, 1)))
    bot.data.transform(Matrix.Translation((tc[0], tc[1], 0)))

    if mode == "pair":
        # 3a. 高さ: ひっくり返した下面の「紙に接していた面」は、実物の一番上。上面のスキャンの一番高い所に合わせる
        h_top = tv[:, 2].max()
        bot.data.transform(Matrix.Translation((0, 0, h_top)))
        h_bot_low = verts(bot)[:, 2].min()
        mid = (max(h_bot_low, 0) + h_top) / 2
        cut(top, mid, keep_above=True)
        cut(bot, mid, keep_above=False)
        clip_outside(bot, tv[:, :2])
    else:
        # 3b. 紙の面（高さ0）で背中合わせ。下面はつぶす
        bot.data.transform(Matrix.Diagonal((1, 1, BOTTOM_Z, 1)))
        clip_outside(bot, tv[:, :2])

    bpy.ops.object.select_all(action="DESELECT")
    top.select_set(True)
    bot.select_set(True)
    bpy.context.view_layer.objects.active = top
    bpy.ops.object.join()
    ob = top
    ob.name = ob.data.name = name

    # 4. 底を0、中心を原点に
    v = verts(ob)
    mn, mx = v.min(axis=0), v.max(axis=0)
    ob.data.transform(Matrix.Translation((-(mn[0] + mx[0]) / 2, -(mn[1] + mx[1]) / 2, -mn[2])))
    ob.data.update()
    size = (mx - mn) * 100
    print(f"## {name}: 重なり{iou:.2f} 回転{math.degrees(theta):.0f}° 大きさ×{s:.2f}"
          f"  幅{size[0]:.1f}×奥行{size[1]:.1f}×高さ{size[2]:.1f}cm")
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
                              use_selection=True, export_yup=True)
    ob.location = ((i % 5) * 0.2, -(i // 5) * 0.2, 0)  # .blend の中では格子状に並べる

if not only:
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(here, "scans-merged.blend"))

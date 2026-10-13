# 玉ねぎのモデル（02_assets/models-merged/onion.glb）を、小さなかけらに割って、刻み玉ねぎらしくする。
# ※ 今は使っていない（2026-10-13、ユーザーの判断で、割らずにWebで凸凹をランダムに付ける方針にした。look.js の「形のでこぼこ」）
# 結果は 02_assets/models-merged/onion-chopped.glb。先に merge_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/fracture_onion.py
#
# 割り方（ボロノイ分割）
# 1. 玉ねぎの中に PIECES 個の点（種）を散らす
# 2. 種ごとに、まわりの種との「ちょうど真ん中の面」で切っていき、その種に一番近い部分だけを残す（石が割れたような形になる）
# 3. 切り口は面でふさいで、閉じた立体にする。切り口は「玉ねぎの断面」用の白っぽいマテリアルにする
# 4. かけらを少し縮めて、すき間を空ける。かけらごとに原点をかけらの中心に置く（Webで、かけらを散らしたり傾けたりするため）

import bpy
import bmesh
import os
import random

import numpy as np
from mathutils import Matrix, Vector

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.normpath(os.path.join(here, "..", "models-merged", "onion.glb"))
dst = os.path.normpath(os.path.join(here, "..", "models-merged", "onion-chopped.glb"))

PIECES = 60  # かけらの数
NEIGHBORS = 14  # 切るときに見る、近くの種の数（ボロノイの形は近くの種だけで決まる）
INWARD = 0.35  # 種を、表面の点から中心へ寄せる割合（表面ぎりぎりだと、薄いかけらばかりになる）
SHRINK = 0.9  # かけらを縮める倍率（すき間を空ける）
CUT_COLOR = (0.92, 0.89, 0.78)  # 切り口の色（リニア）
COLOR_SCALE = 2.0  # 頂点カラーの倍率（merge_scans.py と同じ）。切り口の頂点カラーは 1/COLOR_SCALE（＝元の色のまま）にする
SEED = 7

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
base_ob = next(o for o in bpy.data.objects if o.type == "MESH")
base_ob.data.transform(base_ob.matrix_world)
base_ob.matrix_world = Matrix()
me = base_ob.data

cut_mat = bpy.data.materials.new("onion-cut")
cut_mat.use_nodes = True
bsdf = cut_mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (*CUT_COLOR, 1)
bsdf.inputs["Roughness"].default_value = 0.3
me.materials.append(cut_mat)
cut_index = len(me.materials) - 1

# 1. 種
co = np.empty(len(me.vertices) * 3)
me.vertices.foreach_get("co", co)
co = co.reshape(-1, 3)
center = co.mean(axis=0)
rnd = random.Random(SEED)
picks = co[[rnd.randrange(len(co)) for _ in range(PIECES)]]
seeds = [Vector(p + (center - p) * INWARD * rnd.uniform(0.6, 1.4)) for p in picks]

base = bmesh.new()
base.from_mesh(me)
# GLBはテクスチャの継ぎ目で頂点が分かれている。つながないと、切り口の輪が継ぎ目で途切れて、ふたが張れずに穴が開く
bmesh.ops.remove_doubles(base, verts=base.verts, dist=1e-6)

pieces = []
for i, s in enumerate(seeds):
    bm = base.copy()
    color_layer = bm.loops.layers.float_color.active or bm.loops.layers.color.active
    near = sorted((j for j in range(PIECES) if j != i), key=lambda j: (seeds[j] - s).length)[:NEIGHBORS]
    for j in near:
        n = (seeds[j] - s).normalized()
        res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                                     plane_co=(s + seeds[j]) / 2, plane_no=n, clear_outer=True)
        edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_boundary]
        if edges:
            filled = bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"]
            for f in bmesh.ops.triangulate(bm, faces=filled)["faces"]:
                f.material_index = cut_index
                if color_layer:
                    for lp in f.loops:
                        lp[color_layer] = (1 / COLOR_SCALE,) * 3 + (1,)
    # 念のため、残った穴もふさぎ、面の向きを外向きにそろえる（向きが逆だと、その面が見えなくなる）
    rest = [e for e in bm.edges if e.is_boundary]
    if rest:
        filled = bmesh.ops.holes_fill(bm, edges=rest, sides=0)["faces"]
        for f in bmesh.ops.triangulate(bm, faces=filled)["faces"]:
            f.material_index = cut_index
            if color_layer:
                for lp in f.loops:
                    lp[color_layer] = (1 / COLOR_SCALE,) * 3 + (1,)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if len(bm.faces) < 12:
        bm.free()
        continue
    # 4. かけらの中心へ縮めて、原点をかけらの中心にする
    c = sum((v.co for v in bm.verts), Vector()) / len(bm.verts)
    for v in bm.verts:
        v.co = c + (v.co - c) * SHRINK
    bmesh.ops.translate(bm, verts=bm.verts, vec=-c)
    pm = bpy.data.meshes.new(f"onion-piece-{len(pieces):02d}")
    bm.to_mesh(pm)
    bm.free()
    for m in me.materials:
        pm.materials.append(m)
    ob = bpy.data.objects.new(pm.name, pm)
    ob.location = c
    bpy.context.scene.collection.objects.link(ob)
    pieces.append(ob)

base.free()
bpy.data.objects.remove(base_ob)

bpy.ops.object.select_all(action="DESELECT")
for ob in pieces:
    ob.select_set(True)
bpy.context.view_layer.objects.active = pieces[0]
bpy.ops.object.shade_smooth_by_angle(angle=np.radians(50))  # 表面はなめらか、切り口の角は立てる
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_yup=True,
                          export_vertex_color="ACTIVE")
print(f"## onion-chopped: {len(pieces)}個のかけら → {dst}")

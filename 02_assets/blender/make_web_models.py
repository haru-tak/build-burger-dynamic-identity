# 組み合わせたスキャン（02_assets/models-merged/*.glb）を、Webアプリ用に軽くして 02_assets/models-web/ に書き出す。
# 先に trim_scans.py → merge_scans.py を実行しておく。
#
#   /Applications/Blender.app/Contents/MacOS/Blender -b -P 02_assets/blender/make_web_models.py
#
# - アプリ（03_work/web）は、models.json の約束で読む: 単位cm、底の中心が原点、stackHeight が積む厚み
# - 2種類書き出す
#   models-web/      ジェネレーター用（面 FULL_FACES 枚、テクスチャ FULL_TEX px）
#   models-web/lite/ 100万円バーガー用（8,300個並べるので、面 LITE_FACES 枚、テクスチャ LITE_TEX px）
# - マネキンは、代替モデル（02_assets/models/mannequin.glb）をそのまま使う

import bmesh
import bpy
import json
import math
import os
import shutil

import numpy as np
from mathutils import Matrix, Vector

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
# 頂点カラーを何倍して使うか（merge_scans.py の COLOR_SCALE と同じ値にする）
COLOR_SCALE = 2.0
# 積む厚み = 実際の高さ × この値（スキャンは表面がでこぼこなので、少し重ねると隙間が出ない）
OVERLAP = 0.85

PART_MIN_FACES = 24  # かけら1つあたりの面の数の下限（減らしすぎると、かけらの形がなくなる）

LEVELS = [
    ("", 3000, 1024),  # (サブフォルダ, 面の数, テクスチャの幅)
    ("lite", 400, 256),
]


def build(app_name, scan_name, faces, tex):
    """1つの具材を読み込んで、cmにし、面とテクスチャを減らす。
    刻み玉ねぎのように、複数の部品（かけら）からなるものは、部品ごとに原点を部品の中心に置く（Webで散らすため）"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(src_dir, f"{scan_name}.glb"))
    obs = [o for o in bpy.data.objects if o.type == "MESH"]
    parts = len(obs) > 1
    total = 0
    for ob in obs:
        ob.data = ob.data.copy() if ob.data.users > 1 else ob.data
        ob.data.transform(Matrix.Scale(100, 4) @ ob.matrix_world)  # m → cm
        ob.parent = None
        ob.matrix_world = Matrix()
        # GLBはテクスチャの継ぎ目で頂点が分かれているので、つなぎ直す。分かれたまま面を減らすと、穴だらけになる
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        bm.to_mesh(ob.data)
        bm.free()
        total += len(ob.data.polygons)
    for o in [o for o in bpy.data.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o)

    ratio = min(1.0, faces / total)
    for k, ob in enumerate(obs):
        ob.name = ob.data.name = f"{app_name}-piece-{k:02d}" if parts else app_name
        n = len(ob.data.polygons)
        r = max(ratio, min(1.0, PART_MIN_FACES / n)) if parts else ratio
        if r < 1:
            mod = ob.modifiers.new("decimate", "DECIMATE")
            mod.ratio = r
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.modifier_apply(modifier=mod.name)
        if parts:
            co = np.empty(len(ob.data.vertices) * 3)
            ob.data.vertices.foreach_get("co", co)
            c = co.reshape(-1, 3).mean(axis=0)
            ob.data.transform(Matrix.Translation(Vector(-c)))
            ob.location = Vector(c)

    # 面の境目で陰影が折れないように、なめらかな陰影にする（つやを出すと、カクカクが目立つ）。
    # かけらは、切り口の角だけは立てる
    bpy.ops.object.select_all(action="DESELECT")
    for ob in obs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    if parts:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(50))
    else:
        bpy.ops.object.shade_smooth()

    for img in bpy.data.images:
        if img.size[0] > tex:
            img.scale(tex, max(1, int(tex * img.size[1] / img.size[0])))

    height = max((ob.matrix_world @ v.co).z for ob in obs for v in ob.data.vertices)
    return obs, round(float(height * OVERLAP), 3)


manifest_by_level = {lv: {} for lv, _, _ in LEVELS}
for sub, faces, tex in LEVELS:
    d = os.path.join(out_dir, sub)
    os.makedirs(d, exist_ok=True)
    for app_name, scan_name in USE.items():
        obs, h = build(app_name, scan_name, faces, tex)
        bpy.ops.object.select_all(action="DESELECT")
        for ob in obs:
            ob.select_set(True)
        bpy.ops.export_scene.gltf(filepath=os.path.join(d, f"{app_name}.glb"), export_format="GLB",
                                  use_selection=True, export_yup=True,
                                  export_image_format="JPEG", export_jpeg_quality=85,
                                  export_vertex_color="ACTIVE")
        manifest_by_level[sub][app_name] = {"file": f"{app_name}.glb", "stackHeight": h, "scan": scan_name,
                                            "colorScale": COLOR_SCALE}
        size = os.path.getsize(os.path.join(d, f"{app_name}.glb")) / 1024
        print(f"## {sub or 'full'} {app_name} ← {scan_name}: {len(obs)}部品 {sum(len(o.data.polygons) for o in obs)}面 厚み{h}cm {size:.0f}KB")
    shutil.copy(os.path.join(placeholder_dir, "mannequin.glb"), os.path.join(d, "mannequin.glb"))
    manifest_by_level[sub]["mannequin"] = {"file": "mannequin.glb", "stackHeight": 160.0}
    with open(os.path.join(d, "models.json"), "w") as f:
        json.dump(manifest_by_level[sub], f, indent=2, ensure_ascii=False)
        f.write("\n")

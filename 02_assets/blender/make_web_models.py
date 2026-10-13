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
import os
import shutil

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
    "onion": "onion",
    "sauce-ketchup": "ketchup",
    "sauce-relish": "relish",
    "sauce-burger": "sauce",
}
# 積む厚み = 実際の高さ × この値（スキャンは表面がでこぼこなので、少し重ねると隙間が出ない）
OVERLAP = 0.85

LEVELS = [
    ("", 3000, 1024),  # (サブフォルダ, 面の数, テクスチャの幅)
    ("lite", 400, 256),
]


def build(app_name, scan_name, faces, tex):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(src_dir, f"{scan_name}.glb"))
    ob = next(o for o in bpy.data.objects if o.type == "MESH")
    ob.data.transform(ob.matrix_world)
    ob.matrix_world = Matrix()
    ob.data.transform(Matrix.Scale(100, 4))  # m → cm
    ob.name = ob.data.name = app_name

    # GLBはテクスチャの継ぎ目で頂点が分かれているので、つなぎ直す。分かれたまま面を減らすと、穴だらけになる
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bm.to_mesh(ob.data)
    bm.free()

    if len(ob.data.polygons) > faces:
        mod = ob.modifiers.new("decimate", "DECIMATE")
        mod.ratio = faces / len(ob.data.polygons)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.modifier_apply(modifier=mod.name)

    for img in bpy.data.images:
        if img.size[0] > tex:
            img.scale(tex, max(1, int(tex * img.size[1] / img.size[0])))

    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    height = co.reshape(-1, 3)[:, 2].max()
    return ob, round(float(height * OVERLAP), 3)


manifest_by_level = {lv: {} for lv, _, _ in LEVELS}
for sub, faces, tex in LEVELS:
    d = os.path.join(out_dir, sub)
    os.makedirs(d, exist_ok=True)
    for app_name, scan_name in USE.items():
        ob, h = build(app_name, scan_name, faces, tex)
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.ops.export_scene.gltf(filepath=os.path.join(d, f"{app_name}.glb"), export_format="GLB",
                                  use_selection=True, export_yup=True,
                                  export_image_format="JPEG", export_jpeg_quality=85)
        manifest_by_level[sub][app_name] = {"file": f"{app_name}.glb", "stackHeight": h, "scan": scan_name}
        size = os.path.getsize(os.path.join(d, f"{app_name}.glb")) / 1024
        print(f"## {sub or 'full'} {app_name} ← {scan_name}: {len(ob.data.polygons)}面 厚み{h}cm {size:.0f}KB")
    shutil.copy(os.path.join(placeholder_dir, "mannequin.glb"), os.path.join(d, "mannequin.glb"))
    manifest_by_level[sub]["mannequin"] = {"file": "mannequin.glb", "stackHeight": 160.0}
    with open(os.path.join(d, "models.json"), "w") as f:
        json.dump(manifest_by_level[sub], f, indent=2, ensure_ascii=False)
        f.write("\n")

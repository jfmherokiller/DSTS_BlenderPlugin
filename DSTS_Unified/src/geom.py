import bpy
import os
import re
import base64
import glob
from . import skeleton
from . import mesh
from . import material
from . import utils
from . import dsts_formats
from .. import data
from pathlib import Path
from .anim.InterfaceHundredLine import AnimFileHundredLine
from .anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine

def extract_missing_textures(geom, base_path, game_dir):
    """For every texture referenced by the geom's materials, if it isn't already
    present in base_path (as .img/.dds/.png), pull "<name>.img" (raw DDS bytes)
    directly out of the game's MVGL archives and write it into base_path.
    Returns (extracted_names, missing_names) for reporting."""
    from ..data import mvgl_archive
    from .material import find_texture_file

    archive_set = mvgl_archive.GameArchiveSet.get(game_dir)
    if not archive_set.is_valid():
        return [], []

    os.makedirs(base_path, exist_ok=True)

    tex_names = set()
    for mat_data in geom.materials:
        for uniform in mat_data.uniforms:
            if uniform.uniform_type == "texture":
                tex_names.add(uniform.value)

    extracted, missing = [], []
    for tex_name in sorted(tex_names):
        if find_texture_file(base_path, tex_name) is not None:
            continue  # already have it locally

        data = archive_set.extract_texture(tex_name)
        if data is None:
            missing.append(tex_name)
            continue

        out_path = os.path.join(base_path, tex_name + ".img")
        with open(out_path, "wb") as f:
            f.write(data)
        extracted.append(tex_name)

    return extracted, missing


def import_geom(context, filepath, images_folder=None, game_dir=None):
    error_state_old = dsts_formats.get_throw_errors()
    error_list_old = dsts_formats.get_error_list()
    dsts_formats.set_throw_errors(False)
    dsts_formats.set_error_list([])

    geom = dsts_formats.Geom.from_file(filepath)

    # Create collection
    new_collection = bpy.data.collections.new(name="Geom")
    context.scene.collection.children.link(new_collection)

    new_collection["unknown_0x10"] = geom.unknown_0x10
    new_collection["unknown_0x30"] = geom.unknown_0x30
    new_collection["unknown_0x34"] = geom.unknown_0x34

    new_collection["CLUT"] = base64.b64encode(bytes(geom.clut)).decode("ascii")

    # Import Skeleton
    armature_obj, bone_name_map = skeleton.import_skeleton(geom.skeleton, new_collection, utils.unflop)

    # Import Materials
    blender_materials = {}
    # Use custom images folder if provided, otherwise default to geom_folder/images
    if images_folder:
        base_path = images_folder.rstrip(os.sep)
    else:
        base_path = str(Path(filepath).parent) + os.sep + "images"

    if game_dir:
        extracted, missing = extract_missing_textures(geom, base_path, game_dir)
        if extracted:
            print(f"[DSTS] Extracted {len(extracted)} texture(s) from game files: {', '.join(extracted)}")
        if missing:
            print(f"[DSTS] Texture(s) not found in game archives: {', '.join(missing)}")

    for mat_data in geom.materials:
        mat = bpy.data.materials.new(name=mat_data.name)
        # Fix name collision handled by Blender
        mat_data.name = mat.name 
        material.resolve_material(new_collection, mat, mat_data, base_path)
        blender_materials[mat_data.name] = mat

    # Import Objects (Optimized)
    for mesh_obj in geom.meshes:
        # The new function handles mesh build, obj creation, linking, materials, and weights
        mesh.import_mesh_object(
            mesh_obj, 
            armature_obj, 
            blender_materials, 
            new_collection,
            bone_name_map=bone_name_map
        )
        #handle material vertex buffer layout
        mat = blender_materials[mesh_obj.material.name]
        attr_list = [
            {"count": attr.count, "offset": attr.offset, "atype": attr.atype, "dtype": attr.dtype}
            for attr in mesh_obj.mesh_attributes
        ]
        g_node = next(n for n in mat.node_tree.nodes if n.type == "GROUP" and n.node_tree.name.startswith(f"DSTS_Data-{mat.name}") )
        data_node = next(n for n in g_node.node_tree.nodes if type(n) == data.material_nodes.ShaderDataNode)
        if len(data_node.attributes) == 0:
            data_node.bytes_per_vertex = mesh_obj.bytes_per_vertex
            for attr in attr_list:
                at = data_node.attributes.add()
                at.count, at.offset, at.atype, at.dtype = attr.values()


    error_list = dsts_formats.get_error_list()
    
    # Filter out known benign warnings
    # - "float channels": known limitation, not a real error
    # - "Ibpm does not match": facial bones often have intentional scale differences
    # - "light data" / "camera data": not yet implemented, but doesn't affect model import
    error_list = [e for e in error_list if 
                  "float channels" not in e.lower() and 
                  "ibpm does not match" not in e.lower() and
                  "light data" not in e.lower() and
                  "camera data" not in e.lower()]

    if error_list:
        bpy.ops.wm.show_errors_window('INVOKE_DEFAULT', errors="\n\n".join(error_list))

    dsts_formats.set_throw_errors(error_state_old)
    dsts_formats.set_error_list(error_list_old)
    
    # Auto-import animations from the same directory
    import_animations_for_geom(filepath, armature_obj)
    
    return new_collection


def import_animations_for_geom(geom_filepath, armature_obj):
    """Automatically import .anim files that match the geom file"""
    if not armature_obj:
        return
    
    geom_dir = os.path.dirname(geom_filepath)
    geom_stem = os.path.splitext(os.path.basename(geom_filepath))[0]
    
    # Look for base animation (same name as geom)
    base_anim_path = os.path.join(geom_dir, geom_stem + ".anim")
    
    # Look for related animations (geom_*.anim pattern)
    related_anims = glob.glob(os.path.join(geom_dir, geom_stem + "_*.anim"))
    
    # Collect all animation paths
    anim_paths = []
    if os.path.isfile(base_anim_path):
        anim_paths.append(base_anim_path)
    anim_paths.extend(related_anims)
    
    if not anim_paths:
        return
    
    bone_names = [b.name for b in armature_obj.data.bones]
    
    # Ensure animation data exists
    if not armature_obj.animation_data:
        armature_obj.animation_data_create()
    
    for anim_path in anim_paths:
        try:
            anim_name = os.path.splitext(os.path.basename(anim_path))[0]
            
            # Load animation file
            ab = AnimFileBinaryHundredLine()
            ab.read(anim_path)
            anim_data = AnimFileHundredLine.from_binary(ab)
            
            # Import to NLA track
            _import_animation_to_nla(armature_obj, anim_name, anim_data, bone_names)
            
        except Exception as e:
            print(f"[DSTS] Warning: Failed to import animation {anim_path}: {e}")
            continue


def _import_animation_to_nla(armature_obj, anim_name, anim_data, bone_names):
    """Import animation data to Blender NLA track"""
    # Create action
    action = bpy.data.actions.new(anim_name)
    
    fps = 1  # Animation frames to Blender frames ratio
    
    # Process each bone
    for bone_idx, bone_name in enumerate(bone_names):
        if bone_idx >= anim_data.bone_count:
            break
        
        actiongroup = action.groups.new(bone_name)
        
        # Get rotation keyframes
        if bone_idx in anim_data.rotations:
            rotations = anim_data.rotations[bone_idx]
            if rotations:
                for quat_idx in range(4):
                    fc = action.fcurves.new(
                        data_path=f'pose.bones["{bone_name}"].rotation_quaternion',
                        index=quat_idx
                    )
                    fc.group = actiongroup
                    
                    for frame, rot in rotations.items():
                        # Convert from DSCS format [x,y,z,w] to Blender [w,x,y,z]
                        blender_quat = [rot[3], rot[0], rot[1], rot[2]]
                        kf = fc.keyframe_points.insert(float(fps * frame + 1), blender_quat[quat_idx])
                        kf.interpolation = 'BEZIER'
        
        # Get position keyframes
        if bone_idx in anim_data.positions:
            positions = anim_data.positions[bone_idx]
            if positions:
                for pos_idx in range(3):
                    fc = action.fcurves.new(
                        data_path=f'pose.bones["{bone_name}"].location',
                        index=pos_idx
                    )
                    fc.group = actiongroup
                    
                    for frame, pos in positions.items():
                        kf = fc.keyframe_points.insert(float(fps * frame + 1), pos[pos_idx])
                        kf.interpolation = 'LINEAR'
        
        # Get scale keyframes
        if bone_idx in anim_data.scales:
            scales = anim_data.scales[bone_idx]
            if scales:
                for scale_idx in range(3):
                    fc = action.fcurves.new(
                        data_path=f'pose.bones["{bone_name}"].scale',
                        index=scale_idx
                    )
                    fc.group = actiongroup
                    
                    for frame, scl in scales.items():
                        kf = fc.keyframe_points.insert(float(fps * frame + 1), scl[scale_idx])
                        kf.interpolation = 'LINEAR'
    
    # Create NLA track
    track = armature_obj.animation_data.nla_tracks.new()
    track.name = anim_name
    track.mute = True
    
    start_frame = int(action.frame_range[0]) if action.frame_range[0] > 0 else 1
    nla_strip = track.strips.new(action.name, start_frame, action)
    nla_strip.scale = 24.0 / anim_data.playback_rate if anim_data.playback_rate > 0 else 1.0
    nla_strip.blend_type = 'COMBINE'

def export_geom(collection):
    geom = dsts_formats.Geom()

    geom.unknown_0x10 = collection["unknown_0x10"]
    geom.unknown_0x30 = collection["unknown_0x30"]
    geom.unknown_0x34 = collection["unknown_0x34"]

    geom.clut = base64.b64decode(bytes(collection["CLUT"], "ascii"))

    armatures = [o for o in collection.objects if o.type == "ARMATURE"]
    if not armatures:
        self.report({'ERROR'}, "No armature found in collection")
        return {'CANCELLED'}

    geom.skeleton = skeleton.export_skeleton(armatures[0])

    name_to_mat = {}
    for mat in {o.data.materials[0] for o in collection.objects if o.type == "MESH"}:
        mat_data = material.export_material(mat)
        geom.materials.append(mat_data)
        name_to_mat[mat_data.name] = mat_data

    for obj in [o for o in collection.objects if o.type == "MESH"]:
        mesh_out = mesh.export_mesh_object(obj, geom.skeleton)
        mat_name = obj.data.materials[0].name
        mesh_out.material = name_to_mat[mat_name]
        geom.meshes.append(mesh_out)

    for obj in [*geom.skeleton.bones] + [*geom.materials] + [*geom.meshes]:
        obj.name = re.sub("(\.[0-9]{3})?$", "", obj.name)

    return geom


def export_textures(collection, output_dir, as_png=False):
    """
    Export all textures used by materials in the collection.
    
    Args:
        collection: The Blender collection containing meshes with materials
        output_dir: Base output directory
        as_png: If True, export as PNG files; otherwise export as DDS/IMG
    
    Returns a list of exported texture names.
    """
    import numpy as np
    
    # Create images folder
    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    
    exported = []
    processed_images = set()
    
    # Find all materials in the collection
    for obj in collection.objects:
        if obj.type != "MESH" or not obj.data.materials:
            continue
        
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes:
                continue
            
            # Find the DSTS group node
            for node in mat.node_tree.nodes:
                if node.type == "GROUP" and node.node_tree and node.node_tree.name.startswith(f"DSTS_Data-"):
                    # Search inside the group for texture nodes
                    for g_node in node.node_tree.nodes:
                        if isinstance(g_node, bpy.types.ShaderNodeTexImage) and g_node.image:
                            img = g_node.image
                            
                            # Skip if already processed
                            if img.name in processed_images:
                                continue
                            processed_images.add(img.name)
                            
                            # Get texture name (without extension)
                            tex_name = os.path.splitext(img.name)[0]
                            
                            try:
                                if as_png:
                                    output_path = os.path.join(images_dir, tex_name + ".png")
                                    export_image_as_png(img, output_path)
                                else:
                                    output_path = os.path.join(images_dir, tex_name + ".img")
                                    export_image_as_dds(img, output_path)
                                exported.append(tex_name)
                            except Exception as e:
                                print(f"[DSTS] Warning: Failed to export texture {tex_name}: {e}")
    
    return exported


def export_image_as_dds(image, output_path):
    """
    Export a Blender image to DDS format.
    """
    import numpy as np
    
    # Check if image is packed or has filepath
    if image.packed_file:
        # Image is packed - try to save directly if it's already DDS
        data = image.packed_file.data
        if data[:4] == b'DDS ':
            # Already DDS format, save directly
            with open(output_path, 'wb') as f:
                f.write(data)
            return
    
    # If image has a valid filepath and it's a DDS/IMG, copy it
    if image.filepath:
        src_path = bpy.path.abspath(image.filepath)
        if os.path.isfile(src_path):
            # Check if source is already DDS
            with open(src_path, 'rb') as f:
                header = f.read(4)
            if header == b'DDS ':
                import shutil
                shutil.copy2(src_path, output_path)
                return
    
    # Otherwise, we need to convert from Blender's internal format
    # This requires creating a DDS file from pixel data
    import numpy as np
    
    width = image.size[0]
    height = image.size[1]
    
    if width == 0 or height == 0:
        raise ValueError(f"Image {image.name} has invalid dimensions")
    
    # Get pixel data (RGBA float)
    pixels = np.array(image.pixels[:], dtype=np.float32)
    pixels = pixels.reshape((height, width, 4))
    
    # Flip vertically (DDS is stored bottom-up in some cases)
    pixels = np.flipud(pixels)
    
    # Convert to 8-bit BGRA
    pixels_8bit = (pixels * 255).astype(np.uint8)
    
    # Swap R and B channels (RGBA -> BGRA)
    pixels_bgra = pixels_8bit.copy()
    pixels_bgra[:, :, 0] = pixels_8bit[:, :, 2]  # B = R
    pixels_bgra[:, :, 2] = pixels_8bit[:, :, 0]  # R = B
    
    # Build DDS header manually for uncompressed BGRA
    dds_header = bytearray(128)
    
    # Magic number
    dds_header[0:4] = b'DDS '
    
    # Header size (124)
    dds_header[4:8] = (124).to_bytes(4, 'little')
    
    # Flags (CAPS | HEIGHT | WIDTH | PIXELFORMAT | PITCH)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8
    dds_header[8:12] = flags.to_bytes(4, 'little')
    
    # Height
    dds_header[12:16] = height.to_bytes(4, 'little')
    
    # Width
    dds_header[16:20] = width.to_bytes(4, 'little')
    
    # Pitch (width * 4 bytes per pixel)
    pitch = width * 4
    dds_header[20:24] = pitch.to_bytes(4, 'little')
    
    # Depth (0 for 2D)
    dds_header[24:28] = (0).to_bytes(4, 'little')
    
    # Mipmap count (1)
    dds_header[28:32] = (1).to_bytes(4, 'little')
    
    # Reserved (11 DWORDs)
    # Already zero
    
    # Pixel format starts at offset 76
    # Pixel format size (32)
    dds_header[76:80] = (32).to_bytes(4, 'little')
    
    # Pixel format flags (ALPHAPIXELS | RGB)
    pf_flags = 0x1 | 0x40
    dds_header[80:84] = pf_flags.to_bytes(4, 'little')
    
    # FourCC (0 for uncompressed)
    dds_header[84:88] = (0).to_bytes(4, 'little')
    
    # RGB bit count (32)
    dds_header[88:92] = (32).to_bytes(4, 'little')
    
    # R mask (0x00FF0000)
    dds_header[92:96] = (0x00FF0000).to_bytes(4, 'little')
    
    # G mask (0x0000FF00)
    dds_header[96:100] = (0x0000FF00).to_bytes(4, 'little')
    
    # B mask (0x000000FF)
    dds_header[100:104] = (0x000000FF).to_bytes(4, 'little')
    
    # A mask (0xFF000000)
    dds_header[104:108] = (0xFF000000).to_bytes(4, 'little')
    
    # Caps (TEXTURE)
    dds_header[108:112] = (0x1000).to_bytes(4, 'little')
    
    # Write DDS file
    with open(output_path, 'wb') as f:
        f.write(dds_header)
        f.write(pixels_bgra.tobytes())
    
    return True


def export_image_as_png(image, output_path):
    """
    Export a Blender image to PNG format.
    """
    # Store original settings. Images loaded straight from a raw DDS/.img file
    # (Blender can read DDS but has no matching file_format enum identifier for
    # it) report a file_format value Blender itself can't re-assign -- reading
    # it back here already emits an RNA warning and yields ''. Restoring that
    # in the finally block would raise and mask an otherwise-successful save,
    # so the restore is best-effort only.
    original_path = image.filepath_raw
    original_format = image.file_format

    try:
        # Set up for PNG export
        image.filepath_raw = output_path
        image.file_format = 'PNG'
        image.save()
    finally:
        # Restore original settings
        image.filepath_raw = original_path
        try:
            image.file_format = original_format
        except Exception:
            pass

    return True
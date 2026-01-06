"""
DSTS Unified - Digimon Time Stranger Blender Tools
Combines geom import/export with animation import/export support.
"""

import bpy
import os
import re
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, EnumProperty, BoolProperty
from bpy.types import Operator

bl_info = {
    "name": "DSTS Unified (Digimon Time Stranger)",
    "author": "Nymic_Razor, Pherakki (Animation), Combined by Assistant",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "category": "Import-Export",
    "description": "Import/Export .geom and .anim files for Digimon Time Stranger",
    "location": "File > Import/Export",
}


# =============================================================================
# GEOM IMPORT/EXPORT OPERATORS
# =============================================================================

class DSTS_OT_GeomImport(Operator, ImportHelper):
    """Import DSTS .geom file"""
    bl_idname = "import_scene.dsts_geom"
    bl_label = "DSTS Geom (.geom)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".geom"
    filter_glob: StringProperty(
        default="*.geom",
        options={'HIDDEN'}
    )
    
    use_custom_images_path: BoolProperty(
        name="Custom Images Folder",
        description="Use a custom folder for loading textures instead of the default 'images' subfolder",
        default=False
    )
    
    images_path: StringProperty(
        name="Images Folder",
        description="Folder containing texture files (.img/.dds)",
        subtype='DIR_PATH',
        default=""
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        layout.prop(self, "use_custom_images_path")
        
        row = layout.row()
        row.enabled = self.use_custom_images_path
        row.prop(self, "images_path")

    def execute(self, context):
        from .src.geom import import_geom
        
        # Determine images path
        if self.use_custom_images_path and self.images_path:
            images_folder = bpy.path.abspath(self.images_path)
        else:
            images_folder = None  # Use default (geom_folder/images)
        
        imported_collection = import_geom(context, self.filepath, images_folder)
        self.report({'INFO'}, f"Imported: {imported_collection.name}")
        return {'FINISHED'}


class DSTS_OT_GeomExport(Operator, ExportHelper):
    """Export DSTS .geom file"""
    bl_idname = "export_scene.dsts_geom"
    bl_label = "DSTS Geom (.geom)"
    bl_options = {'REGISTER', 'UNDO'}

    def collection_items(self, context):
        return [(col.name, col.name, "") for col in bpy.data.collections if "unknown_0x10" in col]

    collection_name: EnumProperty(
        name="Collection",
        description="Select collection to export",
        items=collection_items
    )
    
    export_textures: BoolProperty(
        name="Export Textures",
        description="Export all textures used by materials to an 'images' folder",
        default=True
    )
    
    export_textures_as_png: BoolProperty(
        name="Export as PNG",
        description="Export textures as PNG files instead of .img (DDS) format",
        default=False
    )

    filename_ext = ".geom"
    filter_glob: StringProperty(
        default="*.geom",
        options={'HIDDEN'}
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "export_textures")
        
        row = layout.row()
        row.enabled = self.export_textures
        row.prop(self, "export_textures_as_png")

    def execute(self, context):
        from .src.geom import export_geom, export_textures
        from .src.nlst import write_nlst
        
        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        geom = export_geom(collection)
        geom.to_file(self.filepath)

        # Write nlst file
        with open(os.path.splitext(self.filepath)[0] + ".nlst", "wb") as f:
            f.write(write_nlst(geom).encode("utf-8"))
        
        # Export textures if enabled
        if self.export_textures:
            output_dir = os.path.dirname(self.filepath)
            exported_textures = export_textures(collection, output_dir, as_png=self.export_textures_as_png)
            if exported_textures:
                fmt = "PNG" if self.export_textures_as_png else "IMG"
                self.report({'INFO'}, f"Exported: {self.filepath} + {len(exported_textures)} {fmt} texture(s)")
            else:
                self.report({'INFO'}, f"Exported: {self.filepath} (no textures found)")
        else:
            self.report({'INFO'}, f"Exported: {self.filepath}")
        
        return {'FINISHED'}

    def invoke(self, context, event):
        collection = bpy.data.collections.get(self.collection_name)
        if collection:
            blend_dir = os.path.dirname(bpy.data.filepath)
            default_name = f"{collection.name}.geom"
            self.filepath = os.path.join(blend_dir, default_name)
        else:
            self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# =============================================================================
# ANIMATION IMPORT/EXPORT OPERATORS
# =============================================================================

def fetch_armatures(self, context):
    """Get list of armatures in scene for dropdown"""
    armature_list = []
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            armature_list.append((obj.name, obj.name, obj.name, "OUTLINER_OB_ARMATURE", len(armature_list)))
    return tuple(armature_list) if armature_list else (("NONE", "No Armatures", "No armatures in scene", "ERROR", 0),)


def fetch_nla_tracks(self, context):
    """Get list of NLA tracks for the selected armature"""
    if self.armature_name == "NONE" or self.armature_name not in bpy.data.objects:
        return (("NONE", "No Armature Selected", "", "ERROR", 0),)
    
    armature_obj = bpy.data.objects[self.armature_name]
    if armature_obj.type != "ARMATURE" or not armature_obj.animation_data or not armature_obj.animation_data.nla_tracks:
        return (("NONE", "No NLA Tracks", "", "ERROR", 0),)
    
    track_list = []
    for track in armature_obj.animation_data.nla_tracks:
        if not track.mute and track.strips:
            track_list.append((track.name, track.name, track.name, "ACTION", len(track_list)))
    return tuple(track_list) if track_list else (("NONE", "No Active Tracks", "", "ERROR", 0),)


class DSTS_OT_AnimImport(Operator, ImportHelper):
    """Import DSTS .anim file"""
    bl_idname = "import_scene.dsts_anim"
    bl_label = "DSTS Animation (.anim)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".anim"
    filter_glob: StringProperty(
        default="*.anim",
        options={'HIDDEN'}
    )

    files: bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)

    armature_name: EnumProperty(
        items=fetch_armatures,
        name="Armature"
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")

    def execute(self, context):
        from .src.anim import AnimFileHundredLine
        from .src.anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine
        from mathutils import Quaternion
        
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        
        folder = os.path.dirname(self.filepath)
        
        for f in self.files:
            filepath = os.path.join(folder, f.name)
            anim_name = os.path.splitext(f.name)[0]
            
            try:
                # Load animation file
                ab = AnimFileBinaryHundredLine()
                ab.read(filepath)
                anim_data = AnimFileHundredLine.from_binary(ab)
                
                # Import animation to NLA track
                self._import_animation(armature_obj, anim_name, anim_data)
                
            except Exception as e:
                self.report({'WARNING'}, f"Failed to import {f.name}: {str(e)}")
                continue
        
        self.report({'INFO'}, f"Imported {len(self.files)} animation(s)")
        return {'FINISHED'}

    def _import_animation(self, armature_obj, anim_name, anim_data):
        """Import animation data to Blender NLA track"""
        from mathutils import Quaternion
        
        bone_names = [b.name for b in armature_obj.data.bones]
        
        # Create action
        action = bpy.data.actions.new(anim_name)
        
        # Ensure animation data exists
        if not armature_obj.animation_data:
            armature_obj.animation_data_create()
        
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
                    # Create fcurves for quaternion
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


class DSTS_OT_AnimExport(Operator, ExportHelper):
    """Export DSTS .anim file"""
    bl_idname = "export_scene.dsts_anim"
    bl_label = "DSTS Animation (.anim)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".anim"
    filter_glob: StringProperty(
        default="*.anim",
        options={'HIDDEN'}
    )

    armature_name: EnumProperty(
        items=fetch_armatures,
        name="Armature"
    )

    nla_track_name: EnumProperty(
        items=fetch_nla_tracks,
        name="NLA Track"
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")
        layout.prop(self, "nla_track_name")

    def execute(self, context):
        from .src.anim import AnimFileHundredLine
        
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        
        if not armature_obj.animation_data:
            self.report({'ERROR'}, "Armature has no animation data")
            return {'CANCELLED'}
        
        # Find NLA track
        nla_track = None
        for track in armature_obj.animation_data.nla_tracks:
            if track.name == self.nla_track_name:
                nla_track = track
                break
        
        if not nla_track or not nla_track.strips:
            self.report({'ERROR'}, f"NLA track '{self.nla_track_name}' not found or empty")
            return {'CANCELLED'}
        
        # Get action from strip
        nla_strip = nla_track.strips[0]
        action = nla_strip.action
        if not action:
            self.report({'ERROR'}, "NLA strip has no action")
            return {'CANCELLED'}
        
        bone_names = [b.name for b in armature_obj.data.bones]
        
        # Create animation file
        anim_file = AnimFileHundredLine()
        anim_file.playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
        anim_file.bone_count = len(bone_names)
        anim_file.float_channel_count = 0
        anim_file.bone_blend_factors = [255] * anim_file.bone_count
        anim_file.float_channel_blend_factors = []
        
        # Initialize data structures
        for bone_idx in range(anim_file.bone_count):
            anim_file.rotations[bone_idx] = {}
            anim_file.positions[bone_idx] = {}
            anim_file.scales[bone_idx] = {}
        
        action_frame_start = int(action.frame_range[0]) if action.frame_range else 1
        
        # Extract fcurves from action
        for fcurve in action.fcurves:
            data_path = fcurve.data_path
            
            # Parse bone name from data path
            if 'pose.bones["' in data_path:
                bone_name = data_path.split('pose.bones["')[1].split('"]')[0]
                if bone_name not in bone_names:
                    continue
                bone_idx = bone_names.index(bone_name)
                
                # Determine transform type
                if 'rotation_quaternion' in data_path:
                    for kf in fcurve.keyframe_points:
                        frame_anim = int(kf.co[0]) - action_frame_start
                        if frame_anim < 0:
                            continue
                        if frame_anim not in anim_file.rotations[bone_idx]:
                            anim_file.rotations[bone_idx][frame_anim] = [0, 0, 0, 1]
                        
                        # Convert Blender [w,x,y,z] to DSCS [x,y,z,w]
                        quat_component = fcurve.array_index
                        if quat_component == 0:  # w -> [3]
                            anim_file.rotations[bone_idx][frame_anim][3] = kf.co[1]
                        else:  # x,y,z -> [0,1,2]
                            anim_file.rotations[bone_idx][frame_anim][quat_component - 1] = kf.co[1]
                
                elif 'location' in data_path:
                    for kf in fcurve.keyframe_points:
                        frame_anim = int(kf.co[0]) - action_frame_start
                        if frame_anim < 0:
                            continue
                        if frame_anim not in anim_file.positions[bone_idx]:
                            anim_file.positions[bone_idx][frame_anim] = [0, 0, 0]
                        anim_file.positions[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
                
                elif 'scale' in data_path:
                    for kf in fcurve.keyframe_points:
                        frame_anim = int(kf.co[0]) - action_frame_start
                        if frame_anim < 0:
                            continue
                        if frame_anim not in anim_file.scales[bone_idx]:
                            anim_file.scales[bone_idx][frame_anim] = [1, 1, 1]
                        anim_file.scales[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
        
        # Write animation file
        try:
            anim_file.to_file(self.filepath)
            self.report({'INFO'}, f"Exported animation to {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export: {str(e)}")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class DSTS_OT_AnimExportAll(Operator):
    """Export all NLA tracks as separate .anim files"""
    bl_idname = "export_scene.dsts_anim_all"
    bl_label = "DSTS All Animations"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(
        name="Output Directory",
        subtype='DIR_PATH'
    )

    armature_name: EnumProperty(
        items=fetch_armatures,
        name="Armature"
    )
    
    skip_muted: BoolProperty(
        name="Skip Muted Tracks",
        description="Skip NLA tracks that are muted",
        default=False
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")
        layout.prop(self, "skip_muted")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .src.anim import AnimFileHundredLine
        
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        
        if not armature_obj.animation_data or not armature_obj.animation_data.nla_tracks:
            self.report({'ERROR'}, "Armature has no NLA tracks")
            return {'CANCELLED'}
        
        bone_names = [b.name for b in armature_obj.data.bones]
        exported_count = 0
        failed_count = 0
        
        for nla_track in armature_obj.animation_data.nla_tracks:
            # Skip muted tracks if option is enabled
            if self.skip_muted and nla_track.mute:
                continue
            
            if not nla_track.strips:
                continue
            
            nla_strip = nla_track.strips[0]
            action = nla_strip.action
            if not action:
                continue
            
            # Create animation file
            anim_file = AnimFileHundredLine()
            anim_file.playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
            anim_file.bone_count = len(bone_names)
            anim_file.float_channel_count = 0
            anim_file.bone_blend_factors = [255] * anim_file.bone_count
            anim_file.float_channel_blend_factors = []
            
            # Initialize data structures
            for bone_idx in range(anim_file.bone_count):
                anim_file.rotations[bone_idx] = {}
                anim_file.positions[bone_idx] = {}
                anim_file.scales[bone_idx] = {}
            
            action_frame_start = int(action.frame_range[0]) if action.frame_range else 1
            
            # Extract fcurves from action
            for fcurve in action.fcurves:
                data_path = fcurve.data_path
                
                if 'pose.bones["' in data_path:
                    bone_name = data_path.split('pose.bones["')[1].split('"]')[0]
                    if bone_name not in bone_names:
                        continue
                    bone_idx = bone_names.index(bone_name)
                    
                    if 'rotation_quaternion' in data_path:
                        for kf in fcurve.keyframe_points:
                            frame_anim = int(kf.co[0]) - action_frame_start
                            if frame_anim < 0:
                                continue
                            if frame_anim not in anim_file.rotations[bone_idx]:
                                anim_file.rotations[bone_idx][frame_anim] = [0, 0, 0, 1]
                            
                            quat_component = fcurve.array_index
                            if quat_component == 0:
                                anim_file.rotations[bone_idx][frame_anim][3] = kf.co[1]
                            else:
                                anim_file.rotations[bone_idx][frame_anim][quat_component - 1] = kf.co[1]
                    
                    elif 'location' in data_path:
                        for kf in fcurve.keyframe_points:
                            frame_anim = int(kf.co[0]) - action_frame_start
                            if frame_anim < 0:
                                continue
                            if frame_anim not in anim_file.positions[bone_idx]:
                                anim_file.positions[bone_idx][frame_anim] = [0, 0, 0]
                            anim_file.positions[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
                    
                    elif 'scale' in data_path:
                        for kf in fcurve.keyframe_points:
                            frame_anim = int(kf.co[0]) - action_frame_start
                            if frame_anim < 0:
                                continue
                            if frame_anim not in anim_file.scales[bone_idx]:
                                anim_file.scales[bone_idx][frame_anim] = [1, 1, 1]
                            anim_file.scales[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
            
            # Write animation file
            filepath = os.path.join(self.directory, nla_track.name + ".anim")
            try:
                anim_file.to_file(filepath)
                exported_count += 1
            except Exception as e:
                print(f"[DSTS] Failed to export {nla_track.name}: {e}")
                failed_count += 1
        
        if exported_count > 0:
            self.report({'INFO'}, f"Exported {exported_count} animation(s) to {self.directory}")
        if failed_count > 0:
            self.report({'WARNING'}, f"Failed to export {failed_count} animation(s)")
        
        if exported_count == 0 and failed_count == 0:
            self.report({'WARNING'}, "No animations to export")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class DSTS_OT_ExportAll(Operator, ExportHelper):
    """Export everything: Geom + Textures + All Animations"""
    bl_idname = "export_scene.dsts_all"
    bl_label = "DSTS Export All"
    bl_options = {'REGISTER', 'UNDO'}

    def collection_items(self, context):
        return [(col.name, col.name, "") for col in bpy.data.collections if "unknown_0x10" in col]

    collection_name: EnumProperty(
        name="Collection",
        description="Select collection to export",
        items=collection_items
    )
    
    export_textures_as_png: BoolProperty(
        name="Export Textures as PNG",
        description="Export textures as PNG files instead of .img (DDS) format",
        default=False
    )

    filename_ext = ".geom"
    filter_glob: StringProperty(
        default="*.geom",
        options={'HIDDEN'}
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "export_textures_as_png")
        layout.separator()
        tex_fmt = "PNG" if self.export_textures_as_png else "IMG"
        layout.label(text="Will export:")
        layout.label(text="  • Geom file (.geom)")
        layout.label(text="  • Name list (.nlst)")
        layout.label(text=f"  • All textures (images/*.{tex_fmt.lower()})")
        layout.label(text="  • All animations (.anim)")

    def execute(self, context):
        from .src.geom import export_geom, export_textures
        from .src.nlst import write_nlst
        from .src.anim import AnimFileHundredLine
        
        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}
        
        output_dir = os.path.dirname(self.filepath)
        base_name = os.path.splitext(os.path.basename(self.filepath))[0]
        
        # --- Export Geom ---
        geom = export_geom(collection)
        geom.to_file(self.filepath)
        
        # --- Export NLST ---
        nlst_path = os.path.splitext(self.filepath)[0] + ".nlst"
        with open(nlst_path, "wb") as f:
            f.write(write_nlst(geom).encode("utf-8"))
        
        # --- Export Textures ---
        exported_textures = export_textures(collection, output_dir, as_png=self.export_textures_as_png)
        
        # --- Export All Animations ---
        armatures = [o for o in collection.objects if o.type == "ARMATURE"]
        exported_anims = 0
        
        if armatures:
            armature_obj = armatures[0]
            if armature_obj.animation_data and armature_obj.animation_data.nla_tracks:
                bone_names = [b.name for b in armature_obj.data.bones]
                
                for nla_track in armature_obj.animation_data.nla_tracks:
                    if not nla_track.strips:
                        continue
                    
                    nla_strip = nla_track.strips[0]
                    action = nla_strip.action
                    if not action:
                        continue
                    
                    # Create animation file
                    anim_file = AnimFileHundredLine()
                    anim_file.playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
                    anim_file.bone_count = len(bone_names)
                    anim_file.float_channel_count = 0
                    anim_file.bone_blend_factors = [255] * anim_file.bone_count
                    anim_file.float_channel_blend_factors = []
                    
                    for bone_idx in range(anim_file.bone_count):
                        anim_file.rotations[bone_idx] = {}
                        anim_file.positions[bone_idx] = {}
                        anim_file.scales[bone_idx] = {}
                    
                    action_frame_start = int(action.frame_range[0]) if action.frame_range else 1
                    
                    for fcurve in action.fcurves:
                        data_path = fcurve.data_path
                        
                        if 'pose.bones["' in data_path:
                            bone_name = data_path.split('pose.bones["')[1].split('"]')[0]
                            if bone_name not in bone_names:
                                continue
                            bone_idx = bone_names.index(bone_name)
                            
                            if 'rotation_quaternion' in data_path:
                                for kf in fcurve.keyframe_points:
                                    frame_anim = int(kf.co[0]) - action_frame_start
                                    if frame_anim < 0:
                                        continue
                                    if frame_anim not in anim_file.rotations[bone_idx]:
                                        anim_file.rotations[bone_idx][frame_anim] = [0, 0, 0, 1]
                                    
                                    quat_component = fcurve.array_index
                                    if quat_component == 0:
                                        anim_file.rotations[bone_idx][frame_anim][3] = kf.co[1]
                                    else:
                                        anim_file.rotations[bone_idx][frame_anim][quat_component - 1] = kf.co[1]
                            
                            elif 'location' in data_path:
                                for kf in fcurve.keyframe_points:
                                    frame_anim = int(kf.co[0]) - action_frame_start
                                    if frame_anim < 0:
                                        continue
                                    if frame_anim not in anim_file.positions[bone_idx]:
                                        anim_file.positions[bone_idx][frame_anim] = [0, 0, 0]
                                    anim_file.positions[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
                            
                            elif 'scale' in data_path:
                                for kf in fcurve.keyframe_points:
                                    frame_anim = int(kf.co[0]) - action_frame_start
                                    if frame_anim < 0:
                                        continue
                                    if frame_anim not in anim_file.scales[bone_idx]:
                                        anim_file.scales[bone_idx][frame_anim] = [1, 1, 1]
                                    anim_file.scales[bone_idx][frame_anim][fcurve.array_index] = kf.co[1]
                    
                    # Write animation file
                    anim_path = os.path.join(output_dir, nla_track.name + ".anim")
                    try:
                        anim_file.to_file(anim_path)
                        exported_anims += 1
                    except Exception as e:
                        print(f"[DSTS] Failed to export animation {nla_track.name}: {e}")
        
        # Report results
        results = [f"Geom: {self.filepath}"]
        results.append(f"NLST: {nlst_path}")
        if exported_textures:
            results.append(f"Textures: {len(exported_textures)}")
        if exported_anims:
            results.append(f"Animations: {exported_anims}")
        
        self.report({'INFO'}, f"Exported: {', '.join(results)}")
        return {'FINISHED'}

    def invoke(self, context, event):
        collection = bpy.data.collections.get(self.collection_name)
        if collection:
            blend_dir = os.path.dirname(bpy.data.filepath)
            default_name = f"{collection.name}.geom"
            self.filepath = os.path.join(blend_dir, default_name)
        else:
            self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# =============================================================================
# ERROR WINDOW OPERATOR
# =============================================================================

class DSTS_OT_ShowErrors(Operator):
    """Show errors in a new window"""
    bl_idname = "wm.show_errors_window"
    bl_label = "Import Errors"

    errors: StringProperty()
    window_name: StringProperty(default="Import Errors Window")
    text_name: StringProperty(default="Import Errors")

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
        new_window = context.window_manager.windows[-1]

        for area in new_window.screen.areas:
            area.type = 'TEXT_EDITOR'
            for space in area.spaces:
                if space.type == 'TEXT_EDITOR':
                    text = bpy.data.texts.new(name=self.text_name)
                    text.clear()
                    text.write("Errors during import:\n\n" + self.errors)
                    space.text = text
                    text.current_line_index = 0
                    space.top = 0

        new_window.screen.name = self.window_name
        return {'FINISHED'}


# =============================================================================
# SUBMENUS
# =============================================================================

class DSTS_MT_ImportSubmenu(bpy.types.Menu):
    """DSTS Import Submenu"""
    bl_idname = "DSTS_MT_import_submenu"
    bl_label = "DSTS"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_GeomImport.bl_idname, text="DSTS Geom")
        layout.operator(DSTS_OT_AnimImport.bl_idname, text="DSTS Animation")


class DSTS_MT_ExportSubmenu(bpy.types.Menu):
    """DSTS Export Submenu"""
    bl_idname = "DSTS_MT_export_submenu"
    bl_label = "DSTS"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_ExportAll.bl_idname, text="DSTS Export All")
        layout.separator()
        layout.operator(DSTS_OT_GeomExport.bl_idname, text="DSTS Geom Only")
        layout.operator(DSTS_OT_AnimExport.bl_idname, text="DSTS Animation (Single)")
        layout.operator(DSTS_OT_AnimExportAll.bl_idname, text="DSTS Animations (All)")


def menu_func_import(self, context):
    self.layout.menu(DSTS_MT_ImportSubmenu.bl_idname)


def menu_func_export(self, context):
    self.layout.menu(DSTS_MT_ExportSubmenu.bl_idname)


# =============================================================================
# REGISTRATION
# =============================================================================

CLASSES = (
    DSTS_OT_GeomImport,
    DSTS_OT_GeomExport,
    DSTS_OT_AnimImport,
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_ExportAll,
    DSTS_OT_ShowErrors,
    DSTS_MT_ImportSubmenu,
    DSTS_MT_ExportSubmenu,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    
    # Register material nodes from data module
    from .data import material_nodes
    material_nodes.register()


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    
    from .data import material_nodes
    material_nodes.unregister()


if __name__ == "__main__":
    register()

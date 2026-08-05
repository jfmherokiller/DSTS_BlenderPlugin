"""Pure-Python reimplementation of dsts_formats.cp311-win_amd64.pyd.

The compiled extension is Windows-only (built as a pybind11 C++ module targeting
CPython 3.11 on win_amd64) and has no Linux build. Because its filename
(dsts_formats.cp311-win_amd64.pyd) doesn't match any of Python's extension-loader
suffixes on Linux, this plain .py file is picked up automatically there via
normal `import dsts_formats` resolution -- no changes needed to geom.py / mesh.py /
material.py / skeleton.py. On Windows the compiled .pyd still shadows this file
(extension loaders are tried before source loaders for the same module name), so
this is purely additive.

Format knowledge here comes from reverse-engineering the compiled .pyd in IDA
(see RE/dsts_formats_pyd_re.md in the repo root for the full trace) plus empirical
validation against a real extracted .geom file. Header, name tables, skeleton
(bone rest-pose transforms), per-mesh vertex/index geometry, matrix palettes, and
per-material shader-uniform/texture bindings are all implemented and validated
end-to-end through the real addon import pipeline in live Blender against a real
.geom file. Not implemented: export (writing a .geom back out -- Mesh.set_*
methods raise NotImplementedError) and ShaderSetting data (real on-disk location
not found -- see the RE doc). Shader.name stays blank by design, not as a gap:
confirmed against the compiled module that it has no field-level Python binding
at all (see the RE doc's "Shader.name isn't a parser gap" section).
"""

import os
import struct
import zlib


def name_hash(name):
    """CRC-32 of the UTF-8/ASCII name bytes, without the standard final XOR
    complement step (equivalent to `zlib.crc32(name) ^ 0xFFFFFFFF`). Verified
    exactly against known bone-name hashes: GRP_joint -> 31dca9ab,
    J_root -> 6d0d090b, J_center -> 062f2d6a."""
    if isinstance(name, str):
        name = name.encode("ascii", errors="replace")
    return (zlib.crc32(name) ^ 0xFFFFFFFF) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Error/logging state (module-level, matches the pybind11 Logger singleton)
# ---------------------------------------------------------------------------

_throw_errors = True
_error_list = []


def get_throw_errors():
    return _throw_errors


def set_throw_errors(value):
    global _throw_errors
    _throw_errors = bool(value)


def get_error_list():
    return list(_error_list)


def set_error_list(value):
    global _error_list
    _error_list = list(value)


def _report(msg):
    if _throw_errors:
        raise RuntimeError(msg)
    _error_list.append(msg)


# ---------------------------------------------------------------------------
# Small value types
# ---------------------------------------------------------------------------

class BoneTransform:
    """Confirmed layout (RE'd from binary::BoneTransform's registration):
    quaternion @0 (4 floats), position @0x10 (4 floats), scale @0x20 (4 floats).
    48 bytes total. Only used in-memory here -- not yet read from file bytes
    (bone rest-pose file location not traced this session)."""

    __slots__ = ("quaternion", "position", "scale")

    def __init__(self):
        self.quaternion = (0.0, 0.0, 0.0, 1.0)
        self.position = (0.0, 0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0, 0.0)


class Bone:
    """Confirmed layout: transform (embedded BoneTransform) @0x28, parent
    (shared_ptr<Bone>) @0x58, is_geometry (bool) @0x68. name/name_hash offsets
    within the C++ struct are inferred, not directly traced -- irrelevant here
    since we just store them as plain Python attributes."""

    def __init__(self):
        self.name = ""
        self.name_hash = 0
        self.transform = BoneTransform()
        self.parent = None
        self.is_geometry = False

    @property
    def transform_actual(self):
        """World-space transform, computed by walking the parent chain
        (matches the compiled module's on-the-fly composition in
        sub_1800173B0 -- not stored, always derived)."""
        import mathutils
        q = self.transform.quaternion
        p = self.transform.position
        s = self.transform.scale
        local = (
            mathutils.Matrix.Translation(p[:3])
            @ mathutils.Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4()
            @ mathutils.Matrix.Diagonal((*s[:3], 1.0))
        )
        if self.parent is None:
            return local
        return self.parent.transform_actual @ local


class Skeleton:
    """Real on-disk role/location not fully traced this session -- geom.py
    expects geom.skeleton.bones to be the full bone list (hierarchy comes from
    the companion .nlst file, matched by name_hash)."""

    def __init__(self):
        self.bones = []


class Shader:
    """`.name` is intentionally always blank on import -- confirmed against the
    compiled module (see RE doc's "Shader.name isn't a parser gap" section):
    `Shader` has exactly one Python-bound member (`__repr__`) in the whole
    binary, no `name` getter/setter exists anywhere. The material record's
    808-byte "technique" block does get memmoved into `Material.shaders`
    (14 x 56-byte raw slots) by the compiled parser, but with no exposed
    property to read a name out of it, those bytes are simply unreachable
    from Python through the real module either -- so leaving `.name` blank
    here is a correct match to real behavior, not a gap."""

    def __init__(self):
        self.name = ""


# Static id -> shader-uniform-name table (791 entries), extracted directly from a
# hardcoded dictionary compiled into the .pyd at VA 0x1800932B0-0x18009DFB8 (791 x 56-byte
# records: id:int32@0x00, flag:int16@0x04, type:int32@0x06, reserved:6B@0x0A,
# name_hash:uint64@0x10, name:cstr@0x18). Built once into a global unordered_map at module
# init; every per-material uniform record's `id` field (see ShaderUniform docstring) is
# looked up here to get parameter_name. Confirmed exact against known names used by
# material.py (DiffuseColor=24, Bumpiness=27, OverlayMaskSampler=170, LightPixelProj=303,
# etc.) -- not a guess, extracted byte-for-byte from the compiled module.
_UNIFORM_ID_NAMES = {
    0: 'ViewProj', 1: 'World', 2: 'View', 3: 'ViewInverse',
    4: 'WorldInverse', 5: '_MatrixPalette', 6: 'SystemAlpha', 7: 'ViewportPixelSize',
    8: 'ColorSamplerScale', 9: 'AmbientColor', 10: 'SkyDir', 11: 'bloomLimit',
    12: 'blurOffset', 13: 'ShadowDensity', 14: 'bloomScale', 15: 'ColorCorrectionSampler',
    16: 'BlurredSampler', 17: 'DepthSampler', 18: 'dofParams', 19: 'dofOptions',
    20: 'dofGrayScale', 21: 'SkyColor', 22: 'GroundColor', 23: 'ColorSampler',
    24: 'DiffuseColor', 25: 'DiffuseAlpha', 26: 'NormalSampler', 27: 'Bumpiness',
    28: 'SpecularParams', 29: 'SpecularStrength', 30: 'SpecularPower', 31: 'EnvSampler',
    32: 'ReflectionStrength', 33: 'FresnelMin', 34: 'FresnelExp', 35: 'SurfaceColor',
    36: 'FuzzySpecColor', 37: 'SubColor', 38: 'RollOff', 39: 'VelvetStrength',
    40: 'LightSampler', 41: 'OverlayColorSampler', 42: 'OverlayNormalSampler', 43: 'OverlayBumpiness',
    44: 'OverlayStrength', 45: 'CLUTSampler', 46: 'BackBufferSampler', 47: 'GlassParams',
    48: 'GlassStrength', 49: 'Curvature', 50: 'UpsideDown', 51: 'ParallaxBias',
    52: 'ParallaxBiasX', 53: 'ParallaxStrength', 54: 'ShadowSampler', 55: 'LightViewProj',
    56: 'CameraPosition', 57: 'Time', 58: 'ScrollSpeedSet1', 59: 'ScrollSpeedSet1U',
    60: 'ScrollSpeedSet1V', 61: 'ScrollSpeedSet2', 62: 'ScrollSpeedSet2U', 63: 'ScrollSpeedSet2V',
    64: 'ScrollSpeedSet3', 65: 'ScrollSpeedSet3U', 66: 'ScrollSpeedSet3V', 67: 'OffsetSet1',
    68: 'OffsetSet1U', 69: 'OffsetSet1V', 70: 'OffsetSet2', 71: 'OffsetSet2U',
    72: 'OffsetSet2V', 73: 'DistortionStrength', 74: 'FogParams', 75: 'FogNear',
    76: 'FogFar', 77: 'FogAlpha', 78: 'NearFogColor', 79: 'AlphaTestRef',
    80: 'CoverSampler', 81: 'CoverScale', 82: 'CoverScaleH', 83: 'CoverScaleV',
    84: 'CoverAddition', 85: 'MipBias', 86: 'LightMapPower', 87: 'LightMapStrength',
    88: 'Saturation', 89: 'OffsetSet3', 90: 'OffsetSet3U', 91: 'OffsetSet3V',
    92: 'Fat', 93: 'RotationSet1', 94: 'dummy132', 95: 'dummy133',
    96: 'RotationSet2', 97: 'dummy135', 98: 'dummy136', 99: 'RotationSet3',
    100: 'dummy138', 101: 'dummy139', 102: 'ScaleSet1', 103: 'ScaleSet1U',
    104: 'ScaleSet1V', 105: 'ScaleSet2', 106: 'ScaleSet2U', 107: 'ScaleSet2V',
    108: 'ScaleSet3', 109: 'ScaleSet3U', 110: 'ScaleSet3V', 111: 'CoverOffset',
    112: 'CoverOffsetX', 113: 'CoverOffsetY', 114: 'ZBias', 115: 'EnvsSampler',
    116: 'InnerGrowAValue', 117: 'InnerGrowABase', 118: 'InnerGrowASub', 119: 'InnerGrowARange',
    120: 'GlowACLUTSampler', 121: 'InnerGrowBValue', 122: 'alpha_func', 123: 'alpha_test_enable',
    124: 'blend_func', 125: 'blend_equation', 126: 'blend_enable', 127: 'cull_face',
    128: 'cull_face_enable', 129: 'depth_func', 130: 'depth_mask', 131: 'depth_test_enable',
    132: 'polygon_offset', 133: 'polygon_offset_fill_enable', 134: 'color_mask', 135: 'stencil_func',
    136: 'stencil_mask', 137: 'stencil_op', 138: 'stencil_test_enable', 139: 'scissor_test_enable',
    140: 'scissor', 141: 'alpha_to_coverage_enable', 142: 'wire_frame_enable', 143: 'state_end',
    144: 'InnerGrowBBase', 145: 'InnerGrowBSub', 146: 'InnerGrowBRange', 147: 'GlowBCLUTSampler',
    148: 'LocalIntensity', 149: 'InnerGrowAColor', 150: 'InnerGrowBColor', 151: 'SepiaStrength',
    152: 'ViewOffset', 153: 'MetalicValue', 154: 'RoughnessValue', 155: 'PBRRefrectionRoughness',
    156: 'PBRMaskSampler', 157: 'BloomMaskSampler', 158: 'BloomStrength', 159: 'NormalShadowBias',
    160: 'OverlayColorSampler2', 161: 'OverlayColorSampler3', 162: 'OverlayColorSampler4', 163: 'OverlayStrength2',
    164: 'OverlayStrength3', 165: 'OverlayStrength4', 166: 'OverlayNormalSampler2', 167: 'OverlayNormalSampler3',
    168: 'OverlayNormalSampler4', 169: 'RgbaMaskSampler', 170: 'OverlayMaskSampler', 171: 'OverlayMaskSampler2',
    172: 'OverlayMaskSampler3', 173: 'OverlayMaskSampler4', 174: 'OverlayBumpiness2', 175: 'OverlayBumpiness3',
    176: 'OverlayBumpiness4', 177: 'SpecularColorRgb', 178: 'NormaColorlSampler', 179: 'NormalEdgeThickness',
    180: 'NormalEdgeBound', 181: 'ShadowBias', 182: 'OpasityMaskSampler', 183: 'OpasityFade',
    184: 'OpasityBorder', 185: 'OpasityBorderColor', 186: 'ShadowDir', 187: 'ShadowTexInvSize',
    188: 'ProjPlane', 189: 'ssao_radiusParams', 190: 'ssao_biasParams', 191: 'ssao_screenParams',
    192: 'ssao_focalParams', 193: 'ssao_uvToViewParams', 194: 'ssao_optionParams', 195: 'RandomSampler',
    196: 'SSAOSampler', 197: 'ssaoBlurParams', 198: 'TargetTime', 199: 'TargetAlpha',
    200: 'AlphaStayTime', 201: 'BaseAlpha', 202: 'PS_Time', 203: 'TransitionMaskSampler',
    204: 'TransitionValue', 205: 'TransitionBorderColor', 206: 'TransitionBorderValue', 207: 'ProjTexViewProj',
    208: 'ProjTexSampler', 209: 'ScreenLightPos', 210: 'GodRayDensity', 211: 'GodRayNum_Sumple',
    212: 'GodRayWeight', 213: 'GodRayDecay', 214: 'GodRayExposure', 215: 'CommonDepthSumpler',
    216: 'SoftParticleDistLenght', 217: 'IsHeatMap', 218: 'HeatMapColorB', 219: 'HeatMapColorG',
    220: 'EdgeColorSampler', 221: 'FogFovParam', 222: 'ColorYSampler', 223: 'ColorUVSampler',
    224: 'ColorUSampler', 225: 'ColorVSampler', 226: 'ProjTexStrength', 227: 'ProjTexScale',
    228: 'Projection', 229: 'CameraProjPos', 230: 'EdgeFlag', 231: 'DepthEdgeBound',
    232: 'DepthEdgeRange', 233: 'vColorEdgeBound', 234: 'vColorEdgeRange', 235: 'set1RandomMinMax',
    236: 'set2RandomMinMax', 237: 'set3RandomMinMax', 238: 'uvRot1RandomMinMax', 239: 'uvRot2RandomMinMax',
    240: 'uvRot3RandomMinMax', 241: 'uvoffSet1RandomMinMax', 242: 'uvoffSet2RandomMinMax', 243: 'uvoffSet3RandomMinMax',
    244: 'uvScal1RandomMinMax', 245: 'uvScal2RandomMinMax', 246: 'uvScal3RandomMinMax', 247: 'vsRandom',
    248: 'RasterScal', 249: 'RasterOffSetY', 250: 'AnisotropyHighlighBase', 251: 'AnisotropyHighlighStrength',
    252: 'AnisotropyHighlightFresnel', 253: 'AnisotropyHighlightColorRgb', 254: 'ReverseFresnelBase', 255: 'ReverseFresnelSub',
    256: 'ReverseFresnelRange', 257: 'RimLightColor', 258: 'RimLightBase', 259: 'RimLightSub',
    260: 'RimLightRange', 261: 'SilhouetteColorRgb', 262: 'HairHighLightVBase', 263: 'HairHighLightVRange',
    264: 'HairHighLightVOffset', 265: 'HairHighLightColorRgb', 266: 'LightWeightSampler', 267: 'IrradianceMapStrength',
    268: 'IrradianceMapSampler', 269: 'FogParams2', 270: 'MiddleFogColor', 271: 'FarFogColor',
    272: 'NearHeightFogYUpColor', 273: 'MiddleHeightFogYUpColor', 274: 'FarHeightFogYUpColor', 275: 'HeightFogYUpParams',
    276: 'HeightFogYUpParams2', 277: 'NearHeightFogYDownColor', 278: 'MiddleHeightFogYDownColor', 279: 'FarHeightFogYDownColor',
    280: 'HeightFogYDownParams', 281: 'HeightFogYDownParams2', 282: 'EdgeParamSampler', 283: 'EdgeMaterialParams',
    284: 'OverlayDistortionSampler', 285: 'OverlayDistortionStrength', 286: 'OverlayDistortionSampler2', 287: 'OverlayDistortionStrength2',
    288: 'OverlayDistortionSampler3', 289: 'OverlayDistortionStrength3', 290: 'OverlayDistortionSampler4', 291: 'OverlayDistortionStrength4',
    292: 'FogBlend', 293: 'MetalBrightParams', 294: 'OlParallaxBiasX', 295: 'OlParallaxStrength',
    296: 'OverlayPBRMaskSampler', 297: 'OverlayPBRMaskSampler2', 298: 'OverlayPBRMaskSampler3', 299: 'OverlayPBRMaskSampler4',
    300: 'EmissiveColor', 301: 'EmissiveIntensity', 302: 'EmissiveSampler', 303: 'LightPixelProj',
    304: 'ScratchMaskSampler', 305: 'ScratchBorder', 306: 'VerticalWaveSpeed', 307: 'VerticalWaveScal',
    308: 'HorizontalWaveSpeed', 309: 'HorizontalWaveScal', 310: 'Normal2Sampler', 311: 'Normal2Strength',
    312: 'MulDiffuseAlphaSampler', 313: 'VertexColorDegamma', 314: 'WaterRefrDistortionAmount', 315: 'WaterWaveScale',
    316: 'WaterMaxColor', 317: 'WaterMinColor', 318: 'WaterSunColor', 319: 'WaterSunIntensity',
    320: 'WaterSunCosAngle', 321: 'WaterSunExponent', 322: 'WaterRefraction', 323: 'WaterSunRefraction',
    324: 'WaterCoverCosAngle', 325: 'WaterCoverExponent', 326: 'MorphWeights0', 327: 'MorphWeights1',
    328: 'MorphWeights2', 329: 'MorphWeights3', 330: 'WireThickness', 331: 'WireGridNum',
    332: 'ParallaxHeigthScal', 333: 'ParallaxRaySamples', 334: 'WindLocalDirection', 335: 'WindLocalPower',
    336: 'WindLocalRightVector', 337: 'ReflectionOverRayBlend', 338: 'AltSpecularSampler', 339: 'AltSpecularPower',
    340: 'AltSpecularStrength', 341: 'SubSurfaceValue', 342: 'SubSurfaceStrength', 343: 'SubSurfaceDistortion',
    344: 'SubSurfacePower', 345: 'SubSurfaceScale', 346: 'SubSurfaceAmbient', 347: 'SubSurfaceCutOff',
    348: 'SubSurfaceMaskSampler', 349: 'InnerEmissivePower', 350: 'InnerEmissiveStrength', 351: 'InnerEmissiveColor',
    352: 'DistortionNormalSampler', 353: 'DistortionNormalStrength', 354: 'WaterSunNear', 355: 'WaterSunFar',
    356: 'WaterDistortionSampler', 357: 'WaterNormalSampler', 358: 'WaterExtinction', 359: 'WaterFogExponent',
    360: 'WaterNormalDistortion', 361: 'WaterWaveStrength', 362: 'WaterWaveAmplitudeX', 363: 'WaterWaveAmplitudeY',
    364: 'WaterWaveAmplitudeZ', 365: 'WaterWaveAmplitudeW', 366: 'WaterWaveFrequencyX', 367: 'WaterWaveFrequencyY',
    368: 'WaterWaveFrequencyZ', 369: 'WaterWaveFrequencyW', 370: 'WaterWaveSteepnessX', 371: 'WaterWaveSteepnessY',
    372: 'WaterWaveSteepnessZ', 373: 'WaterWaveSteepnessW', 374: 'WaterWaveSpeedX', 375: 'WaterWaveSpeedY',
    376: 'WaterWaveSpeedZ', 377: 'WaterWaveSpeedW', 378: 'WaterWaveDirection1X', 379: 'WaterWaveDirection1Y',
    380: 'WaterWaveDirection1Z', 381: 'WaterWaveDirection1W', 382: 'WaterWaveDirection2X', 383: 'WaterWaveDirection2Y',
    384: 'WaterWaveDirection2Z', 385: 'WaterWaveDirection2W', 386: 'WaterSurfaceUVStrength', 387: 'WaterSurfaceUVSpeed',
    388: 'WaterVarianceSigma', 389: 'WaterSpecularStrength', 390: 'WaterRefractionDepthDistance', 391: 'WaterRefractionDepthBias',
    392: 'CausticsColorSampler', 393: 'CausticsDistortionSampler', 394: 'CausticsMaskSampler', 395: 'CausticsDistortionStrength',
    396: 'CausticsColorStrength', 397: 'CausticsEmissiveColor', 398: 'CausticsEmissiveIntensity', 399: 'ColorDegammaValue',
    400: 'VisiblityFlag', 401: 'ShadowReflectionFactor', 402: 'BackGroundFillPaintFactor', 403: 'BackGroundFillPostProcess',
    404: 'UVPlanarColorScale', 405: 'UVPlanarColorOffset', 406: 'UVPlanarColorDir', 407: 'UVPlanarNormalScale',
    408: 'UVPlanarNormalOffset', 409: 'UVPlanarNormalDir', 410: 'UVPlanarNormal2Scale', 411: 'UVPlanarNormal2Offset',
    412: 'UVPlanarNormal2Dir', 413: 'GeomSpecAAVariance', 414: 'GeomSpecAAThreashold', 415: 'TargetDistANearRange',
    416: 'TargetDistAFarRange', 417: 'TargetDistNearAlpha', 418: 'TargetDistFarAlpha', 419: 'TargetDistAExponent',
    420: 'TargetDistAPos', 421: 'PointLightMaskSampler', 422: 'UVPivotPlanarScale', 423: 'AmbientModelIntensity',
    424: 'UnderWaterFluctuationPower', 425: 'UnderWaterFluctuationSpeed', 426: 'UnderWaterFluctuationFrequency', 427: 'ColorTextureMipBias',
    428: 'ReverseFresnelAlphaSampler', 429: 'DirectColorFilterColor', 430: 'SSAOCompositeParams', 500: 'SSAOViewProj',
    501: 'ProjCoefficient', 502: 'ScreenVertexSize', 503: 'ColorTextureSize', 504: 'ToneMapExposure',
    505: 'ToneMapParams1', 506: 'ToneMapParams2', 507: 'ScreenSize', 508: 'ColorHistorySampler',
    509: 'WindDirection', 510: 'WindPower', 511: 'DeferredPBRSampler', 512: 'DeferredAddColorSampler',
    513: 'DeferredEmissiveColorSampler', 514: 'SSAODepthParams', 515: 'PSCameraPosition', 516: 'ShadowEdgeLimit',
    517: 'ShadowRecvParam', 518: 'SelfShadowParam', 519: 'LevelCtrlParamsA', 520: 'LevelCtrlParamsB',
    521: 'DiffusionSampler', 522: 'CompareSampler', 523: 'DissolveMaskSampler', 524: 'DissolveBorder',
    525: 'DissolveBorderColor', 526: 'DissolvePos', 527: 'DissolveEndPos', 528: 'DissolveStartPos',
    529: 'DissolveDir', 530: 'DissolveBorderColorOnly', 531: 'DissolveUVIsPos', 532: 'DissolveUV_UDir',
    533: 'DissolveUV_VDir', 534: 'DissolveNCUVOffset', 535: 'WorldTransform', 536: 'InvWorldTransform',
    537: 'WindRightVector', 538: 'CascadeShadowSwitchDepth', 600: 'CascadeShadowVisualizeFlag', 601: 'CascadeShadowViewProjections',
    602: 'CascadeShadowParams', 603: 'CascadeShadowSampler', 604: 'CascadeShadowMaterialSampler', 605: 'CascadeShadowCharaSampler',
    606: 'CascadeShadowCharaViewProj', 607: 'PregeneratedShadowViewProj', 608: 'PregeneratedShadowSampler', 609: 'PregeneratedShadowParams1',
    610: 'PregeneratedShadowParams2', 611: 'ShadowMaterialParams', 612: 'DeferredPostShadowParam', 613: 'LensFlarePosition',
    614: 'LensFlareViewProj', 615: 'ShadowCharaDensity', 616: 'ShadowCLUTDensity', 617: 'ShadowCLUTDensity2',
    618: 'OutputColorSampler0', 619: 'DispatchThreadParams', 620: 'LinearDepthParams', 621: 'SSFogParam',
    622: 'SSFogParam2', 623: 'wCameraPosition', 624: 'wCameraUpVector', 625: 'wCameraFrontVector',
    626: 'wCameraRightVector', 627: 'wCameraView', 628: 'wCameraTView', 629: 'wCameraProj',
    630: 'wCameraViewProj', 631: 'wCameraProjJitter', 632: 'UCameraPosition', 633: 'UCameraUpVector',
    634: 'UCameraFrontVector', 635: 'UCameraRightVector', 636: 'UCameraProjJitter', 637: 'PlaneFogDir',
    638: 'ScanEffectMask', 639: 'PostVSTime', 640: 'PostPSTime', 641: 'ScanEffectParam',
    642: 'ZoomBlurParam', 643: 'ZoomBlurParam2', 644: 'ColorBalanceColor', 645: 'bloomWidth',
    646: 'Bloom2BlurParams', 647: 'Bloom2GaussianPdfParams', 648: 'Bloom2CompositeParam', 649: 'Bloom2CompositeParam2',
    650: 'Bloom2Sampler1', 651: 'Bloom2Sampler2', 652: 'Bloom2Sampler3', 653: 'Bloom2Sampler4',
    654: 'Bloom2Sampler5', 655: 'dummy656', 656: 'dummy657', 657: 'CommonLinearDepthSampler',
    658: 'Glare2StarOffsets', 659: 'CastLightProbeFactor', 660: 'LightProbeCoeffs', 661: 'LightProbeParams',
    662: 'VignetteDistance', 663: 'VignetteExponent', 664: 'ColorHSLParams', 665: 'ColorShadowRGB',
    666: 'ColorMidToneGamma', 667: 'ColorHighLightShift', 668: 'ColorBrightContrastParams', 669: 'ColorContrastSampler',
    670: 'ColorBrightnessSampler', 671: 'IndirectSpecularStrength', 672: 'PrevViewProj', 673: 'VelocitySampler',
    674: 'PixelMotionBlurParams', 675: 'ShadowHatchSampler', 676: 'ShadowHatchParams', 677: 'PointLightBlockSampler',
    678: 'PointLightBlockParams', 679: 'CommonCLUTSampler', 680: 'DirLampInfo', 681: 'DirLampParams',
    682: 'SpecDirLampInfo', 683: 'SpecDirLampParams', 684: 'PointLampInfo', 685: 'PointLampParams',
    686: 'PointLampForward', 687: 'DarkPointLampInfo', 688: 'DarkPointLampParams', 689: 'FxPointLampInfo',
    690: 'FxPointLampParams', 691: 'MinusPointLampInfo', 692: 'MinusPointLampParams', 693: 'FallOffPointLampInfo',
    694: 'FallOffPointLampParams', 695: 'SpotLampInfo', 696: 'SpotLampParams', 697: 'SpotLampForward',
    698: 'SpotLampShadowParams', 699: 'SpotLampShadowSampler', 700: 'AreaLampInfo', 701: 'AreaLampParams',
    702: 'RimLightDirection', 703: 'RimLightDirectionColor', 704: 'SSReflectionParams', 705: 'SSReflectionParams2',
    706: 'SSReflectionParams3', 707: 'SSReflectionParams4', 708: 'SSReflectionSampler', 709: 'SSReflectionBlurSampler',
    710: 'SSReflectionViewProj', 711: 'SSReflectionCompositonParams', 712: 'SSReflectionCameraPos', 713: 'SSReflectionFrontVector',
    714: 'SSReflectionPlaneParams', 715: 'PlanarReflectionAlphaParams', 716: 'PlanarReflectionAlphaParams2', 717: 'PlanarReflectionAlphaParams3',
    718: 'VolumetricShadowSampler', 719: 'VolumetricLightPos', 720: 'VolumetricLightDir', 721: 'VolumetricLightColor',
    722: 'VolumetricLightDepthVec', 723: 'VolumetricLightParamsA', 724: 'VolumetricLightParamsB', 725: 'VolumetricLightParamsC',
    726: 'VolumetricLightParamsD', 727: 'VolumetricLightMieG', 728: 'VolumetricLightNoiseVelocity', 729: 'VolumetricLightShadowViewProj',
    730: 'VolumetricLightShadowParams', 731: 'NoiseTexture3D', 732: 'SkySunDirection', 733: 'SkySphereParams1',
    734: 'SkySphereParams2', 735: 'SkyFogInternalParams1', 736: 'SkyInScatterTexture3D', 737: 'SkyTransmittanceTexture',
    738: 'IsMipMapDebug', 739: 'SpotProjParams', 740: 'PSViewFront', 741: 'Depth2Sampler',
    742: 'Depth3Sampler', 743: 'TexProjectionPos', 744: 'BoxProjectionSize', 745: 'TexProjectionRightVec',
    746: 'TexProjectionUpVec', 747: 'TexProjectionFrontVec', 748: 'TexProjectionUVCrop', 749: 'TexProjectionOuterColor',
    750: 'BoxProjectionBackColor', 751: 'BoxProjectionFrontColor', 752: 'BoxProjectionLeftColor', 753: 'BoxProjectionRightColor',
    754: 'BoxProjectionTopColor', 755: 'BoxProjectionBottomColor', 756: 'ShadowCharaParams', 757: 'ShadowCharaParams2',
    758: 'AltSpecularLightDirection', 759: 'AltSpecularIntensity', 760: 'SubSurfaceDistance', 761: 'SubSurfaceWorldPos',
    762: 'FilterParam1', 763: 'FilterParam2', 764: 'FilterParam3', 765: 'FilterParam4',
    766: 'UserCustomSampler1', 767: 'UserCustomSampler2', 768: 'UserCustomSampler3', 769: 'UserCustomSampler4',
    770: 'UserCustomParam1', 771: 'UserCustomParam2', 772: 'UserCustomParam3', 773: 'UserCustomParam4',
    774: 'SSAOCalcParams', 775: 'ZPrePassSampler', 776: 'ZPrePassSampler2', 777: 'FieldAttrTexture',
    778: 'OverrideMaterialParams1', 779: 'OverrideMaterialParams2', 780: 'DebugVisualizeParams', 781: 'IsTransparentPass',
    782: 'PlanarReflectionAttr', 783: 'PlnReflectionEBloomThreashold', 784: 'MapDeferredAttr', 785: 'SHToonParams',
    786: 'TemporalAAParams0', 787: 'TemporalAAParams1', 788: 'TemporalAAParams2', 789: 'TemporalAAParams3',
    790: 'TemporalAAParams4', 791: 'TemporalAAParams5', 792: 'TemporalAAParams6', 793: 'TemporalAAHistoryDepthSampler',
    794: 'TemporalAAPrevViewProj', 795: 'TemporalAADepthViewProj', 796: 'v3CameraPos', 797: 'v3InvWavelength',
    798: 'g', 799: 'fHdrExposure', 800: 'WorldShadowSlopeScaledBias', 801: 'WorldShadowSideBiasScale',
    802: 'ShadowCosAngleThreashold', 803: 'WaterDirLampInfo', 804: 'WaterDirLampParams', 805: 'TexProjectionParams1',
    806: 'TexProjectionParams2', 807: 'MRTParamSampler', 808: 'CubeMap1', 809: 'CubeMap2',
    810: 'fBlendFactor', 811: 'VSColorTextureSize', 812: 'ViewProjNoAA', 813: 'TemporalAADisableParams',
    814: 'HeightFogYUpOffset', 815: 'HeightFogYDownOffset', 816: 'HazmaFogParams', 817: 'HazmaFogParams2',
    818: 'NearHazmaFogColor', 819: 'MiddleHazmaFogColor', 820: 'FarHazmaFogColor', 821: 'InverseWorld',
    822: 'VSImposterParams1', 823: 'VSImposterOffset', 824: 'VSImposterMatrix1', 825: 'VSImposterMatrix2',
    826: 'PSImposterParams1', 827: 'CorrectRenderViewParams', 828: 'CorrectHistoryViewParams', 829: 'CorrectTexCoordViewParams',
    830: 'CorrectViewResolutionParams', 831: 'DepthCorrectViewParams', 832: 'UIBGBlurSampler', 833: 'UIBGBlurTextureSize',
    834: 'UIBGBlurInfo', 835: 'FogNoise3DVelocity', 836: 'FogNoise3DVelocity2', 837: 'FogNoise3DParam',
    838: 'FogCharaFactor', 839: 'SceneEmissiveIntensity', 840: 'SSAOFogParams1', 841: 'SSAOFogParams2',
    842: 'SSAOFogParams3', 843: 'ShapeCutoutSphereParam1', 844: 'SphereCutoutParamArray', 845: 'SphereCutoutTexture',
    846: 'MainCamViewMatrix', 847: 'MainCameraPosition', 848: 'PointLightShadowParams', 849: 'CharaPointShadowParams',
    850: 'CharaPointShadowInfo', 851: 'CharaDirShadowParams', 852: 'CharaDirShadowInfo', 853: 'CharaDirShadowSampler',
    854: 'GravelColorTexture', 855: 'GravelNormalTexture', 856: 'GravelNoiseTexture3D', 857: 'BackGroundPaintTexture',
    858: 'BackGroundPaintViewSize', 859: 'VolumeFogScatterParams', 860: 'ShadowTextureCubeArray', 861: 'ShadowTextureCAParams',
    862: 'GodRayLightShaftParams', 863: 'GodRayLightColorParams', 864: 'GodRayLightTexture', 865: 'MainColorCorrectViewParams',
    866: 'UVPlanarColorDirX', 867: 'UVPlanarColorDirY', 868: 'UVPlanarNormalDirX', 869: 'UVPlanarNormalDirY',
    870: 'UVPlanarNormal2DirX', 871: 'UVPlanarNormal2DirY', 872: 'VSDepthTexture', 873: 'VSGlareDepthParams',
    874: 'ReflectionIndexTexture', 875: 'ReflectionProbeTexture', 876: 'ReflectionProbeParams', 877: 'UserArrayParams',
    878: 'PhantomGrowValue', 879: 'PhantomGrowValue2', 880: 'PhantomGrowValue3', 881: 'PhantomGrowNormal',
    882: 'PhantomGrowColor', 883: 'PhantomGrowColor2', 884: 'PhantomGrowUVOffset', 885: 'PhantomGrowTexture',
    886: 'UVPivotPlanarPos', 887: 'GateStealthWarpTexture', 888: 'GateStealthWarpParams', 889: 'CSVerticesByteAddress',
    890: 'CSVerticesRWByteAddress', 891: 'CSJointMatrixPalette', 892: 'SilhouetteDissolveParam', 893: 'FrameMotionBlurParams',
    894: 'FilterType', 895: 'Exposure', 896: 'Gamma', 897: 'Contrast',
    898: 'IsScaleWipe', 899: 'MeltPSTime', 900: 'CharaSpecularStrength', 901: 'OutsideEdgeParams',
    902: 'OutsideEdgeParams2', 903: 'EdgeColor', 904: 'EdgeDepthParams', 905: 'CharaOcclusionCopyTexture',
    906: 'CharaOcclusionShadowTexture', 907: 'CharaOcclusionParams', 908: 'EnvMapIntensity', 909: 'TiledLightingParams',
    910: 'TiledLightingIndexTexture', 911: 'PointLightTileInfo', 912: 'AreaLightTileInfo', 913: 'SpotLightTileInfo',
    914: 'PointLightTileParams', 915: 'SpotLightTileParams', 916: 'VertexWorldToViewPos', 917: 'PixelWorldToViewPos',
    918: 'UnderWaterFluctuationShake', 919: 'UVScaleYUV', 920: '',
}

class ShaderUniform:
    """Real on-disk layout (see Geom docstring / RE doc "uniform-binding record"
    section): 32-byte record, +0x10 u16 id (-> _UNIFORM_ID_NAMES), +0x12 u16 sub
    (0 = texture, 1-4 = that many packed float32s, 100 = a render-state int32
    pair). `parameter_name` is resolved from `id` via the static 791-entry table
    extracted from the compiled module (dsts_formats_shader_uniform_ids.json in
    the RE doc folder). `unknown_0xC` is only meaningful for texture uniforms --
    it's the record's `extra` field (offset +0x0C of the value payload), whose
    exact meaning wasn't identified but which round-trips a real, varying value
    (not needed to resolve the texture name itself, which comes from `reloff` +
    the header's `extra_base` alone)."""

    def __init__(self):
        self.parameter_name = ""
        self.uniform_type = "float"  # "texture" or "float"
        self.value = None
        self.unknown_0xC = 0


class ShaderSetting:
    """Always empty on import (Material.settings stays `[]`) -- like Shader.name,
    `ShaderSetting` has no field-level Python bindings anywhere in the compiled
    module (only `__repr__`/`__init__`; exhaustive RTTI xref search found 31
    other hits, all generic pybind11 vector/caster plumbing, never a
    `parameter_name`/`value`/`unknown_0x12`-style getter). Also confirmed no
    standalone `.material` file format exists (zero hits for that extension
    string anywhere in the binary), so this isn't a separate file this parser
    is simply missing -- real on-disk source (if any) not identified; left
    unpopulated as the best-supported behavior rather than guessed at."""

    def __init__(self):
        self.parameter_name = ""
        self.value = b""
        self.unknown_0x12 = 0


class MeshAttribute:
    """binary::MeshAttribute, confirmed 24-byte struct: count (uint16) @2,
    offset (uint16) @6. atype/dtype are the Python-friendly string forms
    (VertexAttributeType enum name, e.g. "position"/"uv1"/"weight"; dtype e.g.
    "float"/"uByte") used by mesh.py's extract_attribute()."""

    def __init__(self):
        self.count = 0
        self.offset = 0
        self.atype = ""
        self.dtype = "float"


class Material:
    """Confirmed offsets (in the compiled module's in-memory C++ layout --
    irrelevant to this pure-Python version except as provenance): uniforms
    vector<ShaderUniform> @0x348, settings vector<ShaderSetting> @0x360,
    sizeof(Material)=904 bytes. shaders is a fixed-size sequence of 14 Shader
    refs. unknown_0x314/0x318/0x31C/0x324/0x326 are real fields whose meaning
    wasn't identified -- names preserved for round-trip compatibility with the
    addon's material.py, which stores/restores them as custom properties."""

    def __init__(self):
        self.name = ""
        self.name_hash = 0
        self.shaders = [Shader() for _ in range(14)]
        self.uniforms = []
        self.settings = []
        self.unknown_0x314 = 0
        self.unknown_0x318 = 0
        self.unknown_0x31C = 0
        self.unknown_0x324 = 0
        self.unknown_0x326 = 0


def _tristrip_to_trilist(strip):
    """Converts a triangle strip (with degenerate-triangle joins, the format
    confirmed empirically in chr090.geom -- consecutive repeated indices mark
    strip restarts) into a flat triangle-list index array, alternating
    winding order every other triangle as required by the strip convention."""
    out = []
    for i in range(len(strip) - 2):
        a, b, c = strip[i], strip[i + 1], strip[i + 2]
        if a == b or b == c or a == c:
            continue
        if i % 2 == 0:
            out.extend((a, b, c))
        else:
            out.extend((a, c, b))
    return out


class Mesh:
    """Geom.meshes element. mesh_attributes/pack_vertices()/get_indices() are
    the interface mesh.py actually drives."""

    def __init__(self):
        self.name = ""
        self.name_hash = 0
        self.material = None
        self.mesh_attributes = []
        self.bytes_per_vertex = 0
        self.matrix_palette = []
        self.flag_0 = True
        self.flag_1 = False
        self.flag_2 = False
        self.flag_3 = False
        self.flag_4 = False
        self.flag_5 = False
        self.flag_6 = False
        self.flag_7 = False
        self._packed_vertices = b""
        self._indices = []

    def pack_vertices(self):
        """Returns (mesh_attributes, bytes_per_vertex, packed_bytes) --
        matches the compiled module's Mesh.pack_vertices(). The file's raw
        per-mesh vertex buffer is already in this interleaved format, so this
        is close to a direct pass-through of what was read from disk."""
        return self.mesh_attributes, self.bytes_per_vertex, self._packed_vertices

    def get_indices(self):
        """Flat triangle-list indices (already strip-expanded at load time)."""
        return list(self._indices)

    def set_vertex_count(self, count):
        raise NotImplementedError("dsts_formats.py: export not implemented")

    def set_position(self, positions):
        raise NotImplementedError("dsts_formats.py: export not implemented")


# ---------------------------------------------------------------------------
# Binary reader helpers
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, data):
        self.data = data

    def u16(self, off):
        return struct.unpack_from("<H", self.data, off)[0]

    def u32(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def i64(self, off):
        return struct.unpack_from("<q", self.data, off)[0]

    def u64(self, off):
        return struct.unpack_from("<Q", self.data, off)[0]

    def cstr(self, off):
        end = self.data.index(b"\x00", off)
        return self.data[off:end].decode("ascii", errors="replace")


def _apply_nlst(bones, nlst_text):
    """Fills in Bone.parent / Bone.is_geometry from a companion .nlst text
    file (`name, hash, parent_hash, type` per line, hex hashes, `type` is
    "joint" or "geometry", `parent_hash` is "null" for the root). Matched by
    name_hash rather than list position for robustness."""
    by_hash = {b.name_hash: b for b in bones}
    for line in nlst_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        _name, hash_hex, parent_hex, btype = parts[:4]
        try:
            h = int(hash_hex, 16)
        except ValueError:
            continue
        bone = by_hash.get(h)
        if bone is None:
            continue
        bone.is_geometry = btype.lower() == "geometry"
        if parent_hex.lower() != "null":
            try:
                bone.parent = by_hash.get(int(parent_hex, 16))
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Geom
# ---------------------------------------------------------------------------

class Geom:
    """Confirmed field offsets (compiled module's in-memory layout, kept here
    only as provenance comments -- this pure-Python version stores plain
    attributes instead): unknown_0x10/0x30/0x34 (u32 each, copied straight
    from the matching file-header offsets), meshes (vector<Mesh>) @0x440,
    materials (vector<shared_ptr<Material>>) @0x458.

    File-format layout used by from_bytes/from_file (RE'd + empirically
    validated against a real chr090.geom -- see RE/dsts_formats_pyd_re.md):

        header (168 bytes @ base_offset):
          +0x00 u32  version (must == 316)
          +0x04 u16  material_count
          +0x10 u32  unknown_0x10
          +0x30 u32  unknown_0x30
          +0x34 u32  unknown_0x34
          +0x48 i64  materials_array_offset (rel. to base)
          +0x78 i64  extra_base (shared by both name lists + material names)
          +0x90 i64  toc_offset (rel. to base)
          +0x98 u32  skeleton_table_offset (rel. to base -- was misidentified as a
                     "uniform definitions count" earlier; its own sub-header's count
                     field reads back as exactly the bone count, not any uniform count)

        skeleton table (@ base+skeleton_table_offset):
          +0x10 u16  bone_count
          +0x14 u32  version (== 2 in observed files)
          +0x30      NOT bone transforms -- a by-name-hash lookup sub-table (small
                     incrementing uint16 pairs, looks like a hash-bucket structure
                     for by-hash bone search) whose SIZE SCALES WITH bone_count in
                     4-bone buckets, growing by 16 bytes per bucket. Never decoded
                     internally, not needed since we resolve bones by name via the
                     companion .nlst instead. An earlier version of this reader
                     assumed a fixed +0xF0 gap to BoneTransform[0] here -- that was
                     only ever coincidentally close for the specific bone counts
                     spot-checked at the time; for most bone counts it silently read
                     one field-group off (each bone's *position* landing in the
                     *quaternion* slot and so on), and the misread bytes still
                     happened to pass the degenerate-quaternion guard below, so it
                     never crashed or looked obviously wrong.
          +0x50 + 0x10*((bone_count-1)//4)   BoneTransform[bone_count] REAL start,
                     48 bytes each (quaternion/position/scale, x,y,z,w order, no
                     compression -- confirmed by round-tripping known values through
                     the real compiled dsts_formats.cp311-win_amd64.pyd under Wine).
                     Formula ground-truthed two ways: (1) constructing fresh Geom
                     objects with bone counts 2..65 via the compiled module's own
                     set_*()/to_file() API and measuring exactly where each
                     BoneTransform array landed (18 clean data points); (2) reading
                     a real chr050.geom's geom.skeleton.bones[i].transform directly
                     via the compiled module's own Python getters (bypassing all
                     offset math) and confirming every one of its 25 bones' data
                     sits at exactly this formula's offset -- unit-quaternion
                     identity rotations with sane, bilaterally-symmetric positions,
                     zero exceptions. Bone i's transform corresponds to
                     bone_names[i] (index-aligned with the bone name list). Parent-
                     index/is_geometry encoding elsewhere in this table was not
                     cracked; instead, if a companion .nlst file is found next to
                     the input .geom (same basename, extracted by the game alongside
                     every model), it's used for parent/is_geometry -- see
                     _apply_nlst().

        TOC (24 bytes @ base+toc_offset):
          +0x00 u32  count_a  (bone-name count)
          +0x04 u32  count_b  (material-name count)
          +0x08 i64  ptr_a    (rel. to base -- bone name offset-table)
          +0x10 i64  ptr_b    (rel. to base -- material name offset-table)

        each name list: count x int64 (rel. offsets), string at
          base + extra_base + rel_offset, NUL-terminated.

        materials array: material_count x 128-byte records @ base+materials_array_offset.
        Per-record fields (offsets relative to record start; all empirically
        validated against real chr090.geom bytes -- see RE/dsts_formats_pyd_re.md):
          +0x00 i64  vertex_data_offset (rel. to base)
          +0x08 i64  index_data_offset (rel. to base)
          +0x10 i64  uniform_ids_offset (rel. to base; int32[uniform_count])
          +0x20 i64  mesh_attrs_offset (rel. to base; 8-byte descriptor x attr_count)
          +0x28 u16  uniform_count
          +0x2A u16  attr_count
          +0x2C u16  bytes_per_vertex
          +0x31 u8   8 packed bitflags (mesh flag_0..flag_7)
          +0x34 u32  material_name_hash
          +0x38 i64  material_name_offset (rel. to base+extra_base)
          +0x44 u32  vertex_count
          +0x48 u32  index_count (uint16 indices; empirically a TRIANGLE STRIP
                     with degenerate joins, not a plain list -- expanded to a
                     flat triangle list at load time)

        Per-channel descriptor (8 bytes, at mesh_attrs_offset, attr_count of
        them): {ordinal: u16, count: u16, format: u16, offset: u16}. `offset`
        is the byte offset of this channel within one raw interleaved vertex
        (confirmed: offsets are strictly increasing and span [0, bytes_per_
        vertex) exactly). `ordinal` 1 is empirically POSITION (3x float32 at
        offset 0, validated by decoding real vertex data into a sane bounded
        3D point cloud) -- NOT the same numbering as the separately-confirmed
        VertexAttributeType enum (position=0x18 etc: that enum's axis doesn't
        line up with these small per-record ordinals 1-11, still unreconciled).
        `count`/`format` -> component count / element format are not fully
        decoded for channels beyond position yet.
    """

    def __init__(self):
        self.unknown_0x10 = 0
        self.unknown_0x30 = 0
        self.unknown_0x34 = 0
        self.clut = b""
        self.skeleton = Skeleton()
        self.materials = []
        self.meshes = []

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        geom = cls.from_bytes(data)

        nlst_path = os.path.splitext(path)[0] + ".nlst"
        if os.path.isfile(nlst_path):
            with open(nlst_path, "r", encoding="ascii", errors="replace") as f:
                _apply_nlst(geom.skeleton.bones, f.read())

        return geom

    @classmethod
    def from_bytes(cls, data, base_offset=0):
        r = _Reader(data)
        geom = cls()

        version = r.u32(base_offset + 0x00)
        if version != 316:
            _report(f"Geom version is not 316 (got {version})")
            return geom

        geom.unknown_0x10 = r.u32(base_offset + 0x10)
        geom.unknown_0x30 = r.u32(base_offset + 0x30)
        geom.unknown_0x34 = r.u32(base_offset + 0x34)

        material_count = r.u16(base_offset + 0x04)
        materials_array_offset = r.i64(base_offset + 0x48)
        extra_base = r.i64(base_offset + 0x78)
        toc_offset = r.i64(base_offset + 0x90)

        toc_pos = base_offset + toc_offset
        count_a = r.u32(toc_pos + 0x00)
        count_b = r.u32(toc_pos + 0x04)
        ptr_a = r.i64(toc_pos + 0x08)
        ptr_b = r.i64(toc_pos + 0x10)

        bone_names = cls._read_name_list(r, base_offset, ptr_a, count_a, extra_base)
        material_names = cls._read_name_list(r, base_offset, ptr_b, count_b, extra_base)

        if material_count != count_b:
            _report(
                f"material_count header field ({material_count}) does not match "
                f"material name list count ({count_b})"
            )

        skeleton_table_offset = r.u32(base_offset + 0x98)
        geom.skeleton.bones = cls._read_skeleton_bones(
            r, base_offset, skeleton_table_offset, bone_names
        )

        geom.materials = []
        geom.meshes = []
        record_base = base_offset + materials_array_offset
        for i in range(material_count):
            rec = record_base + i * 128
            mat, mesh = cls._read_material_and_mesh_record(r, base_offset, rec, extra_base)
            if mat.name == "" and i < len(material_names):
                mat.name = material_names[i]
            mesh.name = mat.name
            mesh.name_hash = name_hash(mat.name)
            mesh.material = mat
            mesh.matrix_palette = [
                geom.skeleton.bones[idx]
                for idx in mat._bone_indices
                if 0 <= idx < len(geom.skeleton.bones)
            ]
            geom.materials.append(mat)
            geom.meshes.append(mesh)

        cls._read_uniforms_section(
            r, base_offset, extra_base, materials_array_offset, material_count, geom.materials
        )

        return geom

    # Widest plausible span, relative to the skeleton table, that the by-name-
    # hash lookup sub-table (see _locate_bone_transform_array) could occupy
    # before BoneTransform[0] starts.
    _SKELETON_LOOKUP_SCAN_MIN = 0x30
    _SKELETON_LOOKUP_SCAN_MAX = 0x500
    _SKELETON_LOOKUP_SCAN_STEP = 8

    @classmethod
    def _locate_bone_transform_array(cls, r, table_pos, bone_count):
        """Finds where BoneTransform[0] actually starts, by scanning for the
        offset at which bone_count consecutive 48-byte (quat/pos/scale)
        blocks all look like valid transforms, rather than assuming a fixed
        or formula-derived gap after the table header.

        The region between the table header (+0x10 bone_count, +0x14 version)
        and BoneTransform[0] is a by-name-hash lookup table (small
        incrementing uint16 pairs) whose size is NOT a simple function of
        bone_count alone -- ground-truthed two ways under Wine against the
        real compiled dsts_formats.cp311-win_amd64.pyd: (1) constructing
        fresh Geom objects with bone counts 2..65 via its own set_*()/
        to_file() API showed a clean bone_count-bucketed formula (+0x50,
        growing +0x10 every 4 bones); (2) but reading a real, shipped
        chr050.geom (25 bones) via the compiled module's own Python getters
        (ground truth, no offset math) showed its true BoneTransform start
        does NOT match that formula's prediction for 25 bones -- confirmed
        by locating one bone's exact known position.x bytes directly in the
        file. I.e. the game's own asset pipeline and this reverse-engineered
        community module's write path use different bucket-growth constants
        for the same structure. A fixed formula can't cover both, so this
        scans instead: exactly one offset in the plausible range passed the
        validity check for chr050 (unit-magnitude quaternion, sane scale,
        no NaN, for all 25 bones simultaneously) -- the same offset the
        direct byte search independently confirmed.

        Falls back to the community-module bucket formula if no offset in
        the scanned range validates for every bone (e.g. a file whose real
        transforms are themselves degenerate); the per-bone NaN/zero guard
        below still protects Blender either way.
        """
        for rel in range(cls._SKELETON_LOOKUP_SCAN_MIN, cls._SKELETON_LOOKUP_SCAN_MAX, cls._SKELETON_LOOKUP_SCAN_STEP):
            base = table_pos + rel
            if base + 48 * bone_count > len(r.data):
                break
            valid = True
            for i in range(bone_count):
                off = base + i * 48
                quat = struct.unpack_from("<4f", r.data, off)
                scale = struct.unpack_from("<4f", r.data, off + 32)
                if any(v != v for v in quat + scale):
                    valid = False
                    break
                mag = sum(v * v for v in quat) ** 0.5
                if abs(mag - 1.0) >= 0.02 or not (0.001 < abs(scale[0]) < 1000):
                    valid = False
                    break
            if valid:
                return base

        # Best-effort fallback: community-module bucket formula (correct for
        # files written by dsts_formats.cp311-win_amd64.pyd itself; not
        # necessarily correct for real shipped game assets).
        return table_pos + 0x50 + 0x10 * ((bone_count - 1) // 4)

    @classmethod
    def _read_skeleton_bones(cls, r, base_offset, skeleton_table_rel, bone_names):
        table_pos = base_offset + skeleton_table_rel
        bone_count = r.u16(table_pos + 0x10)
        xform_base = cls._locate_bone_transform_array(r, table_pos, bone_count)

        bones = []
        for i in range(bone_count):
            off = xform_base + i * 48
            bone = Bone()
            quat = struct.unpack_from("<4f", r.data, off)
            pos = struct.unpack_from("<4f", r.data, off + 16)
            scale = struct.unpack_from("<4f", r.data, off + 32)
            # Defensive fallback only -- not expected to trigger against real
            # data anymore now that the base offset is correct, but a
            # degenerate transform would otherwise silently zero-length the
            # bone in Blender (edit_bone.matrix derives bone length from the
            # matrix's scale, and Blender discards zero-length bones on
            # leaving Edit Mode).
            quat_is_zero = sum(v * v for v in quat) < 1e-6
            scale_is_degenerate = any(abs(v) < 1e-6 for v in scale[:3])
            if any(v != v for v in quat + pos + scale) or quat_is_zero or scale_is_degenerate:
                bone.transform.quaternion = (0.0, 0.0, 0.0, 1.0)
                bone.transform.position = (0.0, 0.0, 0.0, 1.0)
                bone.transform.scale = (1.0, 1.0, 1.0, 1.0)
            else:
                bone.transform.quaternion = quat
                bone.transform.position = pos
                bone.transform.scale = scale
            if i < len(bone_names):
                bone.name = bone_names[i]
                bone.name_hash = name_hash(bone.name)
            bones.append(bone)
        return bones

    @staticmethod
    def _read_name_list(r, base_offset, ptr, count, extra_base):
        table_pos = base_offset + ptr
        names = []
        for i in range(count):
            rel_offset = r.i64(table_pos + i * 8)
            pos = base_offset + extra_base + rel_offset
            names.append(r.cstr(pos))
        return names

    # Channel ordinal -> Python-facing attribute name. `position` (ordinal 1)
    # is directly validated (decodes to a sane bounded 3D point cloud).
    # `normal`(2)/`tangent`(3) confirmed by component count (3 vs 4) matching
    # the export side's own conventions. `index`(10)/`weight`(11) confirmed
    # both by component count (4, matching the game's 4-influences-per-vertex
    # skinning limit) AND by dtype (see _FMT_DTYPE below -- uint8 for index,
    # float16 for weight, exactly matching mesh.py's export-side dtype
    # choices). Ordinal 5 was originally guessed as "binormal" but real data
    # shows count=2/float32 there, which fits a UV channel far better than a
    # binormal (typically 3-4 components) -- remapped to uv1. 6/7/8 (uv2/uv3/
    # color) are unvalidated guesses (not present in any material checked so
    # far); 4 duplicates 3 as a tangent fallback.
    _ORDINAL_ATYPE = {
        1: "position",
        2: "normal",
        3: "tangent",
        4: "tangent",
        5: "uv1",
        6: "uv2",
        7: "uv3",
        8: "color",
        9: "color",
        10: "index",
        11: "weight",
    }

    # Channel format code -> numpy dtype string (mesh.py's get_np_dtype()
    # vocabulary). Derived from the byte gap between consecutive channel
    # offsets (gap == count * bytes-per-component) and cross-checked exactly
    # against mesh.py's own export-side dtype choices (float16 for normal/
    # tangent/weight, uint8 for index/color) -- not a guess.
    _FMT_DTYPE = {
        9: "float",
        8: "float16",
        0: "uByte",
    }

    @classmethod
    def _read_material_and_mesh_record(cls, r, base_offset, rec, extra_base):
        mat = Material()
        mesh = Mesh()

        mat.name_hash = r.u32(rec + 0x34)
        name_rel_offset = r.i64(rec + 0x38)
        if name_rel_offset:
            mat.name = r.cstr(base_offset + extra_base + name_rel_offset)

        flags_byte = r.data[rec + 0x31]
        flag_bits = [bool((flags_byte >> b) & 1) for b in range(8)]
        (
            mesh.flag_0, mesh.flag_1, mesh.flag_2, mesh.flag_3,
            mesh.flag_4, mesh.flag_5, mesh.flag_6, mesh.flag_7,
        ) = flag_bits

        # record+0x10/+0x28 were originally (wrongly) assumed to be shader-
        # uniform IDs -- they actually resolve against geom_out+0x428, which
        # turned out to be the SKELETON table (16-byte shared_ptr<Bone>
        # stride), not a uniform table. So these are bone/matrix-palette
        # indices; resolved against geom.skeleton.bones by the caller
        # (Geom.from_bytes) once the skeleton is available and stashed on the
        # *mesh* (matrix_palette), not the material.
        bone_index_count = r.u16(rec + 0x28)
        bone_indices_rel = r.i64(rec + 0x10)
        if bone_index_count and bone_indices_rel:
            ids_pos = base_offset + bone_indices_rel
            mat._bone_indices = [r.u32(ids_pos + j * 4) for j in range(bone_index_count)]
        else:
            mat._bone_indices = []

        # Populated afterward by Geom._read_uniforms_section(), once every
        # material's record has been read (that section's start position depends
        # on ALL materials' geometry/attribute regions, not just this one's).
        mat.uniforms = []
        # Real on-disk location of ShaderSetting data not identified this
        # session -- left empty. Distinct from ShaderUniform (see
        # _read_uniforms_section): settings appear to be a different, still-
        # unlocated table (material.py's ShaderSetting has a raw-bytes `value`
        # and `unknown_0x12`, unlike uniform's typed value shapes).
        mat.settings = []

        # --- mesh geometry ---
        vertex_offset = r.i64(rec + 0x00)
        index_offset = r.i64(rec + 0x08)
        attr_count = r.u16(rec + 0x2A)
        bytes_per_vertex = r.u16(rec + 0x2C)
        vertex_count = r.u32(rec + 0x44)
        index_count = r.u32(rec + 0x48)
        mesh_attrs_rel = r.i64(rec + 0x20)

        mesh.bytes_per_vertex = bytes_per_vertex
        vbuf_pos = base_offset + vertex_offset
        mesh._packed_vertices = r.data[vbuf_pos: vbuf_pos + bytes_per_vertex * vertex_count]

        attrs = []
        if attr_count and mesh_attrs_rel:
            attrs_pos = base_offset + mesh_attrs_rel
            for j in range(attr_count):
                off = attrs_pos + j * 8
                ordinal, count, fmt, byte_offset = struct.unpack_from("<HHHH", r.data, off)
                ma = MeshAttribute()
                ma.count = count
                ma.offset = byte_offset
                ma.atype = cls._ORDINAL_ATYPE.get(ordinal, f"unknown_ordinal_{ordinal}")
                ma.dtype = cls._FMT_DTYPE.get(fmt, "float")
                attrs.append(ma)
        mesh.mesh_attributes = attrs

        ibuf_pos = base_offset + index_offset
        strip = list(struct.unpack_from(f"<{index_count}H", r.data, ibuf_pos)) if index_count else []
        mesh._indices = _tristrip_to_trilist(strip)

        return mat, mesh

    # Fixed size of the per-material block that precedes each material's uniform-
    # record array (constant literal in the compiled module's disassembly). Its
    # internal layout wasn't decoded -- looks like a per-material "shader
    # technique" section -- but since its size never varies, it can just be
    # skipped.
    _UNIFORM_SECTION_HEADER_BLOCK_SIZE = 0x328
    _UNIFORM_RECORD_SIZE = 32
    _UNIFORM_RECORD_SENTINEL = b"\xFF" * 11  # bytes [0x14:0x1F) of every real record

    @classmethod
    def _read_uniforms_section(cls, r, base_offset, extra_base, materials_array_offset, material_count, materials):
        """Reads the real shader-uniform/texture-binding data, which is NOT part of
        the 128-byte material record (that record has no room left for it -- every
        byte is already accounted for by geometry/bone-palette fields). It's a
        separate section, read via a plain sequential (no-seek) stream in the
        compiled module, that comes after EVERY per-material region referenced by
        the materials array (vertex buffer, index buffer, bone-index/matrix-palette
        array, mesh-attribute descriptor array -- for ALL materials, not just the
        current one) -- laid out as `material_count` consecutive
        `[808-byte header block][variable-count 32-byte uniform record]` groups,
        one per material in index order.

        The section's start offset isn't stored anywhere as an explicit field --
        it's implicitly wherever the sequential writer left off, which empirically
        equals `max(end offset of every region any material points into)` across
        ALL materials (verified byte-exact against a real chr090.geom: predicted
        0x21aec0 from this formula matches the real end of material 5's mesh-
        attribute table exactly, off by 0 bytes).

        Each uniform record has no explicit type tag beyond a `sub` field that
        selects the value's shape (0 = texture reference, 1-4 = that many packed
        float32s, 100 = a 2x int32 render-state pair -- e.g. alpha_test_enable,
        blend_equation, blend_enable, cull_face; not a true shader uniform, stored
        as float32-valued for round-trip simplicity since material.py has no
        separate render-state uniform type). There's likewise no explicit count
        field for the record array -- each record ends with an 11-byte 0xFF
        sentinel (offsets 0x14-0x1E) that breaks as soon as the next 32 bytes
        aren't a real record anymore, which is how the array's end is detected.
        """
        max_end = 0
        for i in range(material_count):
            rec = base_offset + materials_array_offset + i * 128
            vertex_offset = r.i64(rec + 0x00)
            index_offset = r.i64(rec + 0x08)
            bone_indices_rel = r.i64(rec + 0x10)
            mesh_attrs_rel = r.i64(rec + 0x20)
            bone_index_count = r.u16(rec + 0x28)
            attr_count = r.u16(rec + 0x2A)
            bytes_per_vertex = r.u16(rec + 0x2C)
            vertex_count = r.u32(rec + 0x44)
            index_count = r.u32(rec + 0x48)
            ends = (
                vertex_offset + bytes_per_vertex * vertex_count,
                index_offset + index_count * 2,
                (bone_indices_rel + bone_index_count * 4) if bone_indices_rel else 0,
                (mesh_attrs_rel + attr_count * 8) if mesh_attrs_rel else 0,
            )
            max_end = max(max_end, *ends)

        pos = base_offset + max_end
        for mat in materials:
            pos += cls._UNIFORM_SECTION_HEADER_BLOCK_SIZE
            while pos + cls._UNIFORM_RECORD_SIZE <= len(r.data):
                rec = r.data[pos:pos + cls._UNIFORM_RECORD_SIZE]
                if rec[0x14:0x1F] != cls._UNIFORM_RECORD_SENTINEL:
                    break

                uid, sub = struct.unpack_from("<HH", rec, 0x10)
                uniform = ShaderUniform()
                uniform.parameter_name = _UNIFORM_ID_NAMES.get(uid, f"unknown_uniform_{uid}")

                if sub == 0:
                    reloff = struct.unpack_from("<i", rec, 0x00)[0]
                    extra = struct.unpack_from("<I", rec, 0x0C)[0]
                    uniform.uniform_type = "texture"
                    uniform.value = r.cstr(base_offset + extra_base + reloff)
                    uniform.unknown_0xC = extra
                elif sub in (1, 2, 3, 4):
                    uniform.uniform_type = "float"
                    uniform.value = list(struct.unpack_from(f"<{sub}f", rec, 0x00))
                else:
                    uniform.uniform_type = "float"
                    uniform.value = list(struct.unpack_from("<2i", rec, 0x00))

                mat.uniforms.append(uniform)
                pos += cls._UNIFORM_RECORD_SIZE

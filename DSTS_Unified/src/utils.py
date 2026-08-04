import mathutils
import math

def unflop(matrix):
    rot = mathutils.Matrix.Rotation(math.radians(90), 4, 'X')
    return rot @ matrix

def flop(matrix):
    rot = mathutils.Matrix.Rotation(math.radians(-90), 4, 'X')
    return rot @ matrix

def skeleton_remap(matrix):
    """Unused -- kept only so old callers don't crash on import.

    An earlier version of dsts_formats.py's skeleton-table reader used the
    wrong base offset for the BoneTransform array (off by 0xC0 bytes into an
    unrelated lookup sub-table), which silently corrupted the root of the
    bone chain (GRP_joint/J_root/J_center/J_leg_l) via an identity-transform
    fallback. That corruption looked like a coordinate-system mismatch, and
    this 180-X + 90-Z rotation was written to visually compensate for it.

    Once the real offset bug was fixed, a direct comparison of raw (pre-
    remap) bone-chain positions against raw per-bone weighted-vertex
    centroids (same file space, no remap on either side) showed the two
    already agree axis-for-axis (X matches to within ~0.02, and the residual
    on the other two axes shrinks toward zero at extremities -- exactly the
    signature of "centroid of skin geometry" vs "exact joint pivot", not a
    coordinate bug). So skeletons need the *same* remap as mesh vertices,
    utils.unflop, not a bespoke one. geom.py now calls utils.unflop directly
    for the skeleton import; do not reintroduce this function as the active
    remap without new evidence."""
    rx = mathutils.Matrix.Rotation(math.radians(180), 4, 'X')
    rz = mathutils.Matrix.Rotation(math.radians(90), 4, 'Z')
    return rz @ rx @ matrix
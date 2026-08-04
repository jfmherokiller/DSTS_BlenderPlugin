"""Pure-Python reader for the game's MVGL archives, used to extract textures
(and other loose files) directly from the installed game instead of requiring
a separate manual-extraction step first.

No external dependencies (no pip install needed inside Blender's Python) --
includes its own LZ4 block decompressor, since MVGL entries are compressed
with the raw/unframed LZ4 block format (same format K4os.Compression.LZ4's
LZ4Codec.Decode uses, which is what the game's own MVLibraryNET tooling is
built on).

MVGL format (little-endian, all offsets/counts as documented in this repo's
RE/battle_voice_system.md tooling notes and MVLibraryNET's MVGL/x64/*.cs):

    MDB1Header (32 bytes):
        uint32 Magic
        uint32 FileEntryCount
        uint32 FileNameCount
        uint32 DataEntryCount
        uint64 DataStart
        uint64 TotalSize
    FileTreeEntry[FileEntryCount] (16 bytes each): uint32 CompareBit, DataId, Left, Right
    FileNameEntry[FileNameCount] (128 bytes each): 4-byte Extension, 124-byte NUL-padded Name
    FileDataEntry[DataEntryCount] (24 bytes each): uint64 Offset, FullSize, CompressedSize

Only the directory (header + tree + names + data-entries -- a few tens of MB even
for the ~13 GB base archive) is read into memory; individual file payloads are
seeked-to and read on demand, never the whole archive.
"""

import os
import struct

_HEADER_FMT = "<IIIIQQ"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_TREE_FMT = "<IIII"
_TREE_SIZE = struct.calcsize(_TREE_FMT)
_NAME_SIZE = 128
_DATA_FMT = "<QQQ"
_DATA_SIZE = struct.calcsize(_DATA_FMT)


def _lz4_block_decompress(data: bytes, uncompressed_size: int) -> bytes:
    """Raw/unframed LZ4 block decompression (github.com/lz4/lz4 block format spec)."""
    out = bytearray(uncompressed_size)
    out_pos = 0
    in_pos = 0
    in_len = len(data)

    while in_pos < in_len:
        token = data[in_pos]
        in_pos += 1

        literal_length = token >> 4
        if literal_length == 15:
            while True:
                b = data[in_pos]
                in_pos += 1
                literal_length += b
                if b != 255:
                    break

        if literal_length:
            out[out_pos:out_pos + literal_length] = data[in_pos:in_pos + literal_length]
            in_pos += literal_length
            out_pos += literal_length

        if in_pos >= in_len:
            break  # last sequence has no match part

        offset = data[in_pos] | (data[in_pos + 1] << 8)
        in_pos += 2

        match_length = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = data[in_pos]
                in_pos += 1
                match_length += b
                if b != 255:
                    break

        match_start = out_pos - offset
        if offset >= match_length:
            out[out_pos:out_pos + match_length] = out[match_start:match_start + match_length]
        else:
            for i in range(match_length):
                out[out_pos + i] = out[match_start + i]
        out_pos += match_length

    return bytes(out)


def _parse_name(raw: bytes) -> str:
    ext = raw[0:4]
    name = raw[4:_NAME_SIZE]
    nul = name.find(b"\x00")
    if nul == -1:
        nul = len(name)
    name_s = name[:nul].decode("ascii", errors="replace")
    ext_len = 3 if ext[3:4] == b" " else 4
    ext_s = ext[:ext_len].decode("ascii", errors="replace")
    return f"{name_s}.{ext_s}"


class MvglArchive:
    """One .mvgl file. Directory is parsed once (lazily) and kept in memory;
    file payloads are read on demand via seek, never the whole archive."""

    def __init__(self, path):
        self.path = path
        self._data_start = None
        self._entries = None  # basename (no dir prefix) -> (offset, compressed_size, full_size)

    def _ensure_loaded(self):
        if self._entries is not None:
            return
        entries = {}
        with open(self.path, "rb") as f:
            header = f.read(_HEADER_SIZE)
            magic, file_entry_count, file_name_count, data_entry_count, data_start, total_size = (
                struct.unpack(_HEADER_FMT, header)
            )
            tree_raw = f.read(_TREE_SIZE * file_entry_count)
            names_raw = f.read(_NAME_SIZE * file_name_count)
            dents_raw = f.read(_DATA_SIZE * data_entry_count)

        for i in range(file_entry_count):
            compare_bit, data_id, left, right = struct.unpack_from(_TREE_FMT, tree_raw, i * _TREE_SIZE)
            if data_id == 0xFFFFFFFF:
                continue
            full_name = _parse_name(names_raw[i * _NAME_SIZE:(i + 1) * _NAME_SIZE])
            # Some entries carry a directory-style prefix baked into the name
            # (e.g. "images\atcbook00.img") -- index by basename only, since
            # that's what geom materials reference and what this addon's
            # existing find_texture_file() already matches on.
            basename = full_name.replace("\\", "/").rsplit("/", 1)[-1]
            off, full, comp = struct.unpack_from(_DATA_FMT, dents_raw, data_id * _DATA_SIZE)
            entries[basename] = (data_start + off, comp, full)

        self._entries = entries

    def has(self, basename: str) -> bool:
        self._ensure_loaded()
        return basename in self._entries

    def names(self):
        self._ensure_loaded()
        return self._entries.keys()

    def extract(self, basename: str):
        """Returns the decompressed bytes for basename, or None if not present."""
        self._ensure_loaded()
        entry = self._entries.get(basename)
        if entry is None:
            return None
        offset, comp_size, full_size = entry
        if full_size == 0:
            return b""
        with open(self.path, "rb") as f:
            f.seek(offset)
            comp_bytes = f.read(comp_size)
        if comp_size == full_size:
            # Some entries are stored uncompressed.
            try:
                return _lz4_block_decompress(comp_bytes, full_size)
            except Exception:
                return comp_bytes
        return _lz4_block_decompress(comp_bytes, full_size)


class GameArchiveSet:
    """Manages the game's archives in override priority order: base app_0,
    then patch (overrides base), then any addcont_* DLC archives (override
    both, since DLC-introduced textures should win over anything they touch)."""

    _cache = {}  # game_dir -> GameArchiveSet, so re-opening the same game dir is free

    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.archives = []  # in priority order, LAST wins on name conflicts
        gamedata = os.path.join(game_dir, "gamedata")

        base = os.path.join(gamedata, "app_0.dx11.mvgl")
        if os.path.isfile(base):
            self.archives.append(MvglArchive(base))

        patch = os.path.join(gamedata, "patch.dx11.mvgl")
        if os.path.isfile(patch):
            self.archives.append(MvglArchive(patch))

        if os.path.isdir(gamedata):
            for fname in sorted(os.listdir(gamedata)):
                if fname.lower().startswith("addcont_") and fname.lower().endswith(".dx11.mvgl"):
                    self.archives.append(MvglArchive(os.path.join(gamedata, fname)))

    @classmethod
    def get(cls, game_dir):
        game_dir = os.path.normpath(game_dir)
        if game_dir not in cls._cache:
            cls._cache[game_dir] = cls(game_dir)
        return cls._cache[game_dir]

    def is_valid(self):
        return len(self.archives) > 0

    def extract_texture(self, tex_name: str):
        """Looks up "<tex_name>.img" across all archives (later archives override
        earlier ones), returns the decompressed DDS bytes, or None if not found
        anywhere."""
        basename = tex_name + ".img"
        result = None
        for archive in self.archives:
            data = archive.extract(basename)
            if data is not None:
                result = data  # keep looking -- a later (higher-priority) archive may also have it
        return result

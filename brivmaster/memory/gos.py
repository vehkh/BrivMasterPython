"""Game object structure model.

Port of IBM_GOS and the pointer classes from IC_BrivMaster_Memory.ahk.

Design difference from the AHK original: instead of cloning offset arrays and
patching children in place (which AHK needed because collection access mutates
the structure tree), the structure is split into:

  * GosTemplate - the static tree built once from the generated import files.
    Each template stores offsets *relative to its parent*.
  * GosNode - a cheap, immutable view pairing a template with the absolute
    offset chain accumulated on the way down. Collection indexing just creates
    a new node with the entry offsets appended, so children resolved through
    it are automatically rebased - the same net effect as the AHK
    UpdateChildrenWithFullOffsets machinery.

Semantics (offsets, strides, collection layouts) are kept byte-identical to
the AHK version; the mono/.NET collection layout constants below come
straight from it.
"""

from __future__ import annotations

import math

from .backend import TYPE_SIZES

# C# type -> reader value type (IBM_GOS.SystemTypes)
SYSTEM_TYPES = {
    "System.Byte": "Char", "System.UByte": "UChar",
    "System.Short": "Short", "System.UShort": "UShort",
    "System.Int32": "Int", "System.UInt32": "UInt",
    "System.Int64": "Int64", "System.Enum": "Int",
    "System.UInt64": "Int64", "System.Single": "Float",
    "System.USingle": "UFloat", "System.Double": "Double",
    "System.Boolean": "Char", "System.String": "UTF-16",
    "Engine.Numeric.Quad": "Quad",
}

# Stride of items inside collections (IBM_GOS.ValueTypeToBytes). Note this is
# NOT the read size: 1/2-byte values still occupy 4-byte slots in collections.
COLLECTION_STRIDES = {
    "Char": 0x4, "UChar": 0x4, "Short": 0x4, "UShort": 0x4,
    "Int": 0x4, "UInt": 0x4, "Int64": 0x8, "UInt64": 0x8,
    "Float": 0x4, "UFloat": 0x4, "Double": 0x8,
    "UTF-16": 0x8, "Quad": 0x10,
}

COLLECTION_TYPES = frozenset(("List", "Dict", "HashSet", "Stack", "Queue"))

# Offset of the count field per collection type (CreateSizeObject)
SIZE_OFFSETS = {"Stack": 0x20, "Queue": 0x28, "Dict": 0x40, "HashSet": 0x30}
SIZE_OFFSET_DEFAULT = 0x18  # List / Array

# Offset of the mutation counter per collection type (CreateVersionObject)
VERSION_OFFSETS = {"Stack": 0x2C, "List": 0x1C, "Queue": 0x28,
                   "Dict": 0x4C, "HashSet": 0x104}

_DICT_SANITY_MAX = 32000

# Field paths requested but absent from the loaded imports - AHK degrades
# silently there (reads return ""); we do the same but keep a record so
# mismatched/outdated imports are diagnosable.
MISSING_FIELDS = set()


class NullNode:
    """Stand-in for a field missing from the imports. All reads fail softly,
    matching the AHK behaviour of chaining off an empty object."""

    __slots__ = ()
    value_type = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __getitem__(self, key):
        return self

    def child(self, name):
        return self

    def has_child(self, name):
        return False

    def __bool__(self):
        return False

    def read(self, value_type=None):
        return None

    def size(self):
        return None

    def version(self):
        return None

    def item(self, index):
        return self

    def dict_value(self, key):
        return None

    def dict_key_at(self, index):
        return self

    def hashset_item(self, key):
        return self

    def read_quad_parts(self):
        return None

    def resolve_address(self):
        return None

    def size_address(self):
        return None

    def dict_value_at(self, index):
        return self

    def rebase(self):
        return None

    def __call__(self, *args, **kwargs):
        # Safety net: an unknown method resolved through __getattr__ ends up
        # here - fail soft like every other NullNode access.
        return None

    @property
    def offsets(self):
        return ()


NULL_NODE = NullNode()


class GosTemplate:
    """One field in the game's object graph, as declared by the imports."""

    __slots__ = ("name", "rel_offsets", "value_type", "children",
                 "collection_key_type", "collection_val_type",
                 "array_dimensions", "dict_cache")

    def __init__(self, name, rel_offsets, value_type="Int"):
        self.name = name
        self.rel_offsets = tuple(int(o) for o in rel_offsets)
        self.value_type = value_type
        self.children = {}
        self.collection_key_type = ""
        self.collection_val_type = ""
        self.array_dimensions = None
        self.dict_cache = {}  # (offsets, key) -> (entry index, dict version)


class RootContext:
    """Shared, mutable attachment state for one pointer root
    (IdleGameManager / GameSettings / EngineSettings)."""

    __slots__ = ("mem", "base_address")

    def __init__(self):
        self.mem = None
        self.base_address = None


class GosNode:
    """A readable location in game memory. Cheap to create; hold no state."""

    __slots__ = ("_tmpl", "_ctx", "_offsets", "value_type")

    def __init__(self, tmpl, ctx, offsets, value_type=None):
        object.__setattr__(self, "_tmpl", tmpl)
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_offsets", offsets)
        object.__setattr__(self, "value_type",
                           value_type if value_type is not None else tmpl.value_type)

    # --- structure navigation -------------------------------------------

    def __getattr__(self, name):
        # AHK object keys are case-insensitive and the generated imports use
        # different casing than the hand-written readers - children are
        # therefore keyed lowercase.
        child = self._tmpl.children.get(name.lower())
        if child is None:
            MISSING_FIELDS.add(f"{self._tmpl.name}.{name}")
            return NULL_NODE
        return GosNode(child, self._ctx, self._offsets + child.rel_offsets)

    def child(self, name):
        """Explicit child access, for names that clash with methods."""
        return self.__getattr__(name)

    def has_child(self, name):
        return name.lower() in self._tmpl.children

    def __getitem__(self, key):
        vt = self.value_type
        if vt == "Dict":
            return self.dict_value(key)
        if vt == "HashSet":
            return self.hashset_item(key)
        return self.item(int(key))

    def item(self, index):
        """Index into a List/Stack/Queue, or a raw array for other types."""
        tmpl = self._tmpl
        mapped = SYSTEM_TYPES.get(tmpl.collection_val_type)
        if self.value_type in ("List", "Stack", "Queue"):
            stride = COLLECTION_STRIDES.get(mapped, 0x8) if mapped else 0x8
            extra = (0x10, 0x20 + index * stride)
            # AHK list items keep the collection's ValueType, which read() maps
            # to a 4-byte Int - matches behaviour for the Int32 lists in use.
            item_type = mapped if mapped else "Int"
        else:
            # Raw array indexing (the AHK "key is number" fallback / Array type)
            dims = tmpl.array_dimensions
            if dims is not None and dims > 1:
                item_size = 0x8
                item_type = "Int64"
            elif mapped and mapped in TYPE_SIZES:
                item_size = TYPE_SIZES[mapped]
                item_type = mapped
            else:
                item_size = 0x8
                item_type = "Int"
            extra = (0x20 + index * item_size,)
        return GosNode(tmpl, self._ctx, self._offsets + extra, item_type)

    def size(self):
        """Element count of a collection."""
        off = SIZE_OFFSETS.get(self.value_type, SIZE_OFFSET_DEFAULT)
        return self._read_typed(self._offsets + (off,), "Int")

    def version(self):
        """Mutation counter of a collection ('__version' in the AHK code)."""
        off = VERSION_OFFSETS.get(self.value_type)
        if off is None:
            return None
        return self._read_typed(self._offsets + (off,), "Int")

    def size_address(self):
        """Resolved address of the collection count - for pinned spam reads."""
        ctx = self._ctx
        if ctx.mem is None:
            return None
        off = SIZE_OFFSETS.get(self.value_type, SIZE_OFFSET_DEFAULT)
        return ctx.mem.resolve(ctx.base_address, self._offsets + (off,))

    # --- dictionaries ------------------------------------------------------

    def _dict_layout(self):
        """Entry layout constants for this Dict (CalculateDictOffset)."""
        key_mapped = SYSTEM_TYPES.get(self._tmpl.collection_key_type)
        val_mapped = SYSTEM_TYPES.get(self._tmpl.collection_val_type)
        key_bytes = COLLECTION_STRIDES.get(key_mapped, 0x8) if key_mapped else 0x8
        val_bytes = COLLECTION_STRIDES.get(val_mapped, 0x8) if val_mapped else 0x8
        item_size = 0x4 if (key_mapped and val_mapped
                            and key_bytes == 0x4 and val_bytes == 0x4) else 0x8
        interval = 0x10 if item_size == 0x4 else 0x18
        if val_mapped == "Quad":
            interval = 0x20
        return key_mapped, val_mapped, item_size, interval

    def _dict_entry_offset(self, index, want_value):
        _, _, item_size, interval = self._dict_layout()
        offset = 0x28 + interval * index  # 64-bit dict entries start at 0x28
        if want_value:
            offset += item_size
        return offset

    def dict_key_at(self, index):
        """Node for the key of the <index>th entry (the AHK ["key", i] access)."""
        key_mapped, _, _, _ = self._dict_layout()
        extra = (0x18, self._dict_entry_offset(index, want_value=False))
        return GosNode(self._tmpl, self._ctx, self._offsets + extra,
                       key_mapped if key_mapped else "Int64")

    def _dict_value_node(self, index):
        _, val_mapped, _, _ = self._dict_layout()
        extra = (0x18, self._dict_entry_offset(index, want_value=True))
        return GosNode(self._tmpl, self._ctx, self._offsets + extra,
                       val_mapped if val_mapped else "Int")

    def dict_value_at(self, index):
        """Value node of the <index>th entry (the AHK ["value", i] access)."""
        return self._dict_value_node(index)

    def dict_value(self, key):
        """Find a dict entry by key (linear scan, version-validated cache).
        Returns a value node, or None when the key is absent/unreadable."""
        count = self.size()
        if count is None or count < 0 or count > _DICT_SANITY_MAX:
            return None
        current_version = self.version()
        cache_key = (self._offsets, key)
        cached = self._tmpl.dict_cache.get(cache_key)
        if cached is not None and current_version is not None \
                and cached[1] == current_version:
            return self._dict_value_node(cached[0])
        for index in range(count):
            if self.dict_key_at(index).read() == key:
                if current_version is not None:
                    self._tmpl.dict_cache[cache_key] = (index, current_version)
                return self._dict_value_node(index)
        return None

    def hashset_item(self, key):
        """Index into a HashSet (CalculateHashSetOffset)."""
        key_mapped = SYSTEM_TYPES.get(self._tmpl.collection_key_type)
        key_bytes = COLLECTION_STRIDES.get(key_mapped, 0x8) if key_mapped else 0x8
        item_size = 0x4 if (key_mapped and key_bytes == 0x4) else 0x8
        base = 0x20 if item_size == 0x4 else 0x28
        interval = 0xC if item_size == 0x4 else 0x10
        extra = (0x18, base + interval * int(key))
        return GosNode(self._tmpl, self._ctx, self._offsets + extra,
                       key_mapped if key_mapped else "Int")

    # --- reading ------------------------------------------------------------

    @property
    def offsets(self):
        return self._offsets

    def resolve_address(self):
        ctx = self._ctx
        if ctx.mem is None:
            return None
        return ctx.mem.resolve(ctx.base_address, self._offsets)

    def _read_typed(self, offsets, value_type):
        ctx = self._ctx
        if ctx.mem is None:
            return None
        return ctx.mem.read_chain(ctx.base_address, offsets, value_type)

    def read(self, value_type=None):
        vt = value_type or self.value_type
        if vt == "UTF-16":
            return self._read_string()
        if vt == "Quad":
            parts = self.read_quad_parts()
            if parts is None:
                return None
            return quad_to_string(*parts)
        if vt in COLLECTION_TYPES:
            # Reading a collection field returns the low half of its pointer -
            # only useful as a null check. Same as the AHK original.
            return self._read_typed(self._offsets, "Int")
        if vt == "Array":
            return self._read_typed(self._offsets, "UInt")
        return self._read_typed(self._offsets, vt)

    def _read_string(self):
        ctx = self._ctx
        if ctx.mem is None:
            return None
        field_addr = self.resolve_address()
        if field_addr is None:
            return None
        string_ptr = ctx.mem.read(field_addr, "Int64")
        if string_ptr is None or string_ptr <= 0:
            return None
        return ctx.mem.read_mono_string(string_ptr)

    def read_quad_parts(self):
        """The two Int64 halves of a 16-byte Quad, or None."""
        addr = self.resolve_address()
        if addr is None:
            return None
        first8 = self._ctx.mem.read(addr, "Int64")
        second8 = self._ctx.mem.read(addr + 0x8, "Int64")
        if first8 is None or second8 is None:
            return None
        return first8, second8

    def rebase(self):
        """Port of IBM_ReBase: pin this node to its currently-resolved address
        so later reads skip the full pointer walk. The result goes stale on
        game reset/restart, exactly like the AHK version."""
        addr = self.resolve_address()
        if addr is None:
            return None
        pinned = RootContext()
        pinned.mem = self._ctx.mem
        pinned.base_address = addr
        return GosNode(self._tmpl, pinned, (), self.value_type)


# --- Quad conversions (ConvQuadToString3 / IBM_ConvQuadToExponent) ----------
# Note: AHK's log() is base 10.

def quad_to_string(first8, second8):
    """Quad value (ConvQuadToString3 port). Small values come back as a
    number (so stat maths works); large ones as an '1.23e45' string."""
    try:
        f = math.log10(first8 + 2.0 ** 63)
        decimated = math.log10(2) * second8 + f
        if decimated <= 4:
            return round((first8 + 2.0 ** 63) * (2.0 ** second8), 2)
        exponent = math.floor(decimated)
        significand = round(10 ** (decimated - exponent), 2)
        return f"{significand}e{exponent}"
    except (ValueError, OverflowError):
        return None


def quad_to_exponent(first8, second8):
    """Quad as a base-10 exponent, e.g. 85.9 for 8e85 (IBM_ConvQuadToExponent)."""
    try:
        f = math.log10(first8 + 2.0 ** 63)
        decimated = math.log10(2) * second8 + f
        if decimated <= 4:
            return round((first8 + 2.0 ** 63) * (2.0 ** second8), 2)
        exponent = math.floor(decimated)
        significand = round(10 ** (decimated - exponent), 2)
        return exponent + math.log10(significand)
    except (ValueError, OverflowError):
        return None

"""Parser for the generated offset import files (IC_*_Import.ahk).

The imports are generated AHK code (from the BrivMaster-Imports repository)
consisting of only three line shapes:

    this.<path>:=New IBM_GOS(this.<parent>,"<Type>",[<offset expr>])
    this.<path>._CollectionKeyType:="<C# type>"     (same for ValType)
    this.<path>._ArrayDimensions:=<n>

Parsing them directly keeps the existing "download offsets from GitHub"
update flow working without converting anything.

Offset expressions are integers or 'this.StaticOffset+<n>' terms (used by the
GameSettings/EngineSettings imports).
"""

from __future__ import annotations

import re

from .gos import GosTemplate

_GOS_RE = re.compile(
    r'^this\.(?P<path>[\w.]+)\s*:=\s*New\s+IBM_GOS\(\s*this\.(?P<parent>[\w.]+)\s*,'
    r'\s*"(?P<vtype>[^"]+)"\s*,\s*\[(?P<offsets>[^\]]*)\]\s*\)\s*$')
_COLLECTION_RE = re.compile(
    r'^this\.(?P<path>[\w.]+)\._Collection(?P<which>Key|Val)Type\s*:='
    r'\s*"(?P<ctype>[^"]*)"\s*$')
_DIMENSIONS_RE = re.compile(
    r'^this\.(?P<path>[\w.]+)\._ArrayDimensions\s*:=\s*(?P<dims>\d+)\s*$')
_STATIC_TERM_RE = re.compile(
    r'^this\.StaticOffset(?:\s*\+\s*(?P<add>\d+))?$')


class ImportParseError(Exception):
    pass


def _eval_offset(token, static_offset):
    token = token.strip()
    match = _STATIC_TERM_RE.match(token)
    if match:
        return static_offset + int(match.group("add") or 0)
    return int(token, 0)  # base 0 handles both decimal and 0x hex


def parse_import_file(path, root_template, root_alias, static_offset=0):
    """Populate root_template's tree from one generated import file.

    root_alias is the AHK expression naming the root inside the file, e.g.
    'IdleGameManager', 'CrusadersGame.GameSettings',
    'UnityGameEngine.Core.EngineSettings'.

    Returns a list of lines that could not be parsed (should be empty for
    well-formed generated imports).
    """
    # Flat namespace of every path defined so far; new fields also attach to
    # the top level (they are 'this.<name>' in the AHK class scope).
    # Keys are lowercased - AHK identifiers are case-insensitive.
    namespace = {root_alias.lower(): root_template}
    unparsed = []

    with open(path, "r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue

            match = _GOS_RE.match(line)
            if match:
                parent = namespace.get(match.group("parent").lower())
                if parent is None:
                    unparsed.append(line)
                    continue
                field_path = match.group("path")
                name = field_path.rsplit(".", 1)[-1]
                try:
                    offsets = [_eval_offset(tok, static_offset)
                               for tok in match.group("offsets").split(",") if tok.strip()]
                except ValueError:
                    unparsed.append(line)
                    continue
                template = GosTemplate(name, offsets, match.group("vtype"))
                parent.children[name.lower()] = template
                namespace[field_path.lower()] = template
                continue

            match = _COLLECTION_RE.match(line)
            if match:
                template = namespace.get(match.group("path").lower())
                if template is None:
                    unparsed.append(line)
                    continue
                if match.group("which") == "Key":
                    template.collection_key_type = match.group("ctype")
                else:
                    template.collection_val_type = match.group("ctype")
                continue

            match = _DIMENSIONS_RE.match(line)
            if match:
                template = namespace.get(match.group("path").lower())
                if template is None:
                    unparsed.append(line)
                    continue
                template.array_dimensions = int(match.group("dims"))
                continue

            unparsed.append(line)

    return unparsed

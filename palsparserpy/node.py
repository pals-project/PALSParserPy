"""
The YAML tree object model: :class:`YAMLTree`, :class:`YAMLNode`, and the
parsing, navigation, editing and emitting operations on them.

  - :class:`YAMLTree` owns the C tree handle and frees it when it is collected.
  - :class:`YAMLNode` is a lightweight value holding a reference to its parent
    tree (keeping it alive) and the integer node id.

Indexing is 0-based, as everywhere else in Python and as in the underlying C
API; a negative index counts from the end.
"""

from __future__ import annotations

import os
from typing import Iterable, Iterator, Union

from ._clib import YAML_NULL_ID, encode, libparser, take_string

__all__ = [
    "YAMLTree", "YAMLNode", "PALSParseError",
    "parse_file", "parse_string", "create_empty_tree",
    "is_map", "is_sequence", "is_scalar", "get_parent", "node_key",
    "add_scalar", "add_map", "add_sequence", "set_scalar", "set_key", "remove",
    "deep_copy_node", "deep_copy_children", "to_yaml_string", "write_yaml",
]


class PALSParseError(ValueError):
    """A YAML document could not be parsed.

    The message carries what the C library reported -- for a syntax error,
    prefixed with the offending ``line L, column C:`` -- so the fault can be
    pinpointed instead of reported as a bare failure.
    """


# ─── core types ──────────────────────────────────────────────────────────────

class YAMLTree:
    """Owns a C ``YAMLTreeHandle``. Freed automatically when the object is
    garbage collected. Do not use the handle after the tree has been freed."""

    __slots__ = ("handle", "__weakref__")

    def __init__(self, handle):
        if not handle:
            raise ValueError("Invalid YAML tree handle (C returned NULL)")
        self.handle = handle

    def __del__(self):
        handle, self.handle = getattr(self, "handle", None), None
        if handle:
            try:
                libparser().delete_tree(handle)
            except Exception:      # interpreter teardown; nothing left to free into
                pass

    def __repr__(self):
        return f"<YAMLTree at 0x{self.handle:x}>" if self.handle else "<YAMLTree (freed)>"


class YAMLNode:
    """A reference to a single node within a :class:`YAMLTree`.

    Holding a ``YAMLNode`` keeps its tree alive. Node ids are invalidated if the
    tree is deleted.
    """

    __slots__ = ("tree", "id")

    def __init__(self, tree: YAMLTree, node_id: int):
        self.tree = tree
        self.id = node_id

    # Two YAMLNodes are equal when they point at the same id in the same tree.
    # Defining these lets YAMLNode be used as a dict key (e.g. in
    # node_correspondence).
    def __eq__(self, other):
        if not isinstance(other, YAMLNode):
            return NotImplemented
        return self.tree is other.tree and self.id == other.id

    def __hash__(self):
        return hash((id(self.tree), self.id))

    # ─── type checks ───────────────────────────────────────────────────────
    def is_map(self) -> bool:
        """Whether this node is a MAP (a collection of key/value pairs).

        A node is exactly one of MAP, sequence, or scalar; use this to decide
        before accessing children by key.
        """
        return bool(libparser().is_map(self.tree.handle, self.id))

    def is_sequence(self) -> bool:
        """Whether this node is a sequence (an ordered list of elements).

        A node is exactly one of MAP, sequence, or scalar; use this to decide
        before accessing children by index.
        """
        return bool(libparser().is_sequence(self.tree.handle, self.id))

    def is_scalar(self) -> bool:
        """Whether this node is a scalar (a leaf holding a single string, number
        or boolean value).

        Scalar nodes have no children and their value is read with
        :attr:`value`, :meth:`as_int`, :meth:`as_float` or :meth:`as_bool`.
        """
        return bool(libparser().is_scalar(self.tree.handle, self.id))

    # ─── traversal ─────────────────────────────────────────────────────────
    def parent(self) -> "YAMLNode":
        """The parent of this node. Raises ``ValueError`` for the root, which has
        no parent."""
        node_id = libparser().get_parent(self.tree.handle, self.id)
        if node_id == YAML_NULL_ID:
            raise ValueError("Node has no parent (it is the root)")
        return YAMLNode(self.tree, node_id)

    def root(self) -> "YAMLNode":
        """The root of the tree this node belongs to (this node itself if it is
        the root)."""
        return YAMLNode(self.tree, libparser().get_root(self.tree.handle))

    def child(self, index: int) -> "YAMLNode":
        """The ``index``-th direct child of a MAP or sequence node, 0-based.

        Unlike ``node[key]``, this reaches a MAP's children by position, which is
        how a single-key map entry is opened without knowing its key.
        """
        n = len(self)
        if index < 0:
            index += n
        if not 0 <= index < n:
            raise IndexError(f"Index out of bounds: {index}")
        node_id = libparser().get_child_by_index(self.tree.handle, self.id, index)
        if node_id == YAML_NULL_ID:
            raise IndexError(f"Index out of bounds: {index}")
        return YAMLNode(self.tree, node_id)

    def get(self, key: str, default=None):
        """The child stored under ``key``, or ``default`` if there is none."""
        node_id = libparser().get_child_by_key(self.tree.handle, self.id, encode(key))
        return default if node_id == YAML_NULL_ID else YAMLNode(self.tree, node_id)

    def __getitem__(self, key: Union[str, int]) -> "YAMLNode":
        """``node[key]`` looks up a direct child of a MAP by its string key;
        ``node[i]`` returns the ``i``-th direct child of a MAP or sequence.

        Only direct children are searched (the lookup is not recursive). Raises
        ``KeyError`` if no child has the given key -- test with ``key in node``
        if it may be absent -- and ``IndexError`` if the index is out of bounds.
        """
        if isinstance(key, int):
            return self.child(key)
        node_id = libparser().get_child_by_key(self.tree.handle, self.id, encode(key))
        if node_id == YAML_NULL_ID:
            raise KeyError(key)
        return YAMLNode(self.tree, node_id)

    def __contains__(self, key: str) -> bool:
        """Whether the MAP node has a direct child stored under ``key``. Only
        direct children are checked; the search is not recursive."""
        return libparser().get_child_by_key(
            self.tree.handle, self.id, encode(key)) != YAML_NULL_ID

    def __len__(self) -> int:
        """The number of direct children: the number of key/value pairs in a MAP,
        or the number of elements in a sequence. Scalar nodes report 0."""
        return int(libparser().get_size(self.tree.handle, self.id))

    # A node is a thing, not a container to be tested for emptiness: without
    # this, __len__ would make an empty map or any scalar falsy.
    def __bool__(self) -> bool:
        return True

    def keys(self) -> list[str]:
        """The keys of a MAP node, in document order. Empty for sequence and
        scalar nodes."""
        if not self.is_map():
            return []
        lib = libparser()
        out = []
        for i in range(len(self)):
            child_id = lib.get_child_by_index(self.tree.handle, self.id, i)
            if child_id == YAML_NULL_ID:
                continue
            key = take_string(lib.get_node_key(self.tree.handle, child_id))
            if key is not None:
                out.append(key)
        return out

    def values(self) -> list["YAMLNode"]:
        """The children of this node, in document order."""
        return [self.child(i) for i in range(len(self))]

    def items(self) -> list[tuple[str, "YAMLNode"]]:
        """The ``(key, child)`` pairs of a MAP node, in document order."""
        return [(k, self[k]) for k in self.keys()]

    def __iter__(self) -> Iterator:
        """Iterate the node's children: a sequence yields its elements, a MAP
        yields its keys (as ``dict`` does; use :meth:`items` for pairs), and a
        scalar yields nothing."""
        if self.is_map():
            return iter(self.keys())
        if self.is_sequence():
            return iter(self.values())
        return iter(())

    def node_key(self) -> Union[str, None]:
        """The key under which this node is stored in its parent MAP, or ``None``
        if it has none. Sequence elements and the tree root have no key."""
        return take_string(libparser().get_node_key(self.tree.handle, self.id))

    # ─── reading values ────────────────────────────────────────────────────
    @property
    def value(self) -> str:
        """The scalar value of this node, as raw text.

        Raises ``ValueError`` if the node has no value (i.e. it is a MAP or a
        bare sequence). Use :meth:`as_int`, :meth:`as_float` or :meth:`as_bool`
        for typed values.
        """
        text = take_string(libparser().as_string(self.tree.handle, self.id))
        if text is None:
            raise ValueError("Node has no scalar value")
        return text

    def as_int(self) -> int:
        """The scalar value parsed as an ``int``. Raises if the node is not a
        scalar or its text is not a valid integer."""
        return int(self.value)

    def as_float(self) -> float:
        """The scalar value parsed as a ``float``. Raises if the node is not a
        scalar or its text is not a valid floating-point number."""
        return float(self.value)

    def as_bool(self) -> bool:
        """The scalar value parsed as a ``bool``.

        Accepts exactly the text ``true`` or ``false``; any other value (or a
        non-scalar node) raises ``ValueError``.
        """
        text = self.value
        if text == "true":
            return True
        if text == "false":
            return False
        raise ValueError(f"Cannot convert '{text}' to bool")

    def __int__(self):
        return self.as_int()

    def __float__(self):
        return self.as_float()

    # ─── modification ──────────────────────────────────────────────────────
    def add_scalar(self, value: str, key: str = None, index: int = None) -> "YAMLNode":
        """Add a scalar child to this node.

        Pass ``key`` for MAP parents; omit it for sequence elements. ``index``
        selects the 0-based position among the existing children; the default
        appends at the end.
        """
        node_id = libparser().add_scalar(
            self.tree.handle, self.id, encode(key), encode(value),
            YAML_NULL_ID if index is None else index)
        if node_id == YAML_NULL_ID:
            raise ValueError("Failed to add scalar")
        return YAMLNode(self.tree, node_id)

    def add_map(self, key: str = None, index: int = None) -> "YAMLNode":
        """Add an empty MAP child to this node.

        Pass ``key`` for MAP parents; omit it for sequence elements. ``index``
        selects the 0-based position among the existing children; the default
        appends at the end.
        """
        node_id = libparser().add_map(
            self.tree.handle, self.id, encode(key),
            YAML_NULL_ID if index is None else index)
        if node_id == YAML_NULL_ID:
            raise ValueError("Failed to add map")
        return YAMLNode(self.tree, node_id)

    def add_sequence(self, key: str = None, index: int = None) -> "YAMLNode":
        """Add an empty sequence child to this node.

        Pass ``key`` for MAP parents; omit it for sequence elements. ``index``
        selects the 0-based position among the existing children; the default
        appends at the end.
        """
        node_id = libparser().add_sequence(
            self.tree.handle, self.id, encode(key),
            YAML_NULL_ID if index is None else index)
        if node_id == YAML_NULL_ID:
            raise ValueError("Failed to add sequence")
        return YAMLNode(self.tree, node_id)

    def __setitem__(self, key: str, value: str) -> None:
        """``node[key] = value`` sets or updates a scalar value in a MAP node.

        If ``key`` already exists its value is updated; otherwise a new scalar
        child is appended.
        """
        lib = libparser()
        child_id = lib.get_child_by_key(self.tree.handle, self.id, encode(key))
        if child_id != YAML_NULL_ID:
            lib.set_scalar(self.tree.handle, child_id, encode(value))
        else:
            lib.add_scalar(self.tree.handle, self.id, encode(key), encode(value),
                           YAML_NULL_ID)

    def set_scalar(self, value: str) -> None:
        """Set or replace the scalar value of this node.

        Operates on an existing node in place; to set a value by key within a MAP
        (adding the key if absent), use ``node[key] = value`` instead.
        """
        libparser().set_scalar(self.tree.handle, self.id, encode(value))

    def set_key(self, key: str) -> None:
        """Set or replace the key under which this node is stored in its parent
        MAP. Only meaningful inside a MAP; sequence elements are keyless."""
        libparser().set_node_key(self.tree.handle, self.id, encode(key))

    def remove(self) -> None:
        """Remove this node, together with all of its descendants, from its
        parent.

        After removal the ``YAMLNode`` is stale and must not be used again.
        Intended for non-root nodes; the root has no parent to be removed from.
        """
        lib = libparser()
        parent_id = lib.get_parent(self.tree.handle, self.id)
        lib.remove_node(self.tree.handle, parent_id, self.id)

    def __delitem__(self, key: Union[str, int]) -> None:
        """``del node[key]`` removes a child and all of its descendants."""
        self[key].remove()

    # ─── deep copy ─────────────────────────────────────────────────────────
    def deep_copy_node(self, src: "YAMLNode") -> None:
        """Copy the type, key, value and all descendants of ``src`` into this
        node, overwriting whatever it previously held. Works across trees."""
        libparser().deep_copy_node(self.tree.handle, self.id,
                                   src.tree.handle, src.id)

    def deep_copy_children(self, src: "YAMLNode", index: int = None) -> None:
        """Copy all children of ``src`` into this node at the 0-based position
        ``index`` among the existing children; the default appends them at the
        end. Works across trees."""
        libparser().deep_copy_children(
            self.tree.handle, self.id, src.tree.handle, src.id,
            YAML_NULL_ID if index is None else index)

    def copy(self) -> "YAMLNode":
        """An independent deep copy of this node, in a tree of its own."""
        dst = create_empty_tree()
        dst.deep_copy_node(self)
        return dst

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo):
        return self.copy()

    # ─── emitting ──────────────────────────────────────────────────────────
    def to_yaml_string(self, exclude: Union[str, Iterable[str]] = ()) -> str:
        """Emit this node and its descendants as a YAML string.

        ``exclude`` is a key name, or a collection of key names, to be left out
        of the output: every MAP entry whose key matches, at any depth, is
        omitted along with its whole subtree. This is a display filter only --
        the node itself is never modified. For example, to print a lattice
        without the floor and reference subtrees::

            print(lat.to_yaml_string(exclude=["FloorP", "ReferenceP"]))
        """
        drop = _exclude_set(exclude)
        if not drop:
            return _emit_yaml(self)
        return _emit_yaml(_pruned_copy(self, drop))

    def write_yaml(self, filename, exclude: Union[str, Iterable[str]] = ()) -> bool:
        """Write the entire tree that contains this node to a YAML file.

        Returns ``True`` on success. ``exclude`` is a key name, or a collection
        of key names, to be left out of the file: every MAP entry whose key
        matches, at any depth, is omitted along with its whole subtree. The tree
        in memory is not modified. For example::

            lat.write_yaml("out.pals.yaml", exclude=["FloorP", "ReferenceP"])
        """
        drop = _exclude_set(exclude)
        # Prune a throw-away copy of the whole tree, then write that copy.
        target = self if not drop else _pruned_copy(self.root(), drop)
        return bool(libparser().write_file(target.tree.handle, encode(os.fspath(filename))))

    # ─── display ───────────────────────────────────────────────────────────
    def __repr__(self):
        if self.is_scalar():
            return f"YAMLNode(scalar: {self.value})"
        if self.is_map():
            return f"YAMLNode(map, {len(self)} keys)"
        if self.is_sequence():
            return f"YAMLNode(sequence, {len(self)} elements)"
        return "YAMLNode(unknown)"

    def __str__(self):
        """The node's contents as YAML, so that printing a node shows its full
        tree. ``repr`` gives the compact one-line form instead."""
        return self.to_yaml_string().rstrip()


# ─── internal helpers ────────────────────────────────────────────────────────

def _root_node(handle) -> YAMLNode:
    """Wrap a tree handle and return a node pointing to its root."""
    tree = YAMLTree(handle)
    return YAMLNode(tree, libparser().get_root(handle))


def _last_parse_error() -> str:
    """The most recent parse error recorded by the C library on this thread
    (empty when the last parse succeeded)."""
    ptr = libparser().yaml_last_parse_error()
    return "" if ptr is None else ptr.decode("utf-8")


def _exclude_set(exclude) -> set:
    """What the ``exclude`` argument of the emitters accepts: one key name, or a
    collection of them."""
    if isinstance(exclude, str):
        return {exclude}
    return {str(k) for k in exclude}


def _emit_yaml(node: YAMLNode) -> str:
    """Emit ``node`` and its descendants as YAML, without any filtering."""
    text = take_string(libparser().node_to_string(node.tree.handle, node.id))
    if text is None:
        raise ValueError("Cannot convert node to YAML string")
    return text


def _pruned_copy(node: YAMLNode, drop: set) -> YAMLNode:
    """An independent copy of ``node``, in a tree of its own, with every entry
    keyed by a name in ``drop`` removed. The caller's tree is left untouched."""
    pruned = node.copy()
    _prune_keys(pruned, drop)
    return pruned


def _prune_keys(node: YAMLNode, drop: set) -> YAMLNode:
    """Recursively remove, in place, every MAP entry of ``node`` whose key is in
    ``drop``."""
    if node.is_map():
        for key in node.keys():
            child = node[key]
            if key in drop:
                child.remove()
            else:
                _prune_keys(child, drop)
    elif node.is_sequence():
        for i in range(len(node)):
            _prune_keys(node.child(i), drop)
    return node


# ─── parsing ─────────────────────────────────────────────────────────────────

def parse_file(filename) -> YAMLNode:
    """Parse a YAML file from disk. Returns a node pointing to the tree root."""
    filename = os.fspath(filename)
    if not os.path.isfile(filename):
        raise FileNotFoundError(f"File not found: {filename}")
    handle = libparser().parse_file(encode(filename))
    if not handle:
        detail = _last_parse_error()
        raise PALSParseError(f"Failed to parse YAML file: {filename}" +
                             (f"\n  {detail}" if detail else ""))
    return _root_node(handle)


def parse_string(yaml_str: str) -> YAMLNode:
    """Parse a YAML string. Returns a node pointing to the tree root."""
    handle = libparser().parse_string(encode(yaml_str))
    if not handle:
        detail = _last_parse_error()
        raise PALSParseError("Failed to parse YAML string" +
                             (f"\n  {detail}" if detail else ""))
    return _root_node(handle)


def create_empty_tree() -> YAMLNode:
    """Create an empty MAP tree. Returns a node pointing to the root MAP."""
    return _root_node(libparser().create_empty_tree())


# ─── function forms of the node methods ──────────────────────────────────────
# The methods above are the primary spelling; these let a node operation be
# written as a call, which reads better in a pipeline and mirrors the C API.

def is_map(node: YAMLNode) -> bool:
    """Whether ``node`` is a MAP. See :meth:`YAMLNode.is_map`."""
    return node.is_map()


def is_sequence(node: YAMLNode) -> bool:
    """Whether ``node`` is a sequence. See :meth:`YAMLNode.is_sequence`."""
    return node.is_sequence()


def is_scalar(node: YAMLNode) -> bool:
    """Whether ``node`` is a scalar. See :meth:`YAMLNode.is_scalar`."""
    return node.is_scalar()


def get_parent(node: YAMLNode) -> YAMLNode:
    """The parent of ``node``. See :meth:`YAMLNode.parent`."""
    return node.parent()


def node_key(node: YAMLNode) -> Union[str, None]:
    """The key ``node`` is stored under. See :meth:`YAMLNode.node_key`."""
    return node.node_key()


def add_scalar(parent: YAMLNode, value: str, key: str = None,
               index: int = None) -> YAMLNode:
    """Add a scalar child to ``parent``. See :meth:`YAMLNode.add_scalar`."""
    return parent.add_scalar(value, key=key, index=index)


def add_map(parent: YAMLNode, key: str = None, index: int = None) -> YAMLNode:
    """Add an empty MAP child to ``parent``. See :meth:`YAMLNode.add_map`."""
    return parent.add_map(key=key, index=index)


def add_sequence(parent: YAMLNode, key: str = None, index: int = None) -> YAMLNode:
    """Add an empty sequence child to ``parent``. See :meth:`YAMLNode.add_sequence`."""
    return parent.add_sequence(key=key, index=index)


def set_scalar(node: YAMLNode, value: str) -> None:
    """Set the scalar value of ``node``. See :meth:`YAMLNode.set_scalar`."""
    node.set_scalar(value)


def set_key(node: YAMLNode, key: str) -> None:
    """Set the key of ``node``. See :meth:`YAMLNode.set_key`."""
    node.set_key(key)


def remove(node: YAMLNode) -> None:
    """Remove ``node`` from its parent. See :meth:`YAMLNode.remove`."""
    node.remove()


def deep_copy_node(dst: YAMLNode, src: YAMLNode) -> None:
    """Copy ``src`` into ``dst``. See :meth:`YAMLNode.deep_copy_node`."""
    dst.deep_copy_node(src)


def deep_copy_children(dst: YAMLNode, src: YAMLNode, index: int = None) -> None:
    """Copy the children of ``src`` into ``dst``. See
    :meth:`YAMLNode.deep_copy_children`."""
    dst.deep_copy_children(src, index=index)


def to_yaml_string(node: YAMLNode, exclude: Union[str, Iterable[str]] = ()) -> str:
    """Emit ``node`` as YAML. See :meth:`YAMLNode.to_yaml_string`."""
    return node.to_yaml_string(exclude=exclude)


def write_yaml(node: YAMLNode, filename, exclude: Union[str, Iterable[str]] = ()) -> bool:
    """Write ``node``'s tree to a file. See :meth:`YAMLNode.write_yaml`."""
    return node.write_yaml(filename, exclude=exclude)

# Parsing and writing YAML

PALSParserPy represents a parsed document as a tree of `YAMLNode` values. Each
node knows whether it is a map, a sequence, or a scalar, and supports the
standard Python collection idioms. The owning `YAMLTree` frees the underlying C
tree automatically when it is garbage-collected, so you never manage memory by
hand.

## Making the functions available

Everything documented here is exported from the top-level package, so one import
brings the whole API into scope:

```python
import palsparserpy as pp

root = pp.parse_file("config.pals.yaml")
```

Every tree operation is also a method on the node itself, so `pp.is_map(node)`
and `node.is_map()` are the same call written two ways. The rest of this guide
uses whichever reads better in context.

## Reading

Parse from a file or from a string. Both return a `YAMLNode` pointing at the
tree root:

| Function | Description |
| --- | --- |
| `parse_file(filename)` | Parse a YAML file from disk. |
| `parse_string(yaml_str)` | Parse YAML from a string. |
| `create_empty_tree()` | Create a new, empty MAP tree to build up from scratch. |
| `parse_and_expand_pals(filename, root_lattice="")` | Parse a PALS lattice file and return original, combined, expanded, full_expanded and adjunct views. |

```python
root = pp.parse_file("config.pals.yaml")
# or
root = pp.parse_string("""
server:
  host: localhost
  port: 8080
features:
  - auth
  - logging
""")
```

A malformed document raises `PALSParseError`, whose message carries the offending
line and column.

`parse_and_expand_pals` is PALS-specific: it returns a `Lattices` value holding
five independent tree views (`original`, `combined`, `expanded`,
`full_expanded`, `adjunct`), each freed on its own when garbage-collected.

## Querying the tree

Use these to inspect a node's kind, walk the tree, and read out its structure.
None of them modify the document.

### Kind checks

Every node is exactly one of map, sequence, or scalar:

| Function | Description |
| --- | --- |
| `is_map(node)` | `True` if `node` is a map (key/value pairs). |
| `is_sequence(node)` | `True` if `node` is a sequence (ordered list). |
| `is_scalar(node)` | `True` if `node` is a scalar leaf value. |

### Navigation and inspection

| Expression | Description |
| --- | --- |
| `node[key]` | The direct child of a map `node` under string `key` (`KeyError` if absent). |
| `node[index]` | The `index`-th child (0-based, negative counts from the end) of a map or sequence. |
| `node.get(key, default)` | The child under `key`, or `default` if there is none. |
| `key in node` | `True` if the map `node` has a direct child under `key`. |
| `len(node)` | Number of direct children (0 for a scalar). |
| `node.keys()` | The keys of a map `node`, in order, as a list of `str`. |
| `node.values()` | The children of `node`, in order. |
| `node.items()` | The `(key, child)` pairs of a map `node`. |
| `node.node_key()` | The key `node` is stored under in its parent, or `None`. |
| `node.child(i)` | The `i`-th child *by position*, which is how a single-key map entry is opened. |
| `get_parent(node)` | The parent node (`ValueError` if `node` is the root). |
| `iter(node)` | Sequences yield their elements; maps yield their keys, as `dict` does. |

```python
"features" in root                # True
len(root["features"])             # 2
root["server"].keys()             # ['host', 'port']

for item in root["features"]:
    print(item.value)             # auth, logging

for k, v in root["server"].items():
    print(k, "=>", v.value)       # host => localhost, port => 8080

parent = pp.get_parent(root["server"])   # back up to the root
```

Indexing is **0-based**, matching Python and the underlying C API. (The Julia
interface to the same library is 1-based, so an index carried across from a
PALSParserJ script needs one subtracted.)

### Reading scalar values

Convert a scalar leaf node to the Python type you want:

| Expression | Description |
| --- | --- |
| `node.value` | The scalar value as a `str` (the raw text). |
| `node.as_int()` | The scalar parsed as an `int`. |
| `node.as_float()` | The scalar parsed as a `float`. |
| `node.as_bool()` | The scalar parsed as a `bool` (`"true"` / `"false"`). |

```python
host = root["server"]["host"].value       # 'localhost'
port = root["server"]["port"].as_int()    # 8080
```

`int(node)` and `float(node)` work as well. There is deliberately no
`bool(node)` conversion: a node is always truthy, so `if node:` asks whether you
*have* a node rather than what it says.

## Building and editing

Create an empty document and add maps, sequences, and scalars to it. Each
builder returns the newly created child node:

| Expression | Description |
| --- | --- |
| `parent.add_scalar(value, key=None, index=None)` | Add a scalar child. |
| `parent.add_map(key=None, index=None)` | Add an empty map child. |
| `parent.add_sequence(key=None, index=None)` | Add an empty sequence child. |
| `node[key] = value` | Set (or create) a scalar child under `key`. |
| `node.set_scalar(value)` | Set or replace a node's scalar value in place. |
| `node.set_key(key)` | Set or replace the key a node is stored under. |
| `node.remove()`, `del parent[key]` | Remove a node and all its descendants. |
| `node.copy()` | An independent deep copy of `node` in a new tree. |
| `dst.deep_copy_node(src)` | Overwrite `dst` with a deep copy of `src`. |
| `dst.deep_copy_children(src, index=None)` | Copy all children of `src` into `dst`. |

Pass `key` for map children and omit it for sequence elements. `index` selects
the 0-based position among the existing children; it defaults to `None`, which
appends at the end, so you usually leave it out.

```python
root = pp.create_empty_tree()

server = root.add_map(key="server")
server["host"] = "localhost"
server["port"] = "8080"

features = root.add_sequence(key="features")
features.add_scalar("auth")
features.add_scalar("logging")
```

The `deep_copy_node` / `deep_copy_children` pair works across different trees,
so you can graft one subtree onto another.

## Writing

Serialize a node to a string or straight to disk:

| Expression | Description |
| --- | --- |
| `to_yaml_string(node, exclude=...)` | The node and its descendants as a YAML `str`. |
| `write_yaml(node, filename, exclude=...)` | Write the whole tree containing `node` to a file. |

```python
text = pp.to_yaml_string(root)      # YAML as a str -- print(root) does the same
pp.write_yaml(root, "out.pals.yaml")
```

Both take an `exclude` argument naming keys to leave out, which is handy for
printing or saving a large lattice without the bulky subtrees. Every MAP entry
with a matching key is dropped, at any depth, together with its subtree; the tree
in memory is not modified.

```python
print(pp.to_yaml_string(root, exclude=["FloorP", "ReferenceP"]))
print(pp.to_yaml_string(root, exclude="FloorP"))   # a single key needs no list
pp.write_yaml(root, "out.pals.yaml", exclude=["FloorP", "ReferenceP"])
```

See the [API Reference](../api.md) for the full list of functions and their
signatures.

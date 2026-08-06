"""Tests of the YAML tree wrapper: parsing, navigation, editing and emitting."""

import pytest

from palsparserpy import (PALSParseError, YAMLTree, create_empty_tree, is_map,
                          is_scalar, is_sequence, parse_file, parse_string,
                          to_yaml_string, write_yaml)


class TestNodeCreation:
    def test_create_empty_tree_returns_a_map_root(self):
        root = create_empty_tree()
        assert root.tree.handle
        assert is_map(root)
        assert len(root) == 0

    def test_add_map_and_add_sequence_create_typed_children(self):
        root = create_empty_tree()
        assert is_map(root.add_map(key="m"))
        assert is_sequence(root.add_sequence(key="s"))

    def test_add_scalar_creates_a_scalar_child(self):
        # Sequence elements are pure VAL nodes; keyed map entries are KEYVAL and
        # ryml's is_val() returns false for those -- use a seq element.
        root = create_empty_tree()
        seq = root.add_sequence(key="items")
        scalar = seq.add_scalar("hello")
        assert is_scalar(scalar)
        assert scalar.value == "hello"

    def test_invalid_tree_handle_raises(self):
        with pytest.raises(ValueError):
            YAMLTree(None)


class TestParsing:
    def test_parse_string_map(self):
        node = parse_string("name: Alice\nage: 30\nactive: true\n")
        assert is_map(node)
        assert "name" in node and "age" in node and "active" in node
        assert node["name"].value == "Alice"
        assert node["age"].as_int() == 30
        assert node["active"].as_bool() is True

    def test_parse_string_sequence(self):
        node = parse_string("- item1\n- item2\n- item3\n")
        assert is_sequence(node)
        assert len(node) == 3
        assert [n.value for n in node] == ["item1", "item2", "item3"]

    def test_parse_string_nested_structure(self):
        node = parse_string("users:\n"
                            "  - name: Alice\n"
                            "    age: 30\n"
                            "  - name: Bob\n"
                            "    age: 25\n")
        assert is_map(node)
        users = node["users"]
        assert is_sequence(users)
        assert len(users) == 2
        assert users[0]["name"].value == "Alice"
        assert users[0]["age"].as_int() == 30

    def test_parse_file_round_trip(self, tmp_path):
        path = tmp_path / "x.yaml"
        path.write_text("x: 1\ny: 2\n")
        node = parse_file(path)
        assert is_map(node)
        assert node["x"].as_int() == 1
        assert node["y"].as_int() == 2

    def test_parse_file_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_file("/nonexistent/path.yaml")

    def test_malformed_yaml_raises_a_pinpointed_error(self, tmp_path):
        # A sequence item missing its ':' is a syntax error. The C library used to
        # abort the whole process; it must now raise a catchable error whose
        # message names the offending line.
        with pytest.raises(PALSParseError, match="line"):
            parse_string("- cav\n    kind: RFCavity\n")

        path = tmp_path / "bad.yaml"
        path.write_text("a: 1\n  b: 2\n")
        with pytest.raises(PALSParseError):
            parse_file(path)


class TestTypeChecks:
    def test_a_node_is_exactly_one_of_the_three(self):
        root = parse_string("key: value")
        seq = parse_string("- 1\n- 2\n- 3\n")
        scalar = seq[0]   # pure VAL node; map entries (KEYVAL) fail is_scalar

        assert is_scalar(scalar) and not is_map(scalar) and not is_sequence(scalar)
        assert is_map(root) and not is_scalar(root) and not is_sequence(root)
        assert is_sequence(seq) and not is_scalar(seq) and not is_map(seq)


class TestAccessOperations:
    def test_map_access_by_key(self):
        node = parse_string("name: Alice\nage: 30")
        assert node["name"].value == "Alice"
        assert node["age"].as_int() == 30

    def test_sequence_access_by_index(self):
        node = parse_string("- 10\n- 20\n- 30\n")
        assert [n.as_int() for n in node] == [10, 20, 30]
        assert node[-1].as_int() == 30

    def test_key_not_found_raises(self):
        node = parse_string("name: Alice")
        with pytest.raises(KeyError):
            node["nonexistent"]
        assert node.get("nonexistent") is None

    def test_index_out_of_bounds_raises(self):
        node = parse_string("- 1\n- 2\n- 3\n")
        with pytest.raises(IndexError):
            node[10]

    def test_contains(self):
        node = parse_string("name: Alice\nage: 30")
        assert "name" in node
        assert "age" in node
        assert "nonexistent" not in node

    def test_len(self):
        assert len(parse_string("- 1\n- 2\n- 3\n- 4\n- 5\n")) == 5
        assert len(parse_string("a: 1\nb: 2\nc: 3")) == 3

    def test_iteration_and_items(self):
        node = parse_string("a: 1\nb: 2")
        assert list(node) == ["a", "b"]
        assert [(k, v.as_int()) for k, v in node.items()] == [("a", 1), ("b", 2)]
        assert [v.as_int() for v in node.values()] == [1, 2]

    def test_a_node_is_always_truthy(self):
        # __len__ alone would make an empty map, and every scalar, falsy.
        assert parse_string("{}")
        assert parse_string("- x\n")[0]


class TestTypeConversions:
    def test_string_conversion(self):
        assert parse_string("msg: hello world")["msg"].value == "hello world"

    def test_int_conversion(self):
        node = parse_string("a: 42\nb: -100")
        assert node["a"].as_int() == 42
        assert int(node["b"]) == -100

    def test_float_conversion(self):
        node = parse_string("x: 3.14159\ny: -2.5")
        assert node["x"].as_float() == pytest.approx(3.14159)
        assert float(node["y"]) == pytest.approx(-2.5)

    def test_bool_conversion(self):
        node = parse_string("t: true\nf: false")
        assert node["t"].as_bool() is True
        assert node["f"].as_bool() is False
        with pytest.raises(ValueError):
            parse_string("x: yes")["x"].as_bool()


class TestModificationOperations:
    def test_setitem_adds_and_updates_map_entries(self):
        root = create_empty_tree()
        root["name"] = "Alice"
        root["age"] = "30"
        root["pi"] = "3.14"
        root["active"] = "true"

        assert root["name"].value == "Alice"
        assert root["age"].as_int() == 30
        assert root["pi"].as_float() == pytest.approx(3.14)
        assert root["active"].as_bool() is True

        root["age"] = "31"                      # update an existing key
        assert root["age"].as_int() == 31

    def test_add_map_as_child_of_map(self):
        parent = create_empty_tree()
        child = parent.add_map(key="child")
        child["key"] = "value"

        assert is_map(parent["child"])
        assert parent["child"]["key"].value == "value"

    def test_set_scalar_updates_an_existing_scalar(self):
        root = create_empty_tree()
        scalar = root.add_scalar("initial", key="val")
        scalar.set_scalar("updated")
        assert scalar.value == "updated"

        scalar.set_scalar("42")
        assert scalar.as_int() == 42

        scalar.set_scalar("2.71828")
        assert scalar.as_float() == pytest.approx(2.71828)

        scalar.set_scalar("false")
        assert scalar.as_bool() is False

    def test_add_scalar_appends_to_sequences(self):
        root = create_empty_tree()
        seq = root.add_sequence(key="items")

        seq.add_scalar("item1")
        seq.add_scalar("item2")
        assert len(seq) == 2
        assert [n.value for n in seq] == ["item1", "item2"]

        seq.add_scalar("10", index=0)           # insert at front
        assert seq[0].value == "10"
        assert len(seq) == 3

    def test_add_map_as_sequence_element(self):
        root = create_empty_tree()
        seq = root.add_sequence(key="records")
        elem = seq.add_map()
        elem["key"] = "value"

        assert len(seq) == 1
        assert is_map(seq[0])
        assert seq[0]["key"].value == "value"

    def test_remove(self):
        root = create_empty_tree()
        root["keep"] = "yes"
        root["delete"] = "no"
        assert len(root) == 2

        root["delete"].remove()
        assert len(root) == 1
        assert "delete" not in root
        assert "keep" in root

    def test_delitem(self):
        root = create_empty_tree()
        root["keep"] = "yes"
        root["delete"] = "no"
        del root["delete"]
        assert "delete" not in root


class TestWriteAndEmit:
    def test_to_yaml_string(self):
        text = to_yaml_string(parse_string("name: Alice\nage: 30"))
        assert isinstance(text, str)
        for part in ("name", "Alice", "age", "30"):
            assert part in text

    def test_to_yaml_string_with_exclude(self):
        node = parse_string(
            "lat:\n"
            "  elements:\n"
            "    - name: q1\n"
            "      FloorP: {r: [1, 2, 3]}\n"
            "      L: 0.5\n"
            "      ReferenceP: {species: electron}\n"
            "  ReferenceP: {pc: 1e9}\n")

        text = to_yaml_string(node, exclude=["FloorP", "ReferenceP"])
        assert "FloorP" not in text
        assert "ReferenceP" not in text
        assert "electron" not in text           # the excluded subtrees go too
        assert "q1" in text
        assert "L" in text

        # A single key may be given as a bare string.
        text = to_yaml_string(node, exclude="FloorP")
        assert "FloorP" not in text
        assert "ReferenceP" in text

        # The default and an empty exclude list are the unfiltered output, and the
        # node itself is never modified.
        assert to_yaml_string(node, exclude=[]) == to_yaml_string(node)
        assert "FloorP" in to_yaml_string(node)

    def test_write_yaml_with_exclude(self, tmp_path):
        root = parse_string(
            "lat:\n"
            "  elements:\n"
            "    - name: q1\n"
            "      FloorP: {r: [1, 2, 3]}\n"
            "      L: 0.5\n"
            "  ReferenceP: {pc: 1e9}\n"
            "other: stuff\n")
        path = tmp_path / "out.yaml"

        # Writing from a non-root node still writes the whole tree.
        assert write_yaml(root["lat"], path, exclude=["FloorP", "ReferenceP"])
        text = path.read_text()
        assert "FloorP" not in text
        assert "ReferenceP" not in text
        assert "q1" in text
        assert "other" in text

        # The tree in memory keeps everything.
        assert "FloorP" in to_yaml_string(root)

    def test_write_yaml_to_file_and_read_back(self, tmp_path):
        root = create_empty_tree()
        root["test"] = "data"
        root["value"] = "123"
        path = tmp_path / "out.yaml"

        assert write_yaml(root, path)
        assert path.is_file()

        loaded = parse_file(path)
        assert loaded["test"].value == "data"
        assert loaded["value"].as_int() == 123


class TestDeepCopy:
    def test_copy_produces_an_independent_duplicate(self):
        original = parse_string("name: Alice\nage: 30")
        cloned = original.copy()

        assert cloned["name"].value == "Alice"
        assert cloned["age"].as_int() == 30

        # Mutating the clone must not affect the original.
        cloned["name"] = "Bob"
        assert cloned["name"].value == "Bob"
        assert original["name"].value == "Alice"

        # Trees are independent objects.
        assert cloned.tree.handle != original.tree.handle

    def test_deep_copy_node_copies_content_into_existing_node(self):
        src = parse_string("x: 10\ny: 20")
        dst = create_empty_tree()
        dst.deep_copy_node(src)

        assert dst["x"].as_int() == 10
        assert dst["y"].as_int() == 20

    def test_deep_copy_children_copies_children_into_existing_node(self):
        src = parse_string("a: 1\nb: 2")
        dst = create_empty_tree()
        dst["existing"] = "yes"

        dst.deep_copy_children(src)
        assert "existing" in dst
        assert "a" in dst and "b" in dst
        assert dst["a"].as_int() == 1

    def test_deep_copy_children_honors_an_explicit_index(self):
        src = parse_string("- a\n- b\n")            # children to graft in
        dst = parse_string("- x\n- y\n")            # existing sequence
        assert is_sequence(dst)

        dst.deep_copy_children(src, index=0)        # insert at the front
        assert len(dst) == 4
        assert [n.value for n in dst] == ["a", "b", "x", "y"]


class TestDisplay:
    def test_repr_names_the_node_type(self):
        assert "map" in repr(parse_string("a: 1\nb: 2"))
        assert "sequence" in repr(parse_string("- x\n- y\n"))
        assert "scalar" in repr(parse_string("- x\n- y\n")[0])

    def test_str_is_the_yaml_text(self):
        assert str(parse_string("a: 1\nb: 2")) == "a: 1\nb: 2"


class TestComplexScenarios:
    def test_build_nested_structure_programmatically(self):
        # {users: [{name: Alice, scores: [90, 85, 92]},
        #          {name: Bob,   scores: [88, 91, 87]}]}
        root = create_empty_tree()
        users = root.add_sequence(key="users")

        user1 = users.add_map()
        user1["name"] = "Alice"
        scores1 = user1.add_sequence(key="scores")
        for value in ("90", "85", "92"):
            scores1.add_scalar(value)

        user2 = users.add_map()
        user2["name"] = "Bob"
        scores2 = user2.add_sequence(key="scores")
        for value in ("88", "91", "87"):
            scores2.add_scalar(value)

        assert is_map(root)
        assert is_sequence(root["users"])
        assert len(root["users"]) == 2
        assert root["users"][0]["name"].value == "Alice"
        assert len(root["users"][0]["scores"]) == 3
        assert root["users"][0]["scores"][0].as_int() == 90

    def test_parse_and_modify_existing_yaml(self):
        node = parse_string("config:\n  timeout: 30\n  retries: 3\n")
        config = node["config"]

        config["timeout"] = "60"
        config["status"] = "enabled"

        assert node["config"]["timeout"].as_int() == 60
        assert node["config"]["status"].value == "enabled"
        assert node["config"]["retries"].as_int() == 3     # unchanged

    def test_round_trip_yaml_through_file(self, tmp_path):
        original = parse_string(
            "application:\n"
            "  name: MyApp\n"
            "  version: 1.0.0\n"
            "  features:\n"
            "    - authentication\n"
            "    - logging\n"
            "    - caching\n"
            "  settings:\n"
            "    debug: true\n"
            "    port: 8080\n")
        path = tmp_path / "out.yaml"

        assert write_yaml(original, path)
        loaded = parse_file(path)

        app = loaded["application"]
        assert app["name"].value == "MyApp"
        assert app["version"].value == "1.0.0"
        assert len(app["features"]) == 3
        assert app["features"][0].value == "authentication"
        assert app["settings"]["debug"].as_bool() is True
        assert app["settings"]["port"].as_int() == 8080


class TestEdgeCases:
    def test_empty_structures(self):
        empty_map = parse_string("{}")
        assert is_map(empty_map)
        assert len(empty_map) == 0

        empty_seq = parse_string("[]")
        assert is_sequence(empty_seq)
        assert len(empty_seq) == 0

    def test_special_string_values(self):
        assert parse_string('text: "true"')["text"].value == "true"
        assert parse_string('number: "123"')["number"].value == "123"

    def test_unicode_strings(self):
        assert parse_string("greeting: こんにちは")["greeting"].value == "こんにちは"
        assert parse_string("emoji: 🎉")["emoji"].value == "🎉"

    def test_multiline_strings(self):
        node = parse_string("description: |\n"
                            "  This is a\n"
                            "  multiline\n"
                            "  string\n")
        assert "multiline" in node["description"].value

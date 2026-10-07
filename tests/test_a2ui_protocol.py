"""Hermetic wire-contract tests for the two supported protocol versions."""
import unittest

from adkl.a2ui_protocol import reduce_operations


def message(version, kind, **body):
    return {"version": version, kind: {"surfaceId": "s", **body}}


class TestSurfaces(unittest.TestCase):
    def test_versioned_data_delete_semantics_and_component_replacement(self):
        for version in ("v0.9", "v1.0"):
            ops = [message(version, "createSurface", catalogId="test"),
                   message(version, "updateComponents", components=[
                       {"id": "root", "component": "Text", "text": "old"}]),
                   message(version, "updateComponents", components=[
                       {"id": "root", "component": "Text", "text": {"path": "/name"}}]),
                   message(version, "updateDataModel", value={"name": "new", "remove": 1}),
                   message(version, "updateDataModel", path="/remove",
                           **({"value": None} if version == "v1.0" else {}))]
            surface, = reduce_operations(ops)
            self.assertEqual(surface["dataModel"], {"name": "new"})
            self.assertEqual(len(surface["components"]), 1)
            self.assertEqual(surface["version"], version)
            self.assertEqual(surface["warnings"], [])

    def test_null_is_a_value_in_09_but_deletes_in_10(self):
        for version in ("v0.9", "v1.0"):
            surface, = reduce_operations([
                message(version, "createSurface", catalogId="test"),
                message(version, "updateDataModel", value={"x": 1}),
                message(version, "updateDataModel", path="/x", value=None)])
            self.assertEqual(surface["dataModel"], {"x": None} if version == "v0.9" else {})

    def test_10_inline_surface_and_json_pointer_array(self):
        surface, = reduce_operations([
            message("v1.0", "createSurface", dataModel={"a/b": [{"~x": "before"}]},
                    components=[{"id": "root", "component": "Text", "text": "hello"}]),
            message("v1.0", "updateDataModel", path="/a~1b/0/~0x", value="after")])
        self.assertEqual(surface["dataModel"], {"a/b": [{"~x": "after"}]})
        self.assertEqual(surface["root"], "root")

    def test_surfaces_never_share_ids_or_data_and_delete_removes_one(self):
        ops = []
        for sid in ("a", "b"):
            ops.append(message("v1.0", "createSurface", surfaceId=sid,
                               dataModel={"name": sid}, components=[
                                   {"id": "root", "component": "Text", "text": sid}]))
        surfaces = reduce_operations(ops)
        self.assertEqual([s["components"][0]["text"] for s in surfaces], ["a", "b"])
        ops.append(message("v1.0", "deleteSurface", surfaceId="a"))
        self.assertEqual([s["surfaceId"] for s in reduce_operations(ops)], ["b"])

    def test_tabs_modal_and_template_edges_are_not_forest_roots(self):
        surface, = reduce_operations([message("v1.0", "createSurface", components=[
            {"id": "root", "component": "Tabs", "tabs": [{"title": "One", "child": "modal"}]},
            {"id": "modal", "component": "Modal", "trigger": "trigger", "content": "list"},
            {"id": "trigger", "component": "Text", "text": "Open"},
            {"id": "list", "component": "List", "children": {"componentId": "item", "path": "/items"}},
            {"id": "item", "component": "Text", "text": {"path": "name"}},
        ])], legacy_roots=True)
        self.assertEqual(surface["roots"], ["root"])

    def test_invalid_messages_fail_explicitly(self):
        cases = [
            [{"version": []}],
            [message("v2.0", "createSurface")],
            [message("v0.9", "createSurface")],
            [message("v0.9", "createSurface", catalogId="test", dataModel={})],
            [message("v1.0", "updateComponents", components=[])],
            [message("v1.0", "createSurface"), message("v1.0", "createSurface")],
            [message("v1.0", "createSurface"), message("v0.9", "deleteSurface")],
            [message("v1.0", "createSurface"), message("v1.0", "updateDataModel")],
            [message("v1.0", "callRendererFunction")],
            [message("v1.0", "createSurface"), message("v1.0", "deleteSurface"),
             message("v1.0", "createSurface")],
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                reduce_operations(case)


if __name__ == "__main__":
    unittest.main()

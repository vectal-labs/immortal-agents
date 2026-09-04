#!/usr/bin/env python3
"""Tests for the cmux.json socketControlMode editor used by install.sh."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from immortal.core import cmux_config


def strip_jsonc(text):
    return re.sub(r"(^\s*//.*$)|(\s//[^\"]*$)", "", text, flags=re.M)


class PatchTests(unittest.TestCase):
    def check(self, text, expect_changed=True):
        out = cmux_config.patched(text)
        if not expect_changed:
            self.assertIsNone(out)
            return None
        self.assertIsNotNone(out)
        data = json.loads(strip_jsonc(out))
        self.assertEqual(data["automation"]["socketControlMode"], "automation")
        self.assertEqual(cmux_config.file_mode(out), "automation")
        return out

    def test_missing_file(self):
        self.check("")

    def test_already_automation(self):
        self.check('{\n  "automation": {\n    "socketControlMode": "automation"\n  }\n}\n', expect_changed=False)

    def test_replaces_cmuxonly_in_place(self):
        out = self.check('{\n  // keep\n  "automation": {\n    "socketControlMode": "cmuxOnly", // trailing\n    "socketPassword": ""\n  }\n}\n')
        self.assertIn("// keep", out)
        self.assertIn('"socketPassword": ""', out)

    def test_ignores_commented_template(self):
        text = '{\n  "schemaVersion": 1\n  //   "automation" : {\n  //     "socketControlMode" : "cmuxOnly",\n  //   }\n}\n'
        out = self.check(text)
        self.assertIn('//     "socketControlMode" : "cmuxOnly"', out)
        self.assertEqual(json.loads(strip_jsonc(out))["schemaVersion"], 1)

    def test_inserts_into_existing_block(self):
        self.check('{\n  "automation": {\n    "socketPassword": "x"\n  }\n}\n')

    def test_inserts_into_empty_multiline_block(self):
        self.check('{\n  "automation": {\n  }\n}\n')

    def test_replaces_empty_inline_block(self):
        self.check('{\n  "automation": {},\n  "schemaVersion": 1\n}\n')

    def test_adds_block_when_absent(self):
        out = self.check('{\n  "schemaVersion": 1,\n  "sidebar": { "showWorkspaceDescription": false }\n}\n')
        self.assertFalse(json.loads(strip_jsonc(out))["sidebar"]["showWorkspaceDescription"])

    def test_file_mode_none_when_unmanaged(self):
        self.assertIsNone(cmux_config.file_mode('{\n  // "socketControlMode": "automation"\n}\n'))


class EnsureTests(unittest.TestCase):
    def test_ensure_writes_backup_then_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cmux.json")
            with open(path, "w") as fh:
                fh.write('{\n  "automation": { "socketControlMode": "cmuxOnly" }\n}\n')
            self.assertEqual(cmux_config.ensure(path), "written")
            self.assertEqual(cmux_config.ensure(path), "unchanged")
            baks = [f for f in os.listdir(tmp) if f.endswith(".bak")]
            self.assertEqual(len(baks), 1)
            self.assertIn("cmuxOnly", open(os.path.join(tmp, baks[0])).read())

    def test_ensure_creates_file_without_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "cmux.json")
            self.assertEqual(cmux_config.ensure(path), "written")
            self.assertEqual(os.listdir(os.path.join(tmp, "sub")), ["cmux.json"])
            self.assertEqual(cmux_config.file_mode(open(path).read()), "automation")


if __name__ == "__main__":
    unittest.main()

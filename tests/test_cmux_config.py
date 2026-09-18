"""Configuration updates preserve user settings and backups."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from immortal.core import cmux_config


class ConfigTests(unittest.TestCase):
    def test_supported_config_shapes_preserve_existing_content(self):
        cases = [
            ("", {}, []),
            ('{\n  // keep\n  "automation": {\n    "socketControlMode": "cmuxOnly", // trailing\n    "socketPassword": ""\n  }\n}\n', {}, ['// keep', '"socketPassword": ""']),
            ('{\n  "schemaVersion": 1\n  //   "automation" : {\n  //     "socketControlMode" : "cmuxOnly",\n  //   }\n}\n', {"schemaVersion": 1}, ['//     "socketControlMode" : "cmuxOnly"']),
            ('{\n  "automation": {\n    "socketPassword": "x"\n  }\n}\n', {}, ['"socketPassword": "x"']),
            ('{\n  "automation": {\n  }\n}\n', {}, []),
            ('{\n  "automation": {},\n  "schemaVersion": 1\n}\n', {"schemaVersion": 1}, []),
            ('{\n  "schemaVersion": 1,\n  "sidebar": { "showWorkspaceDescription": false }\n}\n',
             {"schemaVersion": 1, "sidebar": {"showWorkspaceDescription": False}}, []),
        ]
        for text, preserved, comments in cases:
            with self.subTest(text=text):
                output = cmux_config.patched(text)
                self.assertIsNotNone(output)
                data = json.loads(re.sub(r'(^\s*//.*$)|(\s//[^\"]*$)', '', output, flags=re.M))
                self.assertEqual(data["automation"]["socketControlMode"], "automation")
                self.assertEqual(cmux_config.file_mode(output), "automation")
                for key, value in preserved.items():
                    self.assertEqual(data[key], value)
                for comment in comments:
                    self.assertIn(comment, output)

    def test_already_managed_and_commented_settings(self):
        self.assertIsNone(cmux_config.patched('{\n  "automation": {\n    "socketControlMode": "automation"\n  }\n}\n'))
        self.assertIsNone(cmux_config.file_mode('{\n  // "socketControlMode": "automation"\n}\n'))

    def test_ensure_is_idempotent_and_backs_up_only_existing_files(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sub/cmux.json"
                original = '{\n  "automation": { "socketControlMode": "cmuxOnly" }\n}\n'
                if existing:
                    path.parent.mkdir()
                    path.write_text(original)
                self.assertEqual(cmux_config.ensure(str(path)), "written")
                self.assertEqual(cmux_config.ensure(str(path)), "unchanged")
                self.assertEqual(cmux_config.file_mode(path.read_text()), "automation")
                backups = list(path.parent.glob("*.bak"))
                self.assertEqual(len(backups), int(existing))
                if existing:
                    self.assertEqual(backups[0].read_text(), original)
                else:
                    self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()

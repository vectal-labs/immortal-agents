#!/usr/bin/env python3
"""Node + bb discovery for the launchd watcher."""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.core import bb_node, bb_runtime, logbook
from immortal.hosts import bb as host_bb

# Captured before tests redirect HOME. A leak writes this live install file.
LIVE_RUNTIME = Path.home() / ".immortal-agents" / "bb_runtime.json"

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.sh"

NODE_SH = """#!/bin/sh
if [ "$1" = "-p" ] && [ "$2" = "process.execPath" ]; then
  echo "{exec_path}"
  exit 0
fi
script="$1"
shift
exec /bin/bash "$script" "$@"
"""

BB_OK = """#!/usr/bin/env node
case "$1 $2" in
  "thread show") echo '{"thread":{"id":"thr_x","providerId":"codex","status":"error"}}' ;;
  "thread log") echo '[{"type":"provider/error","seq":1,"createdAt":1788185043065,"data":{"message":"Connection error"}}]' ;;
  "thread tell"|"thread retry") echo '{"ok":true,"delivery":"sent"}' ;;
  *) echo '[]' ;;
esac
exit 0
"""

BB_DOWN = """#!/usr/bin/env node
echo "Error: connect ECONNREFUSED 127.0.0.1:38886" >&2
exit 1
"""

BB_ENGINE = """#!/usr/bin/env node
echo "The engine \\"node\\" is incompatible with this module." >&2
exit 1
"""

BB_SYNTAX_HOST_DAEMON = """#!/usr/bin/env node
echo "SyntaxError: Unexpected token" >&2
echo "    at /Applications/bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app/host-daemon/dist/bb:1" >&2
exit 1
"""


def write_exe(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class Isolated(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.home.mkdir()
        self.state.mkdir()
        self.enterContext(mock.patch.object(logbook, "LOG_PATH", self.state / "watcher.log"))
        self.enterContext(mock.patch.object(logbook, "STATE_PATH", self.state / "state.json"))
        self.enterContext(mock.patch.object(logbook, "STATE_DIR", self.state))
        for key in ("BB_BIN", "BB_CLI", "IMMORTAL_NODE", "NODE_BIN", "IMMORTAL_PLIST",
                    "IMMORTAL_NODE_HINTS", "NVM_DIR", "FNM_DIR", "VOLTA_HOME"):
            os.environ.pop(key, None)
        os.environ["HOME"] = str(self.home)
        os.environ["WATCHER_STATE_DIR"] = str(self.state)
        os.environ["IMMORTAL_NODE_HINTS"] = ""
        os.environ["BB_BIN"] = str(self.root / "missing-bb")
        self.pybin = self.root / "pybin"
        write_exe(self.pybin / "python3", f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
        os.environ["PATH"] = f"{self.pybin}:/usr/bin:/bin"

    def tearDown(self):
        for key in list(os.environ):
            if key not in self._env:
                os.environ.pop(key, None)
        os.environ.update(self._env)
        self.tmp.cleanup()

    def write_node(self, path, exec_path=None):
        path = Path(path)
        text = NODE_SH.format(exec_path=exec_path or path)
        return write_exe(path, text)

    def write_bb(self, text=BB_OK, name="bb"):
        path = self.root / name
        os.environ["BB_BIN"] = write_exe(path, text)
        return os.environ["BB_BIN"]

    def recovery_target(self):
        thread = {"id": "thr_x", "providerId": "codex", "status": "error"}
        events = [{"type": "provider/error", "seq": 1, "createdAt": 1788185043065,
                   "data": {"message": "Connection error"}}]
        with mock.patch.object(host_bb, "_list_error_threads", return_value=[thread]), mock.patch.object(
            host_bb, "_thread_events", return_value=events
        ):
            return host_bb.list_targets()[0]


class DiscoverTests(Isolated):
    def test_homebrew_opt_preferred_over_cellar(self):
        cellar = self.root / "opt" / "homebrew" / "Cellar" / "node" / "26.0.0" / "bin" / "node"
        opt = self.root / "opt" / "homebrew" / "bin" / "node"
        self.write_node(cellar)
        opt.parent.mkdir(parents=True, exist_ok=True)
        opt.symlink_to(cellar)
        self.assertEqual(bb_node.prefer_stable(str(cellar)), str(opt))
        self.write_bb()
        os.environ["PATH"] = str(opt.parent) + ":/usr/bin:/bin"
        runtime = bb_runtime.discover_runtime()
        self.assertEqual(os.path.realpath(runtime["node"]), os.path.realpath(cellar))

    def test_homebrew_keeps_node22_when_default_is_newer(self):
        cellar22 = self.root / "opt" / "homebrew" / "Cellar" / "node@22" / "22.19.0" / "bin" / "node"
        cellar24 = self.root / "opt" / "homebrew" / "Cellar" / "node" / "24.0.0" / "bin" / "node"
        opt22 = self.root / "opt" / "homebrew" / "opt" / "node@22" / "bin" / "node"
        default = self.root / "opt" / "homebrew" / "bin" / "node"
        self.write_node(cellar22)
        self.write_node(cellar24)
        opt22.parent.mkdir(parents=True, exist_ok=True)
        default.parent.mkdir(parents=True, exist_ok=True)
        opt22.symlink_to(cellar22)
        default.symlink_to(cellar24)
        self.assertEqual(bb_node.prefer_stable(str(cellar22)), str(opt22))
        self.assertNotEqual(bb_node.prefer_stable(str(cellar22)), str(default))
        os.environ["IMMORTAL_NODE"] = str(cellar22)
        self.write_bb()
        runtime = bb_runtime.discover_runtime()
        self.assertEqual(os.path.realpath(runtime["node"]), os.path.realpath(cellar22))

    def test_nvm_uses_default_alias_not_newest(self):
        nvm = self.home / ".nvm"
        for ver in ("v20.1.0", "v22.1.0", "v24.1.0"):
            self.write_node(nvm / "versions" / "node" / ver / "bin" / "node")
        (nvm / "alias").mkdir(parents=True)
        (nvm / "alias" / "default").write_text("22\n")
        self.write_bb()
        runtime = bb_runtime.discover_runtime()
        self.assertIn("v22.1.0", runtime["node"])

    def test_nvm_major_alias_picks_numeric_latest(self):
        nvm = self.home / ".nvm"
        for ver in ("v22.9.0", "v22.19.0", "v24.1.0"):
            self.write_node(nvm / "versions" / "node" / ver / "bin" / "node")
        (nvm / "alias").mkdir(parents=True)
        (nvm / "alias" / "default").write_text("22\n")
        self.write_bb()
        runtime = bb_runtime.discover_runtime()
        self.assertIn("v22.19.0", runtime["node"])
        self.assertNotIn("v22.9.0", runtime["node"])
        self.assertNotIn("v24.", runtime["node"])

    def test_fnm_temp_multishell_resolves_to_stable(self):
        stable = self.write_node(self.home / ".local" / "share" / "fnm" / "aliases" / "default" / "bin" / "node")
        temp = self.root / "fnm_multishells" / "12345" / "bin" / "node"
        self.write_node(temp, exec_path=stable)
        os.environ["PATH"] = str(temp.parent) + ":/usr/bin:/bin"
        self.write_bb()
        runtime = bb_runtime.discover_runtime()
        self.assertEqual(runtime["node"], stable)
        self.assertFalse(bb_node.is_temp_path(runtime["node"]))

    def test_fnm_temp_exec_path_is_rejected(self):
        temp = self.root / "fnm_multishells" / "12345" / "bin" / "node"
        self.write_node(temp, exec_path=str(temp))
        os.environ["PATH"] = str(temp.parent) + ":/usr/bin:/bin"
        self.write_bb()
        with self.assertRaises(bb_runtime.BbRuntimeError) as ctx:
            bb_runtime.discover_runtime()
        self.assertIn("Node not found", str(ctx.exception))

    def test_volta_asdf_mise_which(self):
        self.write_bb()
        for name in ("volta", "asdf", "mise"):
            with self.subTest(name=name):
                node = self.write_node(self.root / name / "node")
                mgr = write_exe(
                    self.root / "mgr" / name / name,
                    f'#!/bin/sh\n[ "$1" = which ] && [ "$2" = node ] && echo "{node}" && exit 0\nexit 1\n',
                )
                os.environ["PATH"] = f"{Path(mgr).parent}:{self.pybin}:/usr/bin:/bin"
                runtime = bb_runtime.discover_runtime()
                self.assertEqual(runtime["node"], node)

    def test_missing_node(self):
        self.write_bb()
        status, msg = bb_runtime.diagnose(save=True)
        self.assertEqual(status, "error")
        self.assertIn("Install Node 22", msg)

    def test_incompatible_node(self):
        node = self.write_node(self.root / "node" / "node")
        os.environ["IMMORTAL_NODE"] = node
        self.write_bb(BB_ENGINE)
        status, msg = bb_runtime.diagnose(save=True)
        self.assertEqual(status, "error")
        self.assertIn("incompatible", msg.lower())

    def test_syntaxerror_host_daemon_path_is_not_server_down(self):
        node = self.write_node(self.root / "node" / "node")
        os.environ["IMMORTAL_NODE"] = node
        bb = self.write_bb()
        bb_runtime.save_runtime({"node": node, "bb": bb})
        self.write_bb(BB_SYNTAX_HOST_DAEMON)
        proc = mock.Mock(
            returncode=1,
            stdout="",
            stderr="SyntaxError: Unexpected token\n    at /Applications/bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app/host-daemon/dist/bb:1\n",
        )
        self.assertFalse(bb_runtime.server_down(proc))
        status, msg = bb_runtime.diagnose()
        self.assertEqual(status, "error")
        self.assertNotIn("app is not running", msg)
        self.assertIn("syntaxerror", msg.lower())
        self.assertIn("upgrade node", msg.lower())

    def test_path_with_spaces(self):
        node = self.write_node(self.root / "my node" / "bin" / "node")
        os.environ["IMMORTAL_NODE"] = node
        self.write_bb()
        runtime = bb_runtime.discover_runtime()
        self.assertEqual(runtime["node"], node)
        self.assertEqual(bb_runtime.diagnose()[0], "ok")

    def test_upgrade_rediscovers_after_saved_node_removed(self):
        old = self.write_node(self.root / "old" / "node")
        new = self.write_node(self.root / "new" / "node")
        bb = self.write_bb()
        plist = self.root / "watcher.plist"
        plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"CMUX_QUIET": "1"}}, fmt=plistlib.FMT_XML))
        os.environ["IMMORTAL_PLIST"] = str(plist)
        os.environ["IMMORTAL_NODE"] = new
        bb_runtime.save_runtime({"node": old, "bb": bb})
        os.unlink(old)
        stale, plist_before = bb_runtime.runtime_path().read_text(), plist.read_bytes()
        self.assertEqual(bb_runtime.diagnose()[0], "ok")
        self.assertEqual(bb_runtime.runtime_path().read_text(), stale)
        self.assertEqual(plist.read_bytes(), plist_before)
        self.assertEqual(host_bb.bb_json(["thread", "list"]), [])
        self.assertEqual(json.loads(bb_runtime.runtime_path().read_text())["node"], new)
        self.assertEqual(plist.read_bytes(), plist_before)

    def test_diagnose_oserror_does_not_persist(self):
        bad = write_exe(self.root / "bad" / "node", "")
        new = self.write_node(self.root / "new" / "node")
        bb = self.write_bb()
        plist = self.root / "watcher.plist"
        plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"CMUX_QUIET": "1"}}, fmt=plistlib.FMT_XML))
        os.environ["IMMORTAL_PLIST"] = str(plist)
        os.environ["IMMORTAL_NODE"] = new
        bb_runtime.save_runtime({"node": bad, "bb": bb})
        stale, plist_before = bb_runtime.runtime_path().read_text(), plist.read_bytes()
        self.assertEqual(bb_runtime.diagnose()[0], "ok")
        self.assertEqual(bb_runtime.runtime_path().read_text(), stale)
        self.assertEqual(plist.read_bytes(), plist_before)
        self.assertEqual(host_bb.resume(self.recovery_target()), "sent")
        self.assertEqual(json.loads(bb_runtime.runtime_path().read_text())["node"], new)
        self.assertEqual(plist.read_bytes(), plist_before)

    def test_bb_unavailable_is_skip(self):
        self.write_node(self.root / "node" / "node")
        os.environ["BB_BIN"] = str(self.root / "missing-bb")
        status, msg = bb_runtime.diagnose()
        self.assertEqual(status, "skip")
        self.assertIn("not installed", msg)

    def test_bb_app_closed_is_warn_and_saves_runtime(self):
        node = self.write_node(self.root / "node" / "node")
        os.environ["IMMORTAL_NODE"] = node
        self.write_bb(BB_DOWN)
        status, msg = bb_runtime.diagnose(save=True)
        self.assertEqual(status, "warn")
        self.assertIn("not running", msg)
        saved = bb_runtime.load_runtime()
        self.assertEqual(saved["node"], node)

    def test_scan_and_resume_share_command(self):
        node = self.write_node(self.root / "node" / "node")
        bb = self.write_bb()
        os.environ["IMMORTAL_NODE"] = node
        with mock.patch.object(host_bb, "run_bb", wraps=bb_runtime.run_bb) as run:
            scan = host_bb.bb_cmd(["thread", "list", "--json"])
            resume = host_bb.bb_cmd(["thread", "tell", "thr_x", "keep going", "--mode", "auto", "--json"])
            self.assertEqual(host_bb.bb_json(["thread", "list"]), [])
            self.assertEqual(host_bb.resume(self.recovery_target()), "sent")
        self.assertGreaterEqual(run.call_count, 2)
        self.assertEqual(scan[:2], [node, bb])
        self.assertEqual(resume[:2], [node, bb])
        self.assertEqual(scan[:2], resume[:2])

    def test_tell_timeout_is_sent_once(self):
        node = self.write_node(self.root / "node" / "node")
        bb = self.write_bb()
        bb_runtime.save_runtime({"node": node, "bb": bb})
        calls = []
        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if "tell" in cmd:
                raise subprocess.TimeoutExpired(cmd, 30)
            return original_run(cmd, **kwargs)

        with mock.patch.object(bb_runtime.subprocess, "run", fake_run):
            self.assertEqual(host_bb.resume(self.recovery_target()), "unknown")
        self.assertEqual(len([c for c in calls if "tell" in c]), 1)

    def test_does_not_write_live_runtime_file(self):
        # Regression: an unisolated get_runtime() wrote ~/.immortal-agents/bb_runtime.json
        # and a process-wide subprocess.run mock hid Node during discovery.
        before = LIVE_RUNTIME.read_bytes() if LIVE_RUNTIME.exists() else None
        node = self.write_node(self.root / "node" / "node")
        os.environ["IMMORTAL_NODE"] = node
        self.write_bb()
        host_bb.bb_cmd(["thread", "list", "--json"])
        host_bb.bb_json(["thread", "list"])
        self.assertEqual(host_bb.resume(self.recovery_target()), "sent")
        bb_runtime.get_runtime(refresh=True, persist=True)
        self.assertTrue((self.state / "bb_runtime.json").is_file())
        if before is None:
            self.assertFalse(LIVE_RUNTIME.exists())
        else:
            self.assertEqual(LIVE_RUNTIME.read_bytes(), before)

    def test_install_writes_plist_and_cli_status(self):
        node = self.write_node(self.root / "node" / "node")
        bb = self.write_bb()
        os.environ["IMMORTAL_NODE"] = node
        plist = self.root / "watcher.plist"
        plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"CMUX_QUIET": "1"}}, fmt=plistlib.FMT_XML))
        os.environ["IMMORTAL_PLIST"] = str(plist)
        self.assertEqual(bb_runtime.main(["install"]), 0)
        data = plistlib.loads(plist.read_bytes())
        self.assertEqual(data["EnvironmentVariables"]["IMMORTAL_NODE"], node)
        self.assertEqual(data["EnvironmentVariables"]["BB_BIN"], bb)
        plist_before, state_before = plist.read_bytes(), bb_runtime.runtime_path().read_text()
        self.assertEqual(bb_runtime.main(["check"]), 0)
        self.assertEqual(bb_runtime.main(["status"]), 0)
        self.assertEqual(plist.read_bytes(), plist_before)
        self.assertEqual(bb_runtime.runtime_path().read_text(), state_before)

    def test_watcher_env_drops_installer_pollution(self):
        node = self.write_node(self.root / "node" / "node")
        bb = self.write_bb()
        os.environ["IMMORTAL_NODE"] = node
        os.environ["BB_CLI"] = "/polluted/bb"
        os.environ["NODE_OPTIONS"] = "--require /no/such/pollute.js"
        os.environ["NVM_DIR"] = "/polluted/nvm"
        env = bb_runtime.watcher_env({"node": node, "bb": bb})
        self.assertNotIn("BB_CLI", env)
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertNotIn("NVM_DIR", env)
        self.assertEqual(env["BB_BIN"], bb)
        self.assertEqual(bb_runtime.diagnose()[0], "ok")
        child = subprocess.run(
            [sys.executable, "-c", "from immortal.hosts.bb import bb_json; print(bb_json(['thread', 'list']))"],
            capture_output=True, text=True, timeout=15,
            env={**env, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout.strip()), [])


class InstallerAndBackgroundTests(Isolated):
    def run_installer(self, *args, extra_env=None):
        source, dispatch = INSTALLER.read_text().rsplit('\ncase "$VERB" in\n', 1)
        mocks = r'''
STATE_DIR="$INSTALL_TEST_DIR/state"
PLIST="$INSTALL_TEST_DIR/LaunchAgents/watcher.plist"
REPO_DIR="$INSTALL_TEST_DIR"
uname() { echo Darwin; }
bootout_watcher() { :; }
launchctl() {
  case "$1" in
    bootstrap) return 0 ;;
    print) printf '\tpid = 123\n' ;;
    *) return 1 ;;
  esac
}
sleep() { :; }
setup_cmux() { :; }
setup_updates() { :; }
run_updates() { :; }
launch_hosts() { :; }
do_check() { probe_bb; }
'''
        script = self.root / "install.sh"
        (self.root / "watcher.py").write_text("# test\n")
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "LaunchAgents").mkdir(exist_ok=True)
        (self.root / "state" / "watcher.log").write_text('{"event": "probe", "online": true}\n')
        script.write_text(source + mocks + '\ncase "$VERB" in\n' + dispatch)
        env = {
            **os.environ,
            "INSTALL_TEST_DIR": str(self.root),
            "PYTHONPATH": str(REPO),
            "NO_COLOR": "1",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(script), *args],
            capture_output=True, text=True, timeout=15, env=env,
        )

    def test_install_check_status_with_nvm_node(self):
        node = self.write_node(self.home / ".nvm" / "versions" / "node" / "v22.19.0" / "bin" / "node")
        (self.home / ".nvm" / "alias").mkdir(parents=True)
        (self.home / ".nvm" / "alias" / "default").write_text("22\n")
        bb = self.write_bb()
        result = self.run_installer("--no-telemetry")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Watcher loaded and running", result.stdout)
        self.assertIn("bb: reachable", result.stdout)
        saved = json.loads((self.root / "state" / "bb_runtime.json").read_text())
        self.assertEqual(saved["node"], node)
        self.assertEqual(saved["bb"], bb)
        status = self.run_installer("status")
        self.assertEqual(status.returncode, 0, status.stdout)
        self.assertIn("Watcher loaded and running", status.stdout)
        self.assertIn("bb: reachable", status.stdout)
        check = self.run_installer("check")
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertIn("bb: reachable", check.stdout)
        plist = self.root / "LaunchAgents" / "watcher.plist"
        before_plist = plist.read_bytes()
        before_state = (self.root / "state" / "bb_runtime.json").read_text()
        self.assertEqual(self.run_installer("status").returncode, 0)
        self.assertEqual(self.run_installer("check").returncode, 0)
        self.assertEqual(plist.read_bytes(), before_plist)
        self.assertEqual((self.root / "state" / "bb_runtime.json").read_text(), before_state)

    def test_status_keeps_watcher_ok_when_bb_missing(self):
        os.environ["BB_BIN"] = str(self.root / "missing-bb")
        result = self.run_installer("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Watcher loaded and running", result.stdout)
        self.assertIn("bb: not installed", result.stdout)

    def test_install_missing_node_is_not_permission_denial(self):
        self.write_bb()
        result = self.run_installer("--no-telemetry")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Watcher loaded and running", result.stdout)
        self.assertIn("bb is not ready", result.stdout)
        self.assertIn("Node", result.stdout)
        self.assertNotIn("permission was denied", result.stdout)
        self.assertNotIn("All set.", result.stdout)

    def test_install_incompatible_node_is_not_permission_denial(self):
        os.environ["IMMORTAL_NODE"] = self.write_node(self.root / "node" / "node")
        self.write_bb(BB_ENGINE)
        result = self.run_installer("--no-telemetry")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bb is not ready", result.stdout)
        self.assertNotIn("permission was denied", result.stdout)
        self.assertNotIn("All set.", result.stdout)

    def test_install_closed_bb_is_not_all_set(self):
        os.environ["IMMORTAL_NODE"] = self.write_node(self.root / "node" / "node")
        self.write_bb(BB_DOWN)
        result = self.run_installer("--no-telemetry")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Open bb", result.stdout)
        self.assertNotIn("All set.", result.stdout)

    def test_reproduction_nvm_outside_homebrew_launchd_path(self):
        node = self.write_node(self.home / ".nvm" / "versions" / "node" / "v22.19.0" / "bin" / "node")
        (self.home / ".nvm" / "alias").mkdir(parents=True)
        (self.home / ".nvm" / "alias" / "default").write_text("22\n")
        bb = self.write_bb()
        launchd = {"HOME": str(self.home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        shebang = subprocess.run([bb, "thread", "list", "--json"], capture_output=True, text=True, env=launchd)
        self.assertNotEqual(shebang.returncode, 0)
        self.assertIn("node", shebang.stderr.lower())
        runtime = bb_runtime.discover_runtime()
        self.assertEqual(runtime["node"], node)
        explicit = subprocess.run([node, bb, "thread", "list", "--json"], capture_output=True, text=True, env=launchd)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(explicit.stdout.strip(), "[]")
        self.assertEqual(bb_runtime.diagnose(save=True)[0], "ok")
        self.assertEqual(host_bb.bb_json(["thread", "list"]), [])

    def test_background_launchd_env_lists_threads(self):
        node = self.write_node(self.root / "nvm-style" / "bin" / "node")
        bb = self.write_bb()
        bb_runtime.save_runtime({"node": node, "bb": bb})
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "WATCHER_STATE_DIR": str(self.state),
            "PYTHONPATH": str(REPO),
            "BB_BIN": bb,
            "IMMORTAL_NODE": node,
            "IMMORTAL_NODE_HINTS": "",
        }
        proc = subprocess.Popen(
            [sys.executable, "-c", "from immortal.hosts.bb import bb_json; print(bb_json(['thread', 'list']))"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            start_new_session=True,
        )
        out, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(json.loads(out.strip()), [])


if __name__ == "__main__":
    unittest.main()

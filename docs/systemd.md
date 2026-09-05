# systemd user service install

Do not enable this unit until you want the watcher running continuously.

Edit `systemd/immortal-agents.service` if the clone or Python lives somewhere else. Keep the watcher and terminal apps under the same Linux user.

tmux and WezTerm need no extra setup. Their CLIs must be on the service's `PATH`.

Kitty needs a remote-control socket because systemd runs outside its terminal. Add this to `~/.config/kitty/kitty.conf` and restart Kitty:

```text
allow_remote_control socket-only
listen_on unix:/tmp/immortal-agents-kitty
```

The socket must match `KITTY_LISTEN_ON` in the service file. When set via `kitty.conf`, Kitty appends `-<pid>` to the name (e.g. `/tmp/immortal-agents-kitty-12345`). The watcher resolves that suffix automatically. If you launch Kitty manually, use `kitty --listen-on unix:/tmp/immortal-agents-kitty` for the exact path instead.

Install and start:

```bash
mkdir -p ~/.config/systemd/user ~/.offline-agent-restart
cp systemd/immortal-agents.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now immortal-agents.service
```

Check it:

```bash
systemctl --user status immortal-agents.service
journalctl --user -u immortal-agents.service -f
```

Structured watcher logs are in `~/.offline-agent-restart/watcher.log`.

Uninstall:

```bash
systemctl --user disable --now immortal-agents.service
rm -f ~/.config/systemd/user/immortal-agents.service
systemctl --user daemon-reload
```

## Simulated outage

The service and `sim.py` must use the same `WATCHER_STATE_DIR`. The supplied unit uses `~/.offline-agent-restart`. The simulation never cuts Linux networking.

```bash
python3 sim.py on --minutes 3
python3 sim.py status
python3 sim.py off
```

Always run `off` when a test ends early. Otherwise, the flag expires on its own.

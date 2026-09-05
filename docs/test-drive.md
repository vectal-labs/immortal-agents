# Test drive

After installing, paste this whole prompt into your coding agent. It prepares
sessions on your Mac. You turn Wi-Fi off and back on yourself. Test sessions
use your normal model allowance. Allow about 30 minutes, sometimes longer.

```text
Test Immortal Agents on this Mac. Prepare a real outage test and report what
actually recovered. Follow these 7 steps. Do the setup yourself where your
tools allow it. Tell me exactly which clicks or login steps need me.

1. Inspect this Mac.

Find the installed checkout. Read its README support list. Check the installed
watcher's Python path, running code version, and ./install.sh status. Run
./install.sh check again after opening test apps. Missing optional apps are
skips, not failures. A stopped watcher or denied access to a selected host
must be resolved before the test.

Find installed bb, cmux, Terminal.app, Ghostty, and their signed-in agents.
Use actual versions and CLI help, not old experiment commands. Verify bb can
list/read threads and cmux can access panes. Resolve Automation, login, and
workspace trust prompts. Do not install apps, change providers, or bypass
permissions. If no supported combination can run, stop with the exact blocker.

Follow the README's current support list. Established starting points are
Claude Code or Cursor CLI inside bb, Claude Code or Pi in cmux, and Claude
Code in Terminal.app or Ghostty 1.3+. Cursor CLI outside bb is unsupported.
Treat Codex revival as unverified unless the README says otherwise.

2. Prepare fresh sessions.

You may open installed test apps and create fresh bb threads, cmux panes,
or Terminal/Ghostty windows. Use one working target per supported available
host, up to 4 targets. Prefer different supported agents where available.
Use local sessions on this Mac, each with a unique scratch folder and test
label. Leave my existing sessions alone.

Create a finished control: ask it to reply only "ready", then leave it idle.
Create another control waiting for my answer or a real approval, if safely
supported. Never approve that control. Record any control you cannot stage.
Save each session's host, agent, version, model, ID, folder, and role.

3. Start sustained work.

Send each target this task in its own scratch folder:
"Start immediately. Write a 30-section guide to the history of navigation.
For each numbered section, write at least 400 words, critique it, then write
an improved version. Save sections and revisions in separate numbered files.
Complete sections sequentially, using separate tool calls so each section
requires fresh model work. Use existing knowledge. Do not browse, install
anything, or ask questions. Do not simulate work with a script, sleeps, or
long local computation. If interrupted, inspect your files and continue from
the first unfinished section. Stop after section 30."

Verify targets are generating or making fresh model/tool calls, not finished,
waiting for permission, or only running a local command. If one finishes
while you prepare, give it another numbered batch. Recheck all targets
immediately before announcing the cut.

4. Prepare observation that works offline.

Save a run record under ~/.immortal-agents/test-drives/<timestamp>/ with
session IDs, folders, watcher PID/version, baseline states, and these
instructions. Start and verify a local recorder in a persistent terminal or
equivalent process that survives your agent turn ending. A bare background
command tied to your tool shell is insufficient.

Every 20 seconds, capture timestamps, watcher connectivity state, new watcher
log entries, and test-session activity/errors through local tools. Keep
evidence local and omit credentials. Record for up to 60 minutes or until
the report is complete, including after reconnection. If recording cannot be
verified, stop before the cut. Do not use simulation, edit watcher state, or
manually trigger recovery during this test.

5. Give me all offline instructions before the cut.

Explain that this disconnects the whole Mac and may interrupt other work.
The watcher may also revive unrelated agents that fail during the outage.
Identify Ethernet, tethering, and other routes that could keep this Mac
online. Tell me which ones I need to disconnect manually too.

When ready, tell me: "Turn Wi-Fi off manually now. Start a 10-minute timer,
keep the lid open and the Mac awake, then turn Wi-Fi back on and restore
other connections you disconnected. Restore it at 10 minutes even if agents
still show retrying. Do not wait for another message from me offline."

Never switch off networking yourself, disable network services, or run old
outage scripts. Do not extend the cut automatically. Explain that 10 minutes
is a bounded first test, not a guarantee agents will fail. Past runs saw Pi
take about 18 minutes to fail and Codex survive 92 minutes.

You may lose connection too. Give me the absolute run-record path and this
fallback before the cut: "After reconnecting, if this coordinating agent is
stuck, message it or start a new agent: Read <absolute run-record path> and
finish observing and reporting this test. Do not restart the test or manually
resume its target sessions." Fill in the real path.

6. Observe recovery without helping the targets.

Verify the watcher recorded a real outage of at least 120 seconds. If another
route stayed online, mark the outage invalid. Wi-Fi returning is not enough:
inspect apis_waiting, apis_ready, and apis_not_ready. DNS can delay recovery.

Let the installed watcher act. Do not type "keep going", press Escape,
restart targets, queue follow-ups, or invoke recovery functions yourself.
Record any human or coordinating-agent intervention; it prevents claiming
automatic recovery for that target.

Watch for fresh output and subsequent errors. Allow up to 15 minutes after
API readiness for recovery and rechecks. If APIs remain unavailable or the
recorder deadline arrives first, report that limit as inconclusive. Retrying
is not necessarily dead. Idle alone is not success: Cursor in bb can show
an error as its final message while its status is idle.

7. Report results and finish.

Give a short result for each tested host/agent:
- Watcher revived it: an in-window failure, a watcher prompt to that same
  session, and fresh non-error assistant output afterward were observed.
- Recovered itself: work resumed without a watcher prompt. Revival is untested.
- Remained stuck: a failed target did not resume within the observation window.
  Explain whether it was skipped, delivery failed, or no output followed.
- Inconclusive: it finished before the cut, kept retrying, lacked evidence,
  needed manual help, or the outage/setup did not exercise recovery.

Report whether finished and waiting controls stayed untouched. Unexpected
input into either is a failure. List unsupported/skipped combinations separately.
Match evidence by session ID and timestamp; do not count unrelated revives.
Report measured outage length, time from API readiness to first new output,
and any repeat failures or retries. resume_sent only proves input delivery.
revive_confirmed proves observed activity, not completion or causation alone.
revive_unconfirmed alone does not prove failure.

Save the report and evidence locally and give me their paths. Stop only your
recorder and test tasks after collecting results. Preserve logs. Leave the
watcher and my other sessions running. State exactly which combinations were
verified; do not claim this proves every agent or outage type.
```

Based on the [experiments](experiments/), especially
[stale DNS](experiments/0009-bb-wifi-cut-stale-dns.md),
[Pi recovery](experiments/0013-pi-cmux-wifi-cut.md),
[Cursor recovery](experiments/0015-bb-cursor-wifi-cut-proven.md), and
[Codex self-recovery](experiments/0017-codex-only-cut.md).
See [recovery outcomes](recovery-outcomes.md) for the log meanings.

# PROJECT RULES
- this is a quick and dirty prototype; do not over-engineer or bloat it
- other agents/humans work here; never undo changes you did not make
- never create new ADRs without David's explicit approval

# NETWORK SAFETY
- cutting the internet is allowed ONLY under ADR 0037 with David's fresh approval
- NEVER use `launchctl submit` (restarts the job forever)
- NEVER run `networksetup -setnetworkserviceenabled ... off` (persistent, greys out Wi-Fi)
- read `docs/incidents/0001-launchctl-submit-cut-loop.md` before touching anything network-related

# RESPONSE STYLE
- make all of your responses clear, and SUPER concise
- write in short sentences, in plain English
- always answer in short
- format your answers in nice, clean, readable Markdown
- First sentence answers what happened. No "Let me...", no restating the request, no closing recap, no offers of follow-up. State the result, then stop.
- In your responses, remove all mannered prose. Be direct & clear.

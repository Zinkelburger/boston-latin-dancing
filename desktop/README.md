# Boston Salsa desktop app

A tray app with the site logo that watches, runs and schedules the weekly
Claude review. The review itself is a systemd user timer, so it runs whether
or not the app is open.

```
bld-review.timer ──► bld-review.service ──► automation/claude_review.sh
 (Wed 12:00 Boston,                            1. automation/refresh.sh (scrape → publish → push)
  catches up after                             2. weekly_review.py prepare (checks → questions)
  sleep/power-off)                             3. claude -p automation/agent_prompt.md
                                                  (answers questions; 5 review tools + web search)
                                               4. weekly_review.py recheck (re-verify; follow-up
                                                  questions → one more agent pass)
                                               5. weekly_review.py finish → commit_pipeline.sh
                                                        │
                                                        ▼
                                  automation/logs/review-<timestamp>.jsonl
                                                        │ tailed every 2 s
                                                        ▼
                                           desktop/bld_tray.py (tray icon)
```

## Install

```bash
desktop/install.sh            # install or update (re-run after pulling changes)
desktop/install.sh --remove   # uninstall; settings and logs are kept
```

This installs the timer and service in `~/.config/systemd/user`, adds
"Boston Salsa" to the app menu, sets the tray app to start at login, and
starts it now. It needs `python3-pyside6` and the `claude` CLI, logged in.

## Using it

- **Tray icon.** It shows a green dot while a run is going and a red dot if
  the last run failed. Hover over it for the next run time or the current
  step. Click it to open the window. Right-click for Run now, Stop, and Open
  site.
- **Notifications** appear when a run starts and when it finishes. Click one
  to open the window.
- **Activity tab.** A live, readable view of the run: refresh output, what
  Claude says, each tool call (`▸`) and its result (`↳`), and the final turn
  count and cost. Use the "Run" picker to open earlier runs.
- **Last summary tab.** `automation/logs/last-agent-summary.md`, which Claude
  writes at the end of each run.
- **Schedule & settings tab.** Sets the days, time, catch-up behavior, model,
  and whether to scrape first. Saving writes
  `~/.config/systemd/user/bld-review.timer.d/schedule.conf` and
  `~/.config/bld-review/env`.

## What happens if the computer is off

The timer has `Persistent=true`, controlled by "run as soon as it's back". If
the machine was off or asleep at the scheduled time, the run starts shortly
after the next boot or wake. You get one catch-up run, no matter how many
were missed. Runs only happen while you're logged in. To run without a login
session, use `loginctl enable-linger $USER`.

## Things to know

- `refresh.sh` refuses to run on a dirty working tree. Commit or stash before
  the scheduled time. Otherwise the refresh step is skipped, and the review
  continues on last week's data. The run warns in the Activity log if it
  leaves the tree dirty itself.
- Claude runs with permission checks bypassed, so the guardrail is its tool
  list. It gets web search/fetch and the MCP review profile's five tools. It
  has no shell, no file edits and no git. Publishing and pushing are done by
  the script after it finishes. Any model works; Haiku 4.5 cleared a real
  worklist correctly in testing. Set `BLD_AGENT_MODEL` in the Settings tab.
- By hand: `systemctl --user start bld-review` (same as Run now), or
  `journalctl --user -u bld-review` for systemd's view of the run.

# agentlog

Read and search session logs from Claude Code, Codex, pi, and Hermes in one place.

When an agent stops mid-task (a rate limit, a crash, or lost context), its work is stuck in a transcript that no other agent can easily read. agentlog reads those transcripts. Another agent can then pick up the task with `agentlog resume`.

## Requirements

- Python 3 (the standard library only)
- No third-party packages

## Install

```sh
git clone https://github.com/micahp/agentlog.git
cd agentlog
ln -s "$PWD/agentlog" /usr/local/bin/agentlog
```

Check the install:

```sh
agentlog --version
```

## Where it looks

| Source | Location |
| --- | --- |
| claude | `~/.claude/projects/<slug>/<uuid>.jsonl` |
| codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| pi | `~/.pi/agent/sessions/<cwd slug>/<ISO time>_<uuid>.jsonl` |
| hermes | `~/.hermes/state.db` (read only; messages with `active = 1 OR compacted = 1`) |

## Supported versions

agentlog does not check source versions at run time. The table lists the versions it was tested against. Other versions may work. Check the output when you upgrade a source.

| Source | Tested version | Log location or store |
| --- | --- | --- |
| Claude Code | 2.1.321 | `~/.claude/projects/` JSONL |
| Codex CLI | 0.166.0 | `~/.codex/sessions/` rollout JSONL |
| pi | 1.3.0 | `~/.pi/agent/sessions/` JSONL |
| Hermes Agent | v0.22.2 (state schema 32) | `~/.hermes/state.db` |

Each release records the versions it was tested with. See the release notes.

## Commands

| Command | What it does |
| --- | --- |
| `list` | Recent sessions, newest first, with the first real prompt of each. |
| `status` | Which recent Claude, Codex, and pi sessions hit a rate limit, and whether the limit has reset. Hermes is not checked: it records no limit state. |
| `resume [ref]` | A handoff brief: the user's instructions in order, files touched, the last commands run, and how the session ended. |
| `show [ref]` | A full readable transcript. |
| `search REGEX` | Case-insensitive regex search across sessions. |

Run `agentlog <command> --help` for every option.

### Examples

```sh
# Ten most recent sessions
agentlog list --limit 10

# Sessions from one day
agentlog list --since 2026-10-14 --until 2026-10-14

# Sessions from the last 24 hours
agentlog list --since-hours 24

# Find where a phrase was used
agentlog search "prospective-2026-27" --since 2026-10-01

# Handoff brief for the newest session, written to a file
agentlog resume latest --out /tmp/brief.md

# Limit checks for the five newest sessions, as JSON
agentlog status -n 5 --json
```

## Session refs

A `ref` picks one session. It can be:

- `latest`: the newest session. The calling session is skipped, so it is safe to run from inside an agent.
- `prev` or `latest~N`: the session before the newest, or N sessions back.
- A session id, or a unique prefix of one. An ambiguous prefix lists the matches and exits with status 2.
- `source:id`, such as `codex:01a08`, to limit the match to one source. `source:latest` also works.
- A path to a log file.

## Filters

These apply to `list` and `search`:

| Option | Meaning |
| --- | --- |
| `--since-hours N` | Sessions updated in the last N hours. |
| `--since DATE` | Sessions updated at or after DATE. |
| `--until DATE` | Sessions updated before DATE. A date covers its whole day. |

DATE is `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`, in local time. Bad values are usage errors.

Other options that apply to every command:

| Option | Meaning |
| --- | --- |
| `--source` | `claude`, `codex`, `pi`, `hermes`, or `all` (the default). Comma-separated lists work. |
| `--project PATH` | Only sessions whose working directory starts with PATH. Default `/root`. Use `any` for no filter. |
| `--chars N` | Maximum characters per message block. Default 4000. `0` turns off truncation. |
| `--out FILE` | Write to FILE instead of standard output. |

## Rate limit detection

Detection looks at the structure of each log, not at the text. An agent that mentions a rate limit in prose is not treated as stopped by one.

- **codex**: a `task_complete` record with `usage_limit_exceeded`.
- **claude**: an API error record that no later reply followed. A session that hit an error and kept working is reported as OK.

`status` reports each session as RATE LIMITED, OK, or unknown.

## Notes

- Reads only. agentlog does not change any session file.
- A `resume` brief records what an agent attempted. It does not prove the work landed. The resuming agent should check the state on disk.

## Version history

- **0.3.0**: Hermes Agent support for `list`, `show`, `resume`, and `search`. Reads only messages that are active or compacted, so compressed history is not doubled. Not checked by `status`. Tested with Hermes Agent v0.22.2 (state schema 32).
- **0.2.0**: `--since` and `--until` date filters for `list` and `search`. `--version` flag. Bad date values are usage errors. Tested with Claude Code 2.1.321, Codex CLI 0.166.0, pi 1.3.0.
- **0.1.0**: the state before the date filters. Source versions were not recorded at release time.

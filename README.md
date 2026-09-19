# Docky

A clean terminal-based Docker manager for self-hosted servers. Monitor Compose projects, check image updates, safely upgrade containers, and clean up leftover data.

Zero dependencies: just Python 3.8+ and Docker.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/DimiKont/Docky/main/install.sh | sh
```

This installs to `~/.local/share/docky` and links `~/.local/bin/docky`. Pin a version with `DOCKY_REF=v0.1.0`. To uninstall, delete those two paths.

## Usage

```
docky <command> [target]
```

| Command | Description |
| --- | --- |
| `status` | Show Docker projects, containers, and system metrics |
| `top` | Live CPU and RAM usage mapped to your projects |
| `updates` | Check for available image updates |
| `upgrade [name] [--dry-run]` | Pull and recreate outdated containers, verifying health. Give a project name to upgrade just that one; `--dry-run` shows the plan without changing anything |
| `sweep` | Find and clear stopped containers and unused images |
| `orphans` | Find volumes belonging to deleted or renamed projects |
| `start` / `stop` / `restart` `<name\|all>` | Control a project or all of them |

Docky looks for Compose projects in `~/docker` (up to two levels deep).

## License

MIT

#!/usr/bin/env python3
# docky.py

import sys
from utils import Colors, color
import commands

def show_usage():
    print(f"\n{color('● DOCKY', Colors.BOLD + Colors.CYAN)}\n{color('Docker Server Manager', Colors.DIM)}\n")
    print(color("Usage:", Colors.BOLD) + "\n  docky <command> [target]\n")
    print(color("Commands:", Colors.BOLD))
    cmds = [
        ("status", "Show Docker projects, containers, and system metrics"),
        ("top", "Show real-time CPU and RAM usage mapped to your projects"),
        ("updates", "Check for available image updates"),
        ("upgrade", "Automatically pull and recreate outdated containers"),
        ("sweep", "Find and safely clear ghost data & unused images"),
        ("orphans", "Find volumes belonging to deleted or renamed projects"),
        ("start <name|all>", "Start a specific project or 'all'"),
        ("stop <name|all>", "Stop a specific project or 'all'"),
        ("restart <name|all>", "Restart a specific project or 'all'")
    ]
    for cmd, desc in cmds:
        print(f"  {color(cmd, Colors.CYAN):<28} {desc}")
    print()

def main():
    try:
        if len(sys.argv) < 2:
            return show_usage()
            
        cmd = sys.argv[1].lower()
        if cmd == "status": commands.cmd_status()
        elif cmd == "top": commands.cmd_top()
        elif cmd in ("updates", "update"): commands.cmd_updates(is_upgrade=False)
        elif cmd == "upgrade": commands.cmd_updates(is_upgrade=True)
        elif cmd == "sweep": commands.cmd_sweep()
        elif cmd == "orphans": commands.cmd_orphans()
        elif cmd in ("start", "stop", "restart"):
            if len(sys.argv) < 3:
                print(f"\n{color('! Missing target.', Colors.RED)}\nUsage: {color(f'docky {cmd} <project_name|all>', Colors.BOLD)}\n")
            else:
                commands.cmd_lifecycle(cmd, sys.argv[2])
        else:
            print(f"\n{color(f'! Unknown command: {cmd}', Colors.RED)}")
            show_usage()
            
    except KeyboardInterrupt:
        sys.stdout.write("\r\033[K\n")
        print(color("Aborted by user.", Colors.RED))
        sys.exit(130)

if __name__ == "__main__":
    main()
# commands.py
import sys
import time
import re
import concurrent.futures
from Docky.utils import Colors, color, run_command, get_system_metrics
import docker_api

def get_spinner(idx):
    chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    return chars[idx % len(chars)]

def container_indicator(state):
    state = state.lower()
    if state == "running": return color("●", Colors.GREEN)
    if state == "exited": return color("✕", Colors.RED)
    if state in ("restarting", "created"): return color("↻", Colors.YELLOW)
    return color("?", Colors.YELLOW)

def fetch_project_data(projects, executor):
    sys.stdout.write(f"\r{color('⠋', Colors.CYAN)} {color('Discovering containers...', Colors.DIM)}\033[K")
    sys.stdout.flush()
    futures = [executor.submit(docker_api.get_project_containers, p) for p in projects]
    idx = 0
    while not all(f.done() for f in futures):
        sys.stdout.write(f"\r{color(get_spinner(idx), Colors.CYAN)} {color('Discovering containers...', Colors.DIM)}\033[K")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.08)
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()
    return [{"project": p, "containers": f.result()} for p, f in zip(projects, futures)]

def cmd_status():
    projects = docker_api.find_projects()
    if not projects:
        print(f"\n{color('🐳 DOCKY', Colors.BOLD + Colors.CYAN)}\n\n{color('  No Docker Compose projects found.', Colors.YELLOW)}\n")
        return

    print()
    metrics = get_system_metrics()
    print(color("🐳 DOCKY", Colors.BOLD + Colors.CYAN) + color("  ·  Server Status", Colors.DIM))
    print(f"\n  💾 Storage : {metrics['disk_str']}\n  🐏 Memory  : {metrics['ram_str']}\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        project_data = fetch_project_data(projects, executor)
        
    total = sum(len(d["containers"]) for d in project_data)
    running = sum(1 for d in project_data for c in d["containers"] if c["state"].lower() == "running")

    for p_idx, data in enumerate(project_data):
        project, containers = data["project"], data["containers"]
        is_last_p = (p_idx == len(project_data) - 1)
        p_branch = "└─" if is_last_p else "├─"
        print(f"{p_branch} {color(project['name'], Colors.CYAN + Colors.BOLD)}")
        
        if not containers:
            print(f"{'   ' if is_last_p else '│  '}└─ {color('no containers', Colors.DIM)}")
            continue

        for c_idx, container in enumerate(containers):
            is_last_c = (c_idx == len(containers) - 1)
            c_branch = "└─" if is_last_c else "├─"
            prefix = f"{'   ' if is_last_p else '│  '}{c_branch} "
            print(f"{prefix}{container_indicator(container['state'])} {container['short_name']}")

    print(f"\n{color('●', Colors.GREEN)} {color(f'{running}/{total} containers running', Colors.DIM)}\n")

def cmd_updates(is_upgrade=False):
    projects = docker_api.find_projects()
    if not projects:
        print(f"\n{color('🐳 DOCKY', Colors.BOLD + Colors.CYAN)}\n\n{color('  No Docker Compose projects found.', Colors.YELLOW)}\n")
        return
    print()
    title = "Upgrading Containers" if is_upgrade else "Image Updates"
    print(color("🐳 DOCKY", Colors.BOLD + Colors.CYAN) + color(f"  ·  {title}", Colors.DIM) + "\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        project_data = [d for d in fetch_project_data(projects, executor) if d["containers"]]
        if not project_data: return

        unique_images = {c["image"] for d in project_data for c in d["containers"]}
        image_futures = {img: executor.submit(docker_api.check_image, img) for img in unique_images}

        total_cur, total_upd, total_upg, total_err = 0, 0, 0, 0
        errors = []

        for p_idx, data in enumerate(project_data):
            project, containers = data["project"], data["containers"]
            is_last_p = (p_idx == len(project_data) - 1)
            print(f"{'└─' if is_last_p else '├─'} {color(project['name'], Colors.CYAN + Colors.BOLD)}")

            for c_idx, container in enumerate(containers):
                is_last_c = (c_idx == len(containers) - 1)
                prefix = f"{'   ' if is_last_p else '│  '}{'└─' if is_last_c else '├─'} "
                name = container["short_name"]
                future = image_futures[container["image"]]

                idx = 0
                while not future.done():
                    sys.stdout.write(f"\r{prefix}{color(get_spinner(idx), Colors.CYAN)} {name:<20} {color('checking...', Colors.DIM)}\033[K")
                    sys.stdout.flush()
                    idx += 1; time.sleep(0.08)

                res = future.result()
                status = res["status"]
                if status == "current" and container["running_id"] and res.get("local_id"):
                    if container["running_id"] != res["local_id"]: status = "update"

                if status == "current":
                    total_cur += 1
                    print(f"\r{prefix}{color('✓', Colors.GREEN)} {name:<20} {color('up to date', Colors.DIM)}\033[K")
                elif status == "update":
                    if is_upgrade:
                        upg_future = executor.submit(docker_api.upgrade_service, project["compose"], container["service"])
                        while not upg_future.done():
                            sys.stdout.write(f"\r{prefix}{color(get_spinner(idx), Colors.CYAN)} {name:<20} {color('pulling & recreating...', Colors.YELLOW)}\033[K")
                            sys.stdout.flush()
                            idx += 1; time.sleep(0.08)
                        success, err_msg = upg_future.result()
                        if success:
                            total_upg += 1
                            print(f"\r{prefix}{color('✓', Colors.GREEN)} {name:<20} {color('upgraded', Colors.GREEN)}\033[K")
                        else:
                            total_err += 1; errors.append((name, err_msg))
                            print(f"\r{prefix}{color('!', Colors.RED)} {name:<20} {color('upgrade failed', Colors.RED)}\033[K")
                    else:
                        total_upd += 1
                        print(f"\r{prefix}{color('↑', Colors.YELLOW)} {name:<20} {color('update available', Colors.YELLOW)}\033[K")
                else:
                    total_err += 1; errors.append((container["image"], res["error"]))
                    print(f"\r{prefix}{color('!', Colors.RED)} {name:<20} {color('check failed', Colors.RED)}\033[K")

        print("\n" + "─" * 55)
        print(color(f"✓ {total_cur} up to date", Colors.GREEN))
        if is_upgrade and total_upg: print(color(f"✓ {total_upg} upgraded successfully", Colors.GREEN))
        elif total_upd: print(color(f"↑ {total_upd} update(s) available", Colors.YELLOW))
        if total_err: print(color(f"! {total_err} error(s)", Colors.RED))
        
        if errors:
            print("\n" + color("Check details:", Colors.BOLD))
            for item, err in errors: print(f"  {color('!', Colors.RED)} {item}\n    {color(err, Colors.DIM)}")
        
        if is_upgrade and total_upg > 0:
            print()
            sys.stdout.write(f"{color('⠋', Colors.CYAN)} {color('Cleaning up old images...', Colors.DIM)}\033[K")
            sys.stdout.flush()
            run_command(["docker", "image", "prune", "-f"])
            sys.stdout.write(f"\r{color('✓', Colors.GREEN)} {color('Cleaned up old images.', Colors.DIM)}\033[K\n")
        print()

def cmd_sweep():
    metrics_before = get_system_metrics()
    
    print(f"\n{color('🐳 DOCKY', Colors.BOLD + Colors.CYAN)} {color('  ·  The Ghost Finder', Colors.DIM)}\n")
    sys.stdout.write(f"{color('⠋', Colors.CYAN)} {color('Analyzing Docker filesystem...', Colors.DIM)}\033[K")
    sys.stdout.flush()

    succ, out, err = run_command(["docker", "system", "df"])
    if not succ:
        print(f"\r{color('!', Colors.RED)} {color('Failed to analyze Docker filesystem.', Colors.RED)}\n{err}")
        return

    data = {parts[0]: parts[4].split(" ")[0] for line in out.splitlines()[1:] if len(parts := re.split(r'\s{2,}', line.strip())) >= 5}
    
    _, out_det, _ = run_command(["docker", "system", "df", "-v"])
    unused_images, in_imgs = [], False
    for line in out_det.splitlines():
        if line.startswith("Images space usage:"): in_imgs = True; continue
        if line.startswith("Containers space usage:"): in_imgs = False; continue
        if in_imgs and line and not line.startswith("REPOSITORY"):
            p = re.split(r'\s{2,}', line.strip())
            if len(p) >= 8 and p[-1] == '0':
                unused_images.append(f"{'Untagged Layer' if p[0] == '<none>' else f'{p[0]}:{p[1]}'} {color(f'({p[-4]})', Colors.DIM)} - {p[2]}")

    _, out_c, _ = run_command(["docker", "ps", "-a", "-f", "status=exited", "-f", "status=created", "--format", "{{.Names}} - {{.Status}}"])
    stopped_containers = [line.strip() for line in out_c.splitlines() if line.strip()]

    print(f"\r\033[K{color('👻 Ghost Data Found:', Colors.BOLD)}\n")
    
    has_ghosts = False
    for key, label in [("Images", "Unused Images"), ("Containers", "Stopped Containers"), ("Local Volumes", "Orphaned Volumes"), ("Build Cache", "Build Cache")]:
        size = data.get(key, "0B")
        if size != "0B": has_ghosts = True
        print(f"  {color('•', Colors.DIM)} {label:<20} {color(size, Colors.YELLOW) if size != '0B' else color('Clean', Colors.GREEN)}")

    if not has_ghosts:
        print(f"\n{color('✓ Your system is completely clean! No ghosts found.', Colors.GREEN)}\n")
        return

    if stopped_containers or unused_images:
        print("\n" + "─" * 55 + "\n" + color("Inspection Details:", Colors.BOLD))
        if stopped_containers:
            print(color("\n  Stopped Containers to remove:", Colors.DIM))
            for c in stopped_containers: print(f"    {color('✕', Colors.RED)} {c}")
        if unused_images:
            print(color("\n  Unused Images to remove:", Colors.DIM))
            for i in unused_images: print(f"    {color('✕', Colors.RED)} {i}")
        print("\n" + "─" * 55 + "\n")

    print(color("Deep Sweep Overview:", Colors.DIM))
    print(color("  * Local volumes are kept safe. No app data will be deleted.", Colors.DIM) + "\n")
    
    try: choice = input(color("[?] Do you want to execute a deep sweep? (y/N): ", Colors.BOLD)).strip().lower()
    except EOFError: choice = 'n'
        
    if choice in ['y', 'yes']:
        sys.stdout.write(f"\n{color('⠋', Colors.CYAN)} {color('Sweeping ghosts...', Colors.DIM)}\033[K")
        sys.stdout.flush()
        succ, out, err = run_command(["docker", "system", "prune", "-a", "-f"])
        
        metrics_after = get_system_metrics()
        
        print(f"\r{color('✓', Colors.GREEN)} {color('Sweep complete!', Colors.GREEN)}\033[K")
        if match := re.search(r"Total reclaimed space: (.*)", out):
            freed = match.group(1)
            context = f"Disk usage dropped from {metrics_before['disk_pct']:.1f}% to {metrics_after['disk_pct']:.1f}%"
            print(f"  {color('Freed Space:', Colors.CYAN)} {color(freed, Colors.BOLD)} {color(f'({context})', Colors.DIM)}\n")
    else:
        print(f"\n{color('Sweep aborted. Your ghosts remain.', Colors.DIM)}\n")

def cmd_lifecycle(action, target):
    projects = docker_api.find_projects()
    if not projects: return
    
    if target.lower() != "all":
        projects = [p for p in projects if p["name"].lower() == target.lower()]
        if not projects:
            print(f"\n{color('!', Colors.RED)} {color(f'Project {target} not found.', Colors.RED)}\n")
            return
    else:
        print(f"\n{color(f'⚠️  WARNING: You are about to {action} ALL {len(projects)} projects.', Colors.YELLOW)}")
        try: choice = input(color("[?] Proceed? (y/N): ", Colors.BOLD)).strip().lower()
        except EOFError: choice = 'n'
        if choice not in ['y', 'yes']: return print(f"\n{color('Aborted.', Colors.DIM)}\n")

    print(f"\n{color('🐳 DOCKY', Colors.BOLD + Colors.CYAN)} {color(f'  ·  {action.capitalize()}ing Projects', Colors.DIM)}\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {p["name"]: executor.submit(lambda prj: run_command(["docker", "compose", "-f", str(prj["compose"]), action]), p) for p in projects}
        success_c, error_c = 0, 0
        for p_idx, project in enumerate(projects):
            name = project["name"]
            is_last = (p_idx == len(projects) - 1)
            prefix = f"{'└─' if is_last else '├─'} "
            future = futures[name]
            idx = 0
            while not future.done():
                sys.stdout.write(f"\r{prefix}{color(get_spinner(idx), Colors.CYAN)} {name:<20} {color(f'{action}ing...', Colors.YELLOW)}\033[K")
                sys.stdout.flush()
                idx += 1; time.sleep(0.08)
            succ, _, err = future.result()
            if succ:
                success_c += 1
                print(f"\r{prefix}{color('✓', Colors.GREEN)} {name:<20} {color(f'{action}ed', Colors.GREEN)}\033[K")
            else:
                error_c += 1
                print(f"\r{prefix}{color('!', Colors.RED)} {name:<20} {color(f'failed', Colors.RED)}\033[K\n  {color(err, Colors.DIM)}")
    print()
#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path


DOCKER_ROOT = Path.home() / "docker"

COMPOSE_FILENAMES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)


def run_command(command):
    """Run a command and return stdout."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


def find_projects():
    """Find Docker Compose projects inside ~/docker."""
    projects = []

    if not DOCKER_ROOT.exists():
        return projects

    for directory in sorted(DOCKER_ROOT.iterdir()):
        if not directory.is_dir():
            continue

        for filename in COMPOSE_FILENAMES:
            compose_file = directory / filename

            if compose_file.exists():
                projects.append(
                    {
                        "name": directory.name,
                        "path": directory,
                        "compose": compose_file,
                    }
                )
                break

    return projects


def get_project_containers(project):
    """Return containers belonging to a Compose project."""
    try:
        output = run_command(
            [
                "docker",
                "compose",
                "-f",
                str(project["compose"]),
                "ps",
                "-a",
                "--format",
                "{{.Name}}|{{.State}}|{{.Status}}|{{.Image}}",
            ]
        )
    except RuntimeError:
        return []

    containers = []

    for line in output.splitlines():
        parts = line.split("|", 3)

        if len(parts) != 4:
            continue

        containers.append(
            {
                "name": parts[0],
                "state": parts[1],
                "status": parts[2],
                "image": parts[3],
            }
        )

    return containers


def get_indicator(state):
    """Return a status indicator."""
    state = state.lower()

    if state == "running":
        return "●"

    if state == "exited":
        return "✕"

    if state in ("created", "restarting"):
        return "○"

    return "?"


def status():
    """Display Docker Compose projects and containers."""
    projects = find_projects()

    print()
    print("DOCKY — Docker Server")
    print("=" * 60)
    print()
    print(f" Docker root : {DOCKER_ROOT}")
    print(f" Projects    : {len(projects)}")
    print()

    if not projects:
        print(" No Docker Compose projects found.")
        print()
        return

    for project in projects:
        containers = get_project_containers(project)

        print(f" {project['name']}")
        print(f" {'─' * len(project['name'])}")

        if not containers:
            print("   No containers")
        else:
            for container in containers:
                indicator = get_indicator(container["state"])

                print(
                    f"   {indicator} "
                    f"{container['name']:<28} "
                    f"{container['status']}"
                )

        print()


def get_local_image_id(image):
    """Get the image ID currently used by Docker."""
    try:
        return run_command(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{.Id}}",
            ]
        )
    except RuntimeError:
        return None


def get_remote_image_id(image):
    """
    Pull the image metadata from the registry.

    This uses docker pull, but because the image is already present,
    Docker will only download layers if they are actually missing.
    No containers are restarted or recreated.
    """
    try:
        output = run_command(
            [
                "docker",
                "pull",
                image,
            ]
        )
    except RuntimeError:
        return None, "registry error"

    # Get the image ID after Docker has checked the registry.
    remote_id = get_local_image_id(image)

    return remote_id, output


def check_image_update(image):
    """
    Check whether a newer image exists.

    Returns:
        True  = update available
        False = up to date
        None  = unable to check
    """

    local_id = get_local_image_id(image)

    if not local_id:
        return None

    remote_id, result = get_remote_image_id(image)

    if not remote_id:
        return None

    return local_id != remote_id


def updates():
    """Check all Compose containers for available image updates."""
    projects = find_projects()

    print()
    print("DOCKY — Image Updates")
    print("=" * 70)
    print()

    if not projects:
        print(" No Docker Compose projects found.")
        print()
        return

    checked_images = {}
    results = []

    for project in projects:
        containers = get_project_containers(project)

        for container in containers:
            image = container["image"]

            # Avoid checking the same image more than once.
            if image not in checked_images:
                print(f" Checking {image}...", end="", flush=True)

                result = check_image_update(image)

                checked_images[image] = result

                if result is True:
                    print(" UPDATE")
                elif result is False:
                    print(" OK")
                else:
                    print(" ERROR")

            result = checked_images[image]

            results.append(
                {
                    "project": project["name"],
                    "container": container["name"],
                    "image": image,
                    "update": result,
                }
            )

    print()
    print("Results")
    print("─" * 70)
    print()

    updates_available = 0
    errors = 0

    for result in results:
        update = result["update"]

        if update is True:
            indicator = "↑"
            message = "UPDATE AVAILABLE"
            updates_available += 1

        elif update is False:
            indicator = "✓"
            message = "Up to date"

        else:
            indicator = "!"
            message = "Could not check"
            errors += 1

        print(
            f" {indicator} "
            f"{result['container']:<28} "
            f"{message}"
        )

    print()
    print("─" * 70)
    print()
    print(f" Images checked       : {len(checked_images)}")
    print(f" Containers checked   : {len(results)}")
    print(f" Updates available    : {updates_available}")

    if errors:
        print(f" Check errors         : {errors}")

    print()


def show_help():
    """Display command help."""
    print()
    print("DOCKY — Docker Server Manager")
    print()
    print("Usage:")
    print("  docky <command>")
    print()
    print("Commands:")
    print("  status    Show Docker Compose projects and containers")
    print("  updates   Check for available image updates")
    print("  help      Show this help message")
    print()


def main():
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()

    if command == "status":
        status()

    elif command == "updates":
        updates()

    elif command in ("help", "--help", "-h"):
        show_help()

    else:
        print()
        print(f"Unknown command: {command}")
        print("Run 'docky help' for available commands.")
        print()


if __name__ == "__main__":
    main()
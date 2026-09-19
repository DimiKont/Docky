# docker_api.py
import json
import re
import time
from pathlib import Path
from utils import run_command

DOCKER_ROOT = Path.home() / "docker"
COMPOSE_FILENAMES = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")

def find_projects():
    projects = []
    if not DOCKER_ROOT.exists():
        return projects

    directories_to_check = []
    for level_1 in DOCKER_ROOT.iterdir():
        if level_1.is_dir():
            directories_to_check.append(level_1)
            for level_2 in level_1.iterdir():
                if level_2.is_dir():
                    directories_to_check.append(level_2)

    for directory in directories_to_check:
        for filename in COMPOSE_FILENAMES:
            compose_file = directory / filename
            if compose_file.exists():
                if not any(p["path"] == directory for p in projects):
                    projects.append({"name": directory.name, "path": directory, "compose": compose_file})
                break
    projects.sort(key=lambda p: p["name"])
    return projects

def sanitize_project_name(name):
    """Compose's own normalisation: lowercase, keep only [a-z0-9_-]."""
    return re.sub(r"[^a-z0-9_-]", "", name.lower())

def resolve_project_names(project):
    """
    Every name Compose could have stamped on this project's volumes.

    The folder name alone isn't reliable: Compose sanitizes it
    ("My App" -> "myapp") and a `name:` key in the compose file (or
    COMPOSE_PROJECT_NAME) overrides it entirely. Ask Compose for the
    real name, and keep the sanitized folder name as a fallback so a
    failed lookup only ever widens what counts as "known".
    """
    names = {project["name"], sanitize_project_name(project["name"])}
    success, output, _ = run_command(
        ["docker", "compose", "-f", str(project["compose"]), "config", "--format", "json"]
    )
    if success and output:
        try:
            resolved = json.loads(output).get("name")
            if resolved:
                names.add(resolved)
        except json.JSONDecodeError:
            pass
    return names

def derive_short_name(name, project_name):
    prefix = f"{project_name}-"
    short_name = name[len(prefix):] if name.startswith(prefix) else name
    short_name = short_name[:-2] if short_name.endswith("-1") else short_name
    return short_name

def get_project_containers(project):
    success, output, _ = run_command(
        ["docker", "compose", "-f", str(project["compose"]), "ps", "-a", "--format", "{{.Name}}|{{.State}}|{{.Status}}|{{.Image}}|{{.Service}}"]
    )
    if not success:
        return []

    containers = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) < 4: continue
        name, state, status, image = parts[0], parts[1], parts[2], parts[3]

        short_name = derive_short_name(name, project["name"])
        service = parts[4] if len(parts) >= 5 and parts[4] and "{{" not in parts[4] else short_name

        inspect_success, inspect_output, _ = run_command(["docker", "container", "inspect", name, "--format", "{{.Image}}|{{.Config.Image}}"])
        running_id, config_image = "", image
        if inspect_success and inspect_output and "|" in inspect_output:
            running_id, config_image = inspect_output.split("|", 1)

        containers.append({"name": name, "short_name": short_name, "state": state, "status": status, "image": config_image, "running_id": running_id, "service": service})
    return containers

def get_containers_light(project):
    """
    Lightweight container listing for the live 'top' monitor.

    get_project_containers() above does one `docker container
    inspect` per container to resolve the real image reference --
    needed for update checks, but wasted work on a view that just
    refreshed a second ago. This version is just `docker compose
    ps` with two fields, so it stays cheap enough to re-run every
    refresh tick.
    """
    success, output, _ = run_command(
        ["docker", "compose", "-f", str(project["compose"]), "ps", "-a", "--format", "{{.Name}}|{{.State}}"]
    )
    if not success:
        return []

    containers = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        containers.append({
            "name": name,
            "short_name": derive_short_name(name, project["name"]),
            "state": state,
        })
    return containers

def fetch_stats():
    """
    One-shot snapshot of live resource usage for every running
    container on the host. This is a single `docker stats` call
    regardless of how many projects or containers exist -- it's
    what keeps each refresh tick of the live monitor cheap.
    """
    success, output, _ = run_command([
        "docker", "stats", "--no-stream", "--format",
        "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}",
    ])

    stats_map = {}
    if not success:
        return stats_map

    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        name, cpu, mem_usage, mem_pct, net, blk = parts[:6]
        stats_map[name] = {
            "cpu": cpu,
            "mem_used": mem_usage.split(" / ")[0],
            "mem_pct": mem_pct,
            "net": net,
            "blk": blk,
        }
    return stats_map

def get_local_image_id(image):
    succ, out, err = run_command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    return (out, None) if succ and out else (None, err or "failed to read id")

def check_image_via_pull(image):
    local_id_before, err = get_local_image_id(image)
    if not local_id_before:
        return {"status": "error", "local": None, "remote": None, "local_id": None, "error": f"local: {err}", "checked_via": "pull"}

    success, _, err = run_command(["docker", "pull", image])
    if not success:
        return {"status": "error", "local": local_id_before, "remote": None, "local_id": local_id_before, "error": f"registry: {err}", "checked_via": "pull"}

    local_id_after, err = get_local_image_id(image)
    status = "current" if local_id_before == local_id_after else "update"
    return {"status": status, "local": local_id_before, "remote": local_id_after, "local_id": local_id_after, "error": None, "checked_via": "pull"}

def check_image(image, allow_pull=True):
    """
    Compare local vs. registry digest. When digests can't be
    compared, the fallback is a real `docker pull`, which changes
    local state -- pass allow_pull=False (dry runs) to report
    "unknown" instead of mutating anything.
    """
    local_id, _ = get_local_image_id(image)
    
    succ, out, _ = run_command(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"])
    local_digest = None
    if succ and out:
        try:
            digests = json.loads(out)
            if digests:
                match = re.search(r"@sha256:([a-f0-9]{64})$", digests[0])
                if match: local_digest = f"sha256:{match.group(1)}"
        except json.JSONDecodeError: pass

    succ, out, _ = run_command(["docker", "buildx", "imagetools", "inspect", image])
    remote_digest = None
    if succ:
        for line in out.splitlines():
            match = re.match(r"^Digest:\s+(sha256:[a-f0-9]{64})$", line.strip())
            if match:
                remote_digest = match.group(1)
                break

    if not local_digest or not remote_digest:
        if not allow_pull:
            return {"status": "unknown", "local": local_digest, "remote": remote_digest, "local_id": local_id,
                    "error": "digests unavailable; verifying would require pulling", "checked_via": "none"}
        res = check_image_via_pull(image)
        if res.get("local_id") is None: res["local_id"] = local_id
        return res

    status = "current" if local_digest == remote_digest else "update"
    return {"status": status, "local": local_digest, "remote": remote_digest, "local_id": local_id, "error": None, "checked_via": "digest"}

def upgrade_service(compose_file, service_name):
    succ, _, err = run_command(["docker", "compose", "-f", str(compose_file), "pull", service_name])
    if not succ: return False, f"pull failed: {err}"
    succ, _, err = run_command(["docker", "compose", "-f", str(compose_file), "up", "-d", service_name])
    if not succ: return False, f"up failed: {err}"
    return True, ""

def verify_container_health(container_name, healthcheck_timeout=30, no_healthcheck_grace=6, poll_interval=1.0):
    """
    Confirm a container actually came back up cleanly after
    'up -d', rather than trusting the command's exit code alone --
    a container can be recreated successfully and still crash-loop
    or fail its healthcheck seconds later.

    Containers WITH a healthcheck get the full timeout, since some
    apps (databases warming up, etc.) legitimately take a while to
    report their first result. Containers WITHOUT one only get a
    short grace window -- most real startup failures (bad env var,
    port conflict, permission error) show up as an exit or a
    restart within the first couple seconds, so there's no reason
    to burn the full 30s waiting on something that was never going
    to report "healthy" in the first place.

    Returns:
        (ok: bool, detail: str)
    """
    def inspect_once():
        success, output, _ = run_command([
            "docker", "inspect", container_name,
            "--format",
            "{{.State.Status}}|{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        ])
        if not success or "|" not in output:
            return None
        status, restarts_str, health = output.split("|", 2)
        restarts = int(restarts_str) if restarts_str.isdigit() else 0
        return status, restarts, health

    reading = inspect_once()
    if reading is None:
        return False, "could not inspect container"

    status, baseline_restarts, health = reading
    has_healthcheck = health != "none"
    timeout = healthcheck_timeout if has_healthcheck else no_healthcheck_grace
    deadline = time.time() + timeout
    restarts = baseline_restarts

    while True:
        if status == "exited":
            return False, "container exited shortly after starting"

        if health == "unhealthy":
            return False, "healthcheck reports unhealthy"

        if restarts > baseline_restarts + 1:
            return False, f"container is restart-looping ({restarts} restarts)"

        if health == "healthy":
            return True, "healthcheck passed"

        if time.time() >= deadline:
            break

        time.sleep(poll_interval)
        reading = inspect_once()
        if reading is not None:
            status, restarts, health = reading

    if has_healthcheck:
        if health == "starting":
            return False, "healthcheck did not become healthy in time"
        return False, f"container did not stabilize (status: {status})"

    if status == "running":
        return True, "running (no healthcheck defined)"
    return False, f"container did not stabilize (status: {status})"

def get_volume_sizes():
    """
    Parse `docker system df -v` for real per-volume disk usage.

    This is the only place Docker exposes volume sizes at all --
    `docker volume ls` has no size field, since computing it means
    walking the filesystem. That's also why this is only ever
    called for an on-demand report like orphan detection, never on
    a refresh loop like 'top'.
    """
    success, output, _ = run_command(["docker", "system", "df", "-v"])
    if not success:
        return {}

    sizes = {}
    state = "seek_section"

    for line in output.splitlines():
        stripped = line.strip()

        if state == "seek_section":
            if stripped.startswith("Local Volumes"):
                state = "seek_header"
            continue

        if state == "seek_header":
            if stripped.startswith("VOLUME NAME"):
                state = "data"
            continue

        if state == "data":
            if not stripped:
                break
            columns = stripped.split()
            if len(columns) >= 3:
                sizes[columns[0]] = columns[-1]

    return sizes

def get_all_volumes():
    """
    Every volume on the host, with its Compose project label (if
    Compose created it) and on-disk size (if Docker has already
    computed one).

    A volume's "project" here is the value Compose stamped on it
    at creation time -- it's what lets orphan detection tell "this
    volume belongs to a project that still exists" apart from
    "this volume's project folder is gone."
    """
    success, output, _ = run_command(
        ["docker", "volume", "ls", "--format", "{{.Name}}|{{.Labels}}"]
    )
    if not success:
        return []

    volumes = []
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("|", 1)
        name = parts[0]
        labels_str = parts[1] if len(parts) > 1 else ""

        labels = {}
        for pair in labels_str.split(","):
            if "=" in pair:
                key, _, value = pair.partition("=")
                labels[key.strip()] = value.strip()

        volumes.append({
            "name": name,
            "project": labels.get("com.docker.compose.project"),
        })

    sizes = get_volume_sizes()
    for volume in volumes:
        volume["size"] = sizes.get(volume["name"])

    return volumes

def remove_volume(name):
    success, _, err = run_command(["docker", "volume", "rm", name])
    return success, err
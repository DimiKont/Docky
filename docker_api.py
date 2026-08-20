# docker_api.py
import json
import re
from pathlib import Path
from Docky.utils import run_command

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
        
        prefix = f"{project['name']}-"
        short_name = name[len(prefix):] if name.startswith(prefix) else name
        short_name = short_name[:-2] if short_name.endswith("-1") else short_name
        service = parts[4] if len(parts) >= 5 and parts[4] and "{{" not in parts[4] else short_name

        inspect_success, inspect_output, _ = run_command(["docker", "container", "inspect", name, "--format", "{{.Image}}|{{.Config.Image}}"])
        running_id, config_image = "", image
        if inspect_success and inspect_output and "|" in inspect_output:
            running_id, config_image = inspect_output.split("|", 1)

        containers.append({"name": name, "short_name": short_name, "state": state, "status": status, "image": config_image, "running_id": running_id, "service": service})
    return containers

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

def check_image(image):
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
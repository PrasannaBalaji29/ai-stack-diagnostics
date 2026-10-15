from pathlib import Path


def resolve_project_path(path: str) -> Path:
    project = Path(path).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"Not a directory: {project}")
    return project
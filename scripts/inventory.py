from collections import Counter
from pathlib import Path
import os

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_DIR / "config" / "roots.yaml"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def scan_root(
    root: Path,
    recursive: bool,
    extensions: set[str],
    include_hidden: bool,
    follow_symlinks: bool,
):
    if recursive:
        for current_dir, directories, filenames in os.walk(
            root,
            followlinks=follow_symlinks,
        ):
            if not include_hidden:
                directories[:] = [
                    name for name in directories if not name.startswith(".")
                ]

            for filename in filenames:
                if not include_hidden and filename.startswith("."):
                    continue

                path = Path(current_dir) / filename

                if path.is_symlink() and not follow_symlinks:
                    continue

                if path.suffix.lower() in extensions:
                    yield path
    else:
        for path in root.iterdir():
            if not path.is_file():
                continue

            if not include_hidden and path.name.startswith("."):
                continue

            if path.is_symlink() and not follow_symlinks:
                continue

            if path.suffix.lower() in extensions:
                yield path


def main():
    config = load_config()

    extensions = {
        extension.lower()
        for extension in config["extensions"]
    }

    scan_options = config.get("scan", {})
    include_hidden = scan_options.get("include_hidden", False)
    follow_symlinks = scan_options.get("follow_symlinks", False)

    grand_total = 0
    total_formats = Counter()

    print(f"Configuration: {CONFIG_PATH}")
    print()

    for root_config in config["roots"]:
        root_id = root_config["id"]
        root = Path(root_config["path"])
        recursive = root_config.get("recursive", True)

        if not root.exists():
            print(f"[UNAVAILABLE] {root_id}: {root}")
            continue

        if not root.is_dir():
            print(f"[NOT A DIRECTORY] {root_id}: {root}")
            continue

        try:
            files = list(
                scan_root(
                    root=root,
                    recursive=recursive,
                    extensions=extensions,
                    include_hidden=include_hidden,
                    follow_symlinks=follow_symlinks,
                )
            )
        except PermissionError as error:
            print(f"[PERMISSION ERROR] {root_id}: {error}")
            continue

        formats = Counter(path.suffix.lower() for path in files)
        grand_total += len(files)
        total_formats.update(formats)

        mode = "recursive" if recursive else "top level only"

        print(f"{root_id}:")
        print(f"  Path: {root}")
        print(f"  Mode: {mode}")
        print(f"  Files: {len(files)}")

        for extension, count in sorted(formats.items()):
            print(f"    {extension}: {count}")

        print()

    print("TOTAL")
    print(f"  Files: {grand_total}")

    for extension, count in sorted(total_formats.items()):
        print(f"    {extension}: {count}")


if __name__ == "__main__":
    main()

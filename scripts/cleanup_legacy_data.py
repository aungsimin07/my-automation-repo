"""ONE-TIME cleanup: removes retired files and directories from the data
branch that are no longer produced or read by any current script.

Safe to delete this file and its workflow after running once."""

import shutil
from pathlib import Path

from utils.logger import Logger

# files (relative to data/) that are no longer used by anything
LEGACY_FILES = [
    "data/channels.json",          # superseded by per-file storage under data/channels/
    "data/playlists.json",         # superseded by PLAYLIST_URL/TVG_IDS env vars
    "data/my-automated-playlist.m3u",  # superseded by channels.json embedded links
    "data/fixtures.json",          # unused legacy name, superseded by events.json
    "data/supported_leagues.json", # unused legacy name, superseded by leagues.json
]

# directories to wipe entirely and let regenerate fresh on next fetch_channels.py run
LEGACY_DIRS = [
    "data/channels",
]


def main():
    removed_files = []
    for rel_path in LEGACY_FILES:
        path = Path(rel_path)
        if path.exists():
            path.unlink()
            removed_files.append(rel_path)
            Logger.success(f"Removed file: {rel_path}")
        else:
            Logger.info(f"Not present, skipping: {rel_path}")

    removed_dirs = []
    for rel_path in LEGACY_DIRS:
        path = Path(rel_path)
        if path.exists():
            shutil.rmtree(path)
            removed_dirs.append(rel_path)
            Logger.success(f"Removed directory: {rel_path}")
        else:
            Logger.info(f"Not present, skipping: {rel_path}")

    if not removed_files and not removed_dirs:
        Logger.info("Nothing to clean up. Already clean.")
        return

    Logger.success(f"Cleanup complete: {len(removed_files)} file(s), {len(removed_dirs)} director(y/ies) removed.")
    Logger.warning(
        "Note: any events.json entries still referencing removed channel tvg-ids are now "
        "dangling until sync_channel_links.py runs next (fetch_channels.yml or queue_runner.yml)."
    )


if __name__ == "__main__":
    main()
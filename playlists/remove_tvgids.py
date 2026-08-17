import os

from playlist_store import load_playlists, save_playlists, find_by_id, parse_csv_list
from utils.logger import Logger


def main():
    playlist_id = os.getenv("PLAYLIST_ID", "").strip()
    remove_ids = parse_csv_list(os.getenv("TVG_IDS", ""))

    if not playlist_id:
        Logger.error("PLAYLIST_ID is required.", fatal=True)
    if not remove_ids:
        Logger.error("TVG_IDS is required (comma-separated).", fatal=True)

    playlists = load_playlists()
    target = find_by_id(playlists, playlist_id)
    if target is None:
        Logger.error(f"Playlist '{playlist_id}' not found in playlists.json.", fatal=True)

    existing = target.get("tvgIds", [])
    remove_set = set(remove_ids)
    remaining = [i for i in existing if i not in remove_set]
    removed = [i for i in existing if i in remove_set]

    if not removed:
        Logger.warning("None of the provided tvg-id(s) were found for this playlist. Nothing to remove.")
        return

    target["tvgIds"] = remaining
    save_playlists(playlists)
    Logger.success(f"Removed {len(removed)} tvg-id(s) from playlist '{playlist_id}': {', '.join(removed)}")


if __name__ == "__main__":
    main()

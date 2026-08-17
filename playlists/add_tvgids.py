import os

from playlist_store import load_playlists, save_playlists, find_by_id, parse_csv_list
from utils.logger import Logger


def main():
    playlist_id = os.getenv("PLAYLIST_ID", "").strip()
    new_ids = parse_csv_list(os.getenv("TVG_IDS", ""))

    if not playlist_id:
        Logger.error("PLAYLIST_ID is required.", fatal=True)
    if not new_ids:
        Logger.error("TVG_IDS is required (comma-separated).", fatal=True)

    playlists = load_playlists()
    target = find_by_id(playlists, playlist_id)
    if target is None:
        Logger.error(f"Playlist '{playlist_id}' not found in playlists.json.", fatal=True)

    existing = set(target.get("tvgIds", []))
    added = [i for i in new_ids if i not in existing]

    if not added:
        Logger.warning("All provided tvg-id(s) already exist for this playlist. Nothing to add.")
        return

    target["tvgIds"] = sorted(existing | set(added))
    save_playlists(playlists)
    Logger.success(f"Added {len(added)} tvg-id(s) to playlist '{playlist_id}': {', '.join(added)}")


if __name__ == "__main__":
    main()

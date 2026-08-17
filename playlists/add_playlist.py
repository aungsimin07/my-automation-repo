import os

from playlist_store import load_playlists, save_playlists, find_by_id, parse_csv_list
from utils.logger import Logger


def main():
    playlist_id = os.getenv("PLAYLIST_ID", "").strip()
    playlist_url = os.getenv("PLAYLIST_URL", "").strip()
    initial_tvg_ids = parse_csv_list(os.getenv("TVG_IDS", ""))

    if not playlist_id:
        Logger.error("PLAYLIST_ID is required.", fatal=True)
    if not playlist_url:
        Logger.error("PLAYLIST_URL is required.", fatal=True)

    playlists = load_playlists()
    if find_by_id(playlists, playlist_id):
        Logger.warning(f"Playlist '{playlist_id}' already exists in playlists.json. Skipping add.")
        return

    playlist_object = {
        "id": playlist_id,
        "url": playlist_url,
        "tvgIds": sorted(initial_tvg_ids),
        "last_sync_at": None,
    }

    playlists.append(playlist_object)
    save_playlists(playlists)
    Logger.success(f"Added playlist '{playlist_id}' with {len(initial_tvg_ids)} tvg-id(s).")


if __name__ == "__main__":
    main()

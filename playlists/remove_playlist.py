import os

from playlist_store import load_playlists, save_playlists, find_by_id
from utils.logger import Logger


def main():
    playlist_id = os.getenv("PLAYLIST_ID", "").strip()
    if not playlist_id:
        Logger.error("PLAYLIST_ID is required.", fatal=True)

    playlists = load_playlists()
    target = find_by_id(playlists, playlist_id)
    if target is None:
        Logger.error(f"Playlist '{playlist_id}' not found in playlists.json.", fatal=True)

    playlists = [p for p in playlists if p is not target]
    save_playlists(playlists)
    Logger.success(f"Removed playlist '{playlist_id}' ({target.get('url')}).")


if __name__ == "__main__":
    main()

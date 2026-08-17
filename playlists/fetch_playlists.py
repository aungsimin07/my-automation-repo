import re
from pathlib import Path

import requests

from playlist_store import load_playlists
from utils.logger import Logger

DOWNLOAD_DIR = Path("/tmp/playlist_downloads")
OUTPUT_FILE = Path("data/my-automated-playlist.m3u")
EXTINF_TVGID_PATTERN = re.compile(r'tvg-id="([^"]*)"')


def download_playlist(playlist_id: str, url: str):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / f"{playlist_id}.m3u"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        Logger.error(f"Failed to download playlist '{playlist_id}' from {url}: {e}")
        return None
    dest.write_text(resp.text, encoding="utf-8")
    return dest


def extract_matching_entries(file_path: Path, tvg_ids: set) -> list:
    """Return [(extinf_line, stream_url_line), ...] for entries whose
    tvg-id is in tvg_ids."""
    matches = []
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("#EXTINF"):
            i += 1
            continue

        id_match = EXTINF_TVGID_PATTERN.search(line)
        tvg_id = id_match.group(1) if id_match else None

        # the stream URL is the next non-empty, non-comment line
        j = i + 1
        url_line = None
        while j < len(lines):
            candidate = lines[j].strip()
            if candidate and not candidate.startswith("#"):
                url_line = candidate
                break
            j += 1

        if tvg_id and tvg_id in tvg_ids and url_line:
            matches.append((line, url_line))

        i = (j + 1) if url_line else (i + 1)

    return matches


def main():
    playlists = load_playlists()
    if not playlists:
        Logger.warning("No playlists found in playlists.json. Nothing to sync.")
        return

    all_matches = []
    for playlist in playlists:
        playlist_id = playlist.get("id")
        url = playlist.get("url")
        tvg_ids = set(playlist.get("tvgIds", []))

        if not tvg_ids:
            Logger.info(f"Playlist '{playlist_id}' has no tvgIds configured. Skipping.")
            continue

        Logger.info(f"Downloading playlist '{playlist_id}' from {url}")
        local_file = download_playlist(playlist_id, url)
        if local_file is None:
            continue

        matches = extract_matching_entries(local_file, tvg_ids)
        found_ids = {EXTINF_TVGID_PATTERN.search(m[0]).group(1) for m in matches}
        missing = tvg_ids - found_ids
        if missing:
            Logger.warning(
                f"Playlist '{playlist_id}': {len(missing)} tvg-id(s) not found in source: "
                f"{', '.join(sorted(missing))}"
            )

        Logger.success(f"Playlist '{playlist_id}': matched {len(matches)}/{len(tvg_ids)} tvg-id(s).")
        all_matches.extend(matches)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for extinf, stream_url in all_matches:
            f.write(f"{extinf}\n{stream_url}\n")

    Logger.success(f"Wrote {len(all_matches)} channel(s) to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

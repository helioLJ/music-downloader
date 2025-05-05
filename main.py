#!/usr/bin/env python3
"""
music_downloader.py – Download music tracks, playlists or albums from
YouTube and save them as MP3 files in the chosen directory (or flash drive).

🆕 What's New in v0.6 (May 6, 2025)
──────────────────────────────
• Smart distinction between specific songs and artists
• Better traceability: shows source line for each download
• Thread-safe concurrent processing
• Prevents duplicate songs from the same artist
• Prioritizes official versions
• Skips previously downloaded songs
• Configurable simultaneous downloads (--threads)
• Rich progress messages

Prerequisites:
  • Python 3.10+
  • yt-dlp ≥ 2024.04
      pip install --upgrade yt-dlp rich
  • ffmpeg installed and available in PATH

Quick usage:
 1. Create/edit **input.txt** (one item per line). Can contain:
    – Video/Playlist URLs ✨
    – Specific song name ("Artist - Title")
    – Artist/duo/band name (downloads the TOP --top songs)
 2. Connect flash drive (find the drive letter/mount path).
 3. Run:
       python music_downloader.py -i input.txt -o E:/Music --top 5

The script searches YouTube (`ytsearch{N}:<query>`) and downloads the best
audio, converts to MP3 192 kbps, adds metadata and saves as:
    <Title>.mp3
"""

from __future__ import annotations

import argparse
import concurrent.futures
import pathlib
import re
import sys
import threading
import time
from typing import List, Dict, Set, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

# Console for formatted output
console = Console()

# Base yt-dlp configuration
YTDLP_OPTS_BASE: dict = {
    "format": "bestaudio/best",
    "outtmpl": "%(title)s.%(ext)s",
    "quiet": True,
    "no_warnings": True,
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        },
        {"key": "FFmpegMetadata"},
    ],
}

# Patterns to identify undesired versions
LOWER_PRIORITY_PATTERNS = [
    r"\b(?:letra|playback|cover|karaoke)\b",
    r"#\d+",  # Avoids numbered versions like "#41"
    r"\b(?:instrumental|piano|tutorial)\b",
]

# Download tracking by input entry
DOWNLOAD_TRACKER: Dict[int, List[str]] = {}
DOWNLOAD_TRACKER_LOCK = threading.Lock()

# ────────────────────────────────────────────────────────────────────────────
# Utility functions
# ────────────────────────────────────────────────────────────────────────────


def parse_input(file_path: pathlib.Path) -> List[Tuple[int, str, bool]]:
    """Returns list of tuples (line_num, text, is_specific_song)."""
    entries: List[Tuple[int, str, bool]] = []
    line_num = 0

    for line in file_path.read_text(encoding="utf-8").splitlines():
        line_num += 1
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Determines if it's a specific song or an artist
        # A specific song has the format "Artist - Title"
        is_specific_song = bool(re.search(r"^.*?\s+[-–]\s+.*?$", line))

        entries.append((line_num, line, is_specific_song))
    return entries


def is_url(text: str) -> bool:
    """Check if the given text is a URL."""
    return bool(re.match(r"^(https?://|ytsearch\d*:)", text, flags=re.IGNORECASE))


def build_query(entry: str, top: int, is_specific_song: bool) -> str:
    """If not a URL, build a ytsearchN query.

    For specific songs, we search for only 1 result (the most relevant).
    For artists, we search for the N best results.
    """
    if is_url(entry):
        return entry

    # For specific songs, limit to 1 result
    if is_specific_song:
        return f"ytsearch1:{entry}"

    # For artists, use the N best results
    return f"ytsearch{top}:{entry}"


def get_existing_files(output_dir: pathlib.Path) -> Set[str]:
    """Returns a set of already downloaded music titles."""
    existing_titles = set()

    for file in output_dir.glob("*.mp3"):
        if file.is_file():
            # Use file name without extension as identifier
            title = file.stem
            existing_titles.add(title.lower())  # Normalize to lowercase for comparisons

    return existing_titles


def extract_artist_name(title: str) -> str:
    """Extracts the artist name from the song title."""
    # Attempt 1: Search for "Artist - Title" pattern
    artist_match = re.match(r"^(.*?)\s*[-–|]\s*", title)
    if artist_match:
        return artist_match.group(1).strip().lower()

    # Attempt 2: Search for "Feat" or "ft."
    feat_match = re.search(r"^(.*?)\s+(?:feat\.?|ft\.?|com)\s+", title, re.IGNORECASE)
    if feat_match:
        return feat_match.group(1).strip().lower()

    # If no clear pattern can be identified
    return title.lower()


def is_low_priority_version(title: str) -> bool:
    """Checks if the title indicates a lower priority version (lyrics, piano, etc.)."""
    lowered = title.lower()
    for pattern in LOWER_PRIORITY_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return True
    return False


def filter_and_sort_entries(entries: List[dict]) -> List[dict]:
    """Filters and sorts entries to prioritize official versions."""
    if not entries:
        return []

    # Filter out undesired versions if alternatives exist
    if len(entries) > 1:
        # First, separate versions by priority
        priority_entries = [
            e for e in entries if not is_low_priority_version(e.get("title", ""))
        ]

        # If there are priority versions, use only those
        if priority_entries:
            entries = priority_entries

    # Sort by preference criteria
    return sorted(
        entries,
        key=lambda e: (
            # Put webm and non-mp3 formats last
            "webm" in e.get("ext", ""),
            # Prioritize shorter videos (avoids complete albums when searching for a specific song)
            e.get("duration", 0) > 600,  # More than 10 minutes goes to the end
            # Tiebreaker by views (more views = higher priority)
            # Fixed to ensure we never have a NoneType
            -(e.get("view_count") or 0),  # Use 0 if view_count is None
        ),
    )


def add_download_to_tracker(line_num: int, title: str) -> None:
    """Adds a download to the tracker by input line."""
    with DOWNLOAD_TRACKER_LOCK:
        if line_num not in DOWNLOAD_TRACKER:
            DOWNLOAD_TRACKER[line_num] = []
        DOWNLOAD_TRACKER[line_num].append(title)


def download_single(
    line_num: int,
    entry: str,
    is_specific_song: bool,
    dest: pathlib.Path,
    top: int,
    progress: Progress,
    task_id: int,
    artist_tracker: Dict[str, Set[str]],
    existing_titles: Set[str],
    lock: threading.Lock,
) -> None:
    """Downloads (potentially multiple) tracks matching *entry*."""
    try:
        from yt_dlp import YoutubeDL  # late import - avoids cost in main thread
    except ImportError:
        progress.console.print(
            "[bold red]Error:[/] yt-dlp is not installed. Run 'pip install yt-dlp'."
        )
        progress.update(task_id, advance=1)  # Advance even with error
        return

    opts = YTDLP_OPTS_BASE.copy()
    opts["paths"] = {"home": str(dest)}

    # Add nicer print informing that the entry is being processed
    entry_type = "[magenta]Song[/]" if is_specific_song else "[blue]Artist[/]"
    progress.console.print(
        f"[bold cyan]Processing:[/] [yellow]({line_num})[/] {entry_type} [white]{entry}[/]"
    )

    try:
        with YoutubeDL(opts) as ydl:
            # First, extract information without downloading
            info_dict = ydl.extract_info(
                build_query(entry, top, is_specific_song), download=False
            )

            # If it's a playlist, process each item individually with deduplication
            if info_dict and "_type" in info_dict and info_dict["_type"] == "playlist":
                playlist_title = info_dict.get("title", "Unknown Playlist")
                entries = info_dict.get("entries", [])
                filtered_entries = []

                for item in entries:
                    if not item:
                        continue

                    title = item.get("title", "Unknown").lower()

                    # Skip if already downloaded previously
                    if title in existing_titles:
                        progress.console.print(
                            f"[yellow]⚠[/] [yellow]({line_num})[/] Skipping: [dim]{item.get('title', 'Unknown')}[/] (already exists)"
                        )
                        continue

                    # Extract artist from title
                    artist = extract_artist_name(item.get("title", ""))

                    # Use lock to check and update tracker
                    with lock:
                        # For specific song, allow only ONE song
                        # For artists, allow multiple songs from the same artist
                        if is_specific_song:
                            # For specific song, allow only one song per entry
                            # (artist doesn't matter)
                            if line_num not in artist_tracker:
                                artist_tracker[line_num] = set()

                            if len(artist_tracker[line_num]) == 0:
                                filtered_entries.append(item)
                                artist_tracker[line_num].add(title)
                        else:
                            # For artists, allow one song per artist in the playlist
                            if artist not in artist_tracker:
                                artist_tracker[artist] = set()

                            # If we haven't downloaded this specific video from this artist yet
                            if title not in artist_tracker[artist]:
                                filtered_entries.append(item)
                                # Mark as "to be downloaded" to prevent another thread from grabbing it
                                artist_tracker[artist].add(title)

                # Filter and sort entries to prioritize official versions
                filtered_entries = filter_and_sort_entries(filtered_entries)

                # Download filtered entries
                if filtered_entries:
                    # Show what will be downloaded
                    count = min(3, len(filtered_entries))
                    items_preview = ", ".join(
                        [
                            f"'{e.get('title', 'Unknown')[:30]}...'"
                            for e in filtered_entries[:count]
                        ]
                    )
                    if len(filtered_entries) > count:
                        items_preview += (
                            f" and {len(filtered_entries) - count} more item(s)"
                        )

                    progress.console.print(
                        f"[blue]ℹ[/] [yellow]({line_num})[/] Selected [bold cyan]{len(filtered_entries)}[/] of [cyan]{len(entries)}[/] items from playlist: {items_preview}"
                    )

                    # Download each item
                    for item in filtered_entries:
                        try:
                            ydl.process_ie_result(item, download=True)
                            title = item.get("title", "Unknown")
                            progress.console.print(
                                f"[green]✓[/] [yellow]({line_num})[/] Downloaded: [bold]{title}[/]"
                            )
                            add_download_to_tracker(line_num, title)
                        except Exception as item_exc:
                            progress.console.print(
                                f"[red]✗[/] [yellow]({line_num})[/] Failed to download an item from playlist: {str(item_exc)}"
                            )
                            # Remove from tracker if failed to try again later?
                            # No, as it could be a temporary failure. Keep it marked.

                    progress.console.print(
                        f"[green]✓[/] [yellow]({line_num})[/] Completed playlist: [bold]{playlist_title}[/] ([bold cyan]{len(filtered_entries)}[/] items)"
                    )
                else:
                    progress.console.print(
                        f"[yellow]⚠[/] [yellow]({line_num})[/] No new items to download in playlist: [bold]{playlist_title}[/]"
                    )

            # For normal search queries
            elif info_dict:
                entries = []

                # Check if it's a list of results or a single video
                if isinstance(info_dict, list):
                    entries = info_dict
                elif "entries" in info_dict:
                    entries = info_dict.get("entries", [])
                else:
                    # It's a single video, turn it into a list
                    entries = [info_dict]

                # Filter out those that already exist
                filtered_entries = []
                for item in entries:
                    if not item:
                        continue

                    title = item.get("title", "Unknown").lower()

                    # Skip if already downloaded previously
                    if title in existing_titles:
                        progress.console.print(
                            f"[yellow]⚠[/] [yellow]({line_num})[/] Skipping: [dim]{item.get('title', 'Unknown')}[/] (already exists)"
                        )
                        continue

                    filtered_entries.append(item)

                # Filter and sort entries to prioritize official versions
                if filtered_entries:
                    filtered_entries = filter_and_sort_entries(filtered_entries)

                    # For specific songs, limit to just 1 result
                    # For artists, allow all results
                    if is_specific_song and len(filtered_entries) > 1:
                        filtered_entries = filtered_entries[:1]  # Only the best result

                    # Process each result
                    downloads_count = 0
                    for item in filtered_entries:
                        title = item.get("title", "Unknown").lower()
                        artist = extract_artist_name(item.get("title", ""))

                        should_download = False
                        with lock:
                            if is_specific_song:
                                # For specific songs, control by line
                                if line_num not in artist_tracker:
                                    artist_tracker[line_num] = set()

                                # Allow only 1 download per line with specific song
                                if len(artist_tracker[line_num]) == 0:
                                    should_download = True
                                    artist_tracker[line_num].add(title)
                            else:
                                # For artists, control by artist
                                if artist not in artist_tracker:
                                    artist_tracker[artist] = set()

                                if title not in artist_tracker[artist]:
                                    should_download = True
                                    artist_tracker[artist].add(title)

                        # Download the video IF it was decided that it should be downloaded
                        if should_download:
                            try:
                                ydl.process_ie_result(item, download=True)
                                title = item.get("title", "Unknown")
                                progress.console.print(
                                    f"[green]✓[/] [yellow]({line_num})[/] Downloaded: [bold]{title}[/]"
                                )
                                add_download_to_tracker(line_num, title)
                                downloads_count += 1

                                # If it's a specific song, stop after the first download
                                if is_specific_song:
                                    break
                            except Exception as exc:
                                progress.console.print(
                                    f"[red]✗[/] [yellow]({line_num})[/] Failed to download: {str(exc)}"
                                )
                        else:
                            # Show appropriate message
                            if is_specific_song:
                                progress.console.print(
                                    f"[yellow]⚠[/] [yellow]({line_num})[/] Skipping: [dim]{item.get('title', 'Unknown')}[/] (already downloaded one song for this entry)"
                                )
                            else:
                                progress.console.print(
                                    f"[yellow]⚠[/] [yellow]({line_num})[/] Skipping: [dim]{item.get('title', 'Unknown')}[/] (artist already processed in this run)"
                                )

                    if downloads_count == 0:
                        progress.console.print(
                            f"[yellow]⚠[/] [yellow]({line_num})[/] No new songs downloaded for: [bold]{entry}[/]"
                        )
                else:
                    progress.console.print(
                        f"[yellow]⚠[/] [yellow]({line_num})[/] No results for: [bold]{entry}[/] (or already downloaded)"
                    )
            else:
                progress.console.print(
                    f"[yellow]⚠[/] [yellow]({line_num})[/] Could not find results for: [bold]{entry}[/]"
                )
    except Exception as exc:
        progress.console.print(
            f"[bold red]✗[/] [yellow]({line_num})[/] Failed to process [yellow]'{entry}'[/]: {exc}"
        )
    finally:
        progress.update(task_id, advance=1)


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Batch downloader for music from YouTube"
    )
    parser.add_argument(
        "-i", "--input", default="input.txt", help="Input file (default: input.txt)"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output",
        help="Destination directory (can be a flash drive)",
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=4,
        help="Simultaneous downloads (default: 4)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="Number of results to download when the input is an artist (default: 1)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download even of files that already exist",
    )

    args = parser.parse_args()

    # Display a header with the program title
    console.print(
        Panel.fit(
            "[bold yellow]Music Downloader[/] [dim]v0.6[/]",
            border_style="cyan",
            padding=(1, 10),
        )
    )

    input_path = pathlib.Path(args.input).expanduser()
    if not input_path.exists():
        console.print(
            f"[bold red]Error:[/] Input file '{input_path}' not found.", style="red"
        )
        sys.exit(1)

    output_dir = pathlib.Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = parse_input(input_path)
    if not entries:
        console.print(
            "[bold red]Error:[/] No valid entries found in input.txt.", style="red"
        )
        sys.exit(1)

    # Count how many are specific songs and how many are artists
    specific_songs_count = sum(1 for _, _, is_specific in entries if is_specific)
    artists_count = len(entries) - specific_songs_count

    # Identify files that already exist to avoid downloading again
    existing_titles = set() if args.force else get_existing_files(output_dir)
    if existing_titles and not args.force:
        console.print(
            f"[blue]ℹ[/] Found [bold cyan]{len(existing_titles)}[/] already downloaded files"
        )

    # Display configuration summary
    config_table = Table(title="Configuration", show_header=False, box=None)
    config_table.add_column("Parameter", style="cyan")
    config_table.add_column("Value", style="yellow")

    config_table.add_row("Input file", str(input_path))
    config_table.add_row("Output directory", str(output_dir))
    config_table.add_row("Threads", str(args.threads))
    config_table.add_row("Top results", str(args.top))
    config_table.add_row("Total entries", str(len(entries)))
    config_table.add_row("Specific songs", str(specific_songs_count))
    config_table.add_row("Artists", str(artists_count))
    config_table.add_row("Force mode", "Yes" if args.force else "No")

    console.print(config_table)
    console.print()

    start_time = time.time()

    # Dictionary to track artists already downloaded in this session (thread-safe)
    artist_tracker: Dict[str, Set[str]] = {}
    lock = threading.Lock()  # Lock to protect the artist_tracker

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Downloading", total=len(entries))

        # Use ThreadPoolExecutor for concurrent downloads
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = []
            for line_num, entry, is_specific_song in entries:
                futures.append(
                    pool.submit(
                        download_single,
                        line_num,
                        entry,
                        is_specific_song,
                        output_dir,
                        args.top,
                        progress,
                        task_id,
                        artist_tracker,
                        existing_titles,
                        lock,
                    )
                )

            # Wait for all downloads to finish
            concurrent.futures.wait(futures)

    elapsed = time.time() - start_time

    # Count how many files were added in this session
    new_files_count = 0
    if not args.force:
        current_titles = get_existing_files(output_dir)
        new_files_count = len(current_titles - existing_titles)

    # Display the report of songs downloaded by input line
    console.print()
    console.print("[bold cyan]Download summary by entry:[/]")
    download_table = Table(show_header=True)
    download_table.add_column("Line", style="cyan")
    download_table.add_column("Type", style="magenta")
    download_table.add_column("Entry", style="yellow")
    download_table.add_column("Downloaded Songs", style="green")

    # Sort by line for consistency
    for line_num, entry, is_specific_song in sorted(entries, key=lambda x: x[0]):
        downloaded = DOWNLOAD_TRACKER.get(line_num, [])
        num_downloads = len(downloaded)
        entry_type = "Song" if is_specific_song else "Artist"

        # Truncate the list if it's too long
        if num_downloads > 3:
            display_list = ", ".join(
                [
                    f"'{item[:40]}...'" if len(item) > 40 else f"'{item}'"
                    for item in downloaded[:3]
                ]
            )
            display_list += f" and {num_downloads - 3} more songs"
        elif num_downloads > 0:
            display_list = ", ".join([f"'{item}'" for item in downloaded])
        else:
            display_list = "[dim]None[/]"

        download_table.add_row(
            str(line_num), entry_type, entry, f"{num_downloads} ({display_list})"
        )

    console.print(download_table)
    console.print()
    console.print(
        Panel.fit(
            f"[bold green]✅ Download completed![/]\n\n"
            f"[cyan]Total time:[/] [yellow]{elapsed:.1f}s[/]\n"
            f"[cyan]New files:[/] [yellow]{new_files_count}[/]\n"
            f"[cyan]Files saved in:[/] [yellow]{output_dir}[/]",
            title="Result",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()

# Music Downloader

A powerful Python script for downloading music from YouTube. This tool allows you to easily download individual songs, songs from specific artists, or entire playlists, and convert them to high-quality MP3 files with proper metadata.

## 🌟 Features

- **Smart Music Recognition**: Distinguishes between specific songs and artists
- **Batch Processing**: Download multiple songs or artists from a simple text file
- **Quality Control**: Prioritizes official versions and high-quality audio
- **Duplicate Prevention**: Avoids downloading the same song twice
- **Concurrent Downloads**: Configurable thread count for faster downloads
- **Rich Progress Display**: Beautiful terminal-based progress bars and status updates
- **Metadata Integration**: Automatically adds proper metadata to downloaded MP3s

## 📋 Requirements

- Python 3.10 or higher
- Required Python packages (installed automatically with `uv`):
  - yt-dlp
  - rich
  - mutagen
  - pydub
- FFmpeg (must be installed separately and available in your PATH)

## 🔧 Installation

1. Install `uv` if you don't have it already:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Clone this repository:
   ```bash
   git clone https://github.com/helioLJ/music-downloader.git
   cd music-downloader
   ```

3. Set up the project:
   ```bash
   # Create virtual environment and install dependencies
   uv venv
   uv sync
   ```

4. Install FFmpeg for your platform:
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt install ffmpeg` (Ubuntu/Debian) or `sudo dnf install ffmpeg` (Fedora)

## 🚀 Quick Usage

1. Create an `input.txt` file with your download list (or use the included example file):
   ```
   # Specific songs (format: "Artist - Song Title")
   The Beatles - Let It Be
   Queen - Bohemian Rhapsody
   
   # Artists (get top songs)
   Michael Jackson
   Adele
   
   # Direct URLs
   https://www.youtube.com/watch?v=kMGECYPxtpA
   ```

2. Run the script:
   ```bash
   uv run python main.py -i input.txt -o ./Music --top 3
   ```

## 🛠️ Command Line Options

- `-i, --input`: Input file with songs/artists to download (default: `input.txt`)
- `-o, --output`: Output directory for downloaded files (default: `output`)
- `-t, --threads`: Number of simultaneous downloads (default: 4)
- `--top`: Number of songs to download per artist (default: 1)
- `--force`: Download files even if they already exist locally

## 📝 Input File Format

The input file can contain the following entries (one per line):

1. **Specific songs**: `Artist - Song Title` (downloads exactly that song)
2. **Artists**: Just the artist name (downloads their top N songs)
3. **URLs**: Direct YouTube links (videos or playlists)

Lines starting with `#` are treated as comments and ignored.

## 🔄 Example Workflow

```bash
# Create input.txt with your favorite songs & artists
echo "Coldplay - Viva La Vida" > input.txt
echo "Bruno Mars" >> input.txt

# Download to a portable device (e.g., USB drive)
uv run python main.py -o /media/usb/Music --top 5

# Check the results
ls -l /media/usb/Music
```

## 📱 Perfect for Portable Music

This tool is ideal for creating music collections on USB drives or other portable devices:
- Download your parent's favorite classics
- Create workout playlists
- Build genre-specific collections
- Prepare road trip music

## 📄 License

[MIT License](LICENSE) - Feel free to use, modify, and distribute this code.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📜 Changelog

### v0.6 (06-May-2025)
- Added smarter artist/song distinction
- Added tracking by input line
- Added thread-safe concurrent downloads
- Improved duplicate detection
- Added official version prioritization
- Skip previously downloaded songs
- Added configurable threads option
- Enhanced progress display with rich

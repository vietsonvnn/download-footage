# VidGrab — Lightweight Video Downloader

Giao diện đẹp, dễ dùng. Download video từ YouTube (và nhiều nguồn khác) về máy.

## Cài đặt (1 lần duy nhất)

### Yêu cầu
- **Python 3.8+** (đã có sẵn trên macOS, Windows cần cài từ python.org)
- **ffmpeg** (để merge video + audio cho chất lượng cao)

### Bước 1: Cài dependencies

```bash
pip install flask yt-dlp
```

### Bước 2: Cài ffmpeg

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
```bash
winget install ffmpeg
# hoặc tải từ https://ffmpeg.org/download.html
```

## Chạy app

```bash
cd vidgrab
python server.py
```

Trình duyệt sẽ tự mở tại `http://localhost:9123`

## Sử dụng

1. **Single download**: Paste 1 URL → nhấn Download (hoặc Ctrl/Cmd + Enter)
2. **Bulk download**: Paste nhiều URL (mỗi dòng 1 URL) → nhấn Download
3. **Chọn chất lượng**: Click chip 720p / 1080p / 1440p / 4K / MP3
4. **Settings** (⚙️): Thay đổi thư mục lưu, template tên file, số download đồng thời
5. **Mở thư mục** (📂): Mở folder chứa video đã tải

## Cấu trúc

```
vidgrab/
├── server.py      # Python backend (Flask + yt-dlp)
├── index.html     # Giao diện web
├── config.json    # Tự tạo khi lưu settings
└── README.md      # File này
```

## Mẹo

- Video mặc định lưu tại `~/Downloads/VidGrab/`
- Chất lượng mặc định: 1080p
- Hỗ trợ tất cả nguồn mà yt-dlp hỗ trợ (1000+ trang web)
- `Ctrl/Cmd + Enter` để download nhanh
- `Esc` để đóng Settings

## Mở rộng trong tương lai

App sử dụng yt-dlp làm engine, nên tự động hỗ trợ hầu hết các trang video:
YouTube, Vimeo, Dailymotion, Twitter/X, Facebook, Instagram, TikTok, v.v.
Khi cần thêm nguồn như StoryBlocks/Envato, có thể mở rộng bằng custom extractor.

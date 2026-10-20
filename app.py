import os
import re
import sys
import time
import json
import uuid
import shutil
import socket
import ipaddress
import subprocess
import threading
import concurrent.futures
import urllib.request
from urllib.parse import urlparse
from datetime import datetime

# Ensure utf-8 encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from flask import Flask, render_template, request, jsonify, send_file, abort

# Configure FFmpeg: prioritize system ffmpeg (Linux/Docker) then fallback to imageio_ffmpeg
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        raw_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        bin_dir = os.path.dirname(raw_ffmpeg)
        ffmpeg_alias = os.path.join(bin_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not os.path.exists(ffmpeg_alias):
            try:
                shutil.copy2(raw_ffmpeg, ffmpeg_alias)
            except Exception:
                pass
        FFMPEG_PATH = ffmpeg_alias if os.path.exists(ffmpeg_alias) else raw_ffmpeg
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        if os.name != "nt" and FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
            try:
                os.chmod(FFMPEG_PATH, 0o755)
            except Exception:
                pass
    except Exception as e:
        print(f"[Warning] Could not initialize imageio-ffmpeg: {e}")

if FFMPEG_PATH and os.name != "nt" and os.path.exists(FFMPEG_PATH):
    try:
        os.chmod(FFMPEG_PATH, 0o755)
    except Exception:
        pass

import yt_dlp

app = Flask(__name__)

# Base directories
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
TASKS_DIR = os.path.join(DOWNLOADS_DIR, "task_states")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(TASKS_DIR, exist_ok=True)

# Shared persistent task storage across multiple Gunicorn workers
def get_task_state(task_id):
    """Retrieve task state from disk so any Gunicorn worker process can read it."""
    state_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def update_task_state(task_id, **kwargs):
    """Atomically update task state on disk so all worker processes stay in sync."""
    state_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    try:
        current = get_task_state(task_id) or {}
        current.update(kwargs)
        tmp_file = f"{state_file}.tmp.{os.getpid()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(current, f)
        os.replace(tmp_file, state_file)
    except Exception as e:
        print(f"[Warning] Task state update error: {e}")

def sanitize_filename(name):
    """Sanitize string for safe filenames across platforms."""
    name = re.sub(r'[\\/*?:"<>|]', "", name or "")
    name = re.sub(r'[\s_]+', " ", name).strip()
    return name[:80] if name else "video"

def format_duration(seconds):
    """Convert seconds into HH:MM:SS or MM:SS."""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def is_safe_url(url_string):
    """
    Validate URL to protect against SSRF (Server-Side Request Forgery)
    and internal network probing.
    """
    try:
        parsed = urlparse(url_string)
        if parsed.scheme not in ("http", "https"):
            return False, "Invalid URL scheme. Sirf http aur https allowed hain."

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid hostname in URL."

        hostname_lower = hostname.lower()

        # Block localhost and local loopback domains
        if hostname_lower in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "local"):
            return False, "Localhost addresses are not allowed."

        # Resolve hostname to check for private / internal IP ranges
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
            ):
                return False, "Private or internal network URLs are blocked for security."
        except socket.gaierror:
            return False, "Host address could not be resolved."

        return True, None
    except Exception as e:
        return False, f"URL validation error: {str(e)}"

def detect_platform(url):
    """Detect source platform from URL."""
    url_l = (url or "").lower()
    if "instagram.com" in url_l:
        return "instagram"
    elif "facebook.com" in url_l or "fb.watch" in url_l:
        return "facebook"
    elif "youtube.com" in url_l or "youtu.be" in url_l:
        return "youtube"
    elif "tiktok.com" in url_l:
        return "tiktok"
    elif "twitter.com" in url_l or "x.com" in url_l:
        return "twitter"
    return "other"

def resolve_canonical_url(url):
    """
    Resolve shortened, share, or redirect URLs (e.g. facebook.com/share/r/,
    fb.watch/, youtu.be/, instagram.com/share/) to their direct canonical media URLs.
    """
    try:
        url_l = (url or "").lower()
        needs_resolution = any(
            marker in url_l for marker in [
                "facebook.com/share/",
                "fb.watch",
                "instagram.com/share/",
                "youtu.be/",
                "bit.ly/",
                "tinyurl.com/"
            ]
        )
        if needs_resolution:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                if final_url and is_safe_url(final_url)[0]:
                    return final_url
    except Exception as e:
        print(f"[Info] Redirect resolution notice for {url[:50]}: {e}")
    return url

def fetch_opengraph_fallback(url):
    """
    Fallback extractor for public photo and story posts across Instagram, Facebook, and Twitter
    when video extractors report no video found or fail to resolve.
    Extracts high-resolution og:image, og:video, and title tags directly.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode("utf-8", errors="ignore")

            og_img = re.search(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            if not og_img:
                og_img = re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+property=[\'"]og:image[\'"]', html, re.I)
            if not og_img:
                og_img = re.search(r'<meta[^>]+name=[\'"]twitter:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)

            og_vid = re.search(r'<meta[^>]+property=[\'"]og:video[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            if not og_vid:
                og_vid = re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+property=[\'"]og:video[\'"]', html, re.I)

            og_title = re.search(r'<meta[^>]+property=[\'"]og:title[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            if not og_title:
                og_title = re.search(r'<title>([^<]+)</title>', html, re.I)

            title_text = (og_title.group(1).strip() if og_title else "Social Media Post").replace("&amp;", "&").replace("&quot;", '"')
            img_url = (og_img.group(1).replace("&amp;", "&") if og_img else None)
            vid_url = (og_vid.group(1).replace("&amp;", "&") if og_vid else None)

            if vid_url:
                return {
                    "title": title_text,
                    "thumbnail": img_url or "",
                    "formats": [{
                        "format_id": "direct_mp4",
                        "url": vid_url,
                        "ext": "mp4",
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                        "height": 1080
                    }],
                    "duration": None,
                    "is_photo": False
                }
            elif img_url:
                return {
                    "title": title_text,
                    "thumbnail": img_url,
                    "thumbnails": [{"url": img_url, "width": 1080, "height": 1080}],
                    "formats": [],
                    "duration": None,
                    "is_photo": True,
                    "url": img_url
                }
    except Exception as e:
        print(f"[Info] OpenGraph fallback notice: {e}")
    return None

def parse_media_info(info):
    """
    Intelligently inspect info dict to distinguish between:
    - Standard Video (with formats)
    - Photo / Image post (with thumbnails but no video formats)
    - Carousel / Playlist (with entries)
    Returns: (target_entry, is_photo, best_image_url)
    """
    if not info:
        return None, False, None

    # Handle playlist / carousel entries
    entries = info.get("entries")
    if entries and isinstance(entries, list) and len(entries) > 0:
        valid_entries = [e for e in entries if e]
        # Prefer an entry that has video formats if available, otherwise first entry
        vid_entry = next((e for e in valid_entries if any(f.get("vcodec") != "none" and (f.get("height") or f.get("ext") in ("mp4", "webm", "mkv")) for f in (e.get("formats") or []))), None)
        target = vid_entry or (valid_entries[0] if valid_entries else info)
    else:
        target = info

    formats = target.get("formats") or []
    has_video = any(
        f.get("vcodec") != "none" and (f.get("height") or f.get("ext") in ("mp4", "webm", "mkv"))
        for f in formats
    ) if formats else False

    # Collect best image from thumbnails
    thumbs = target.get("thumbnails") or info.get("thumbnails") or []
    best_image = None
    if thumbs:
        sorted_thumbs = sorted(
            [t for t in thumbs if isinstance(t, dict) and t.get("url")],
            key=lambda x: (x.get("width") or 0) * (x.get("height") or 0),
            reverse=True
        )
        if sorted_thumbs:
            best_image = sorted_thumbs[0].get("url")

    if not best_image:
        best_image = target.get("thumbnail") or info.get("thumbnail")
    if not best_image and target.get("url") and any((target.get("ext") or "").endswith(e) for e in ("jpg", "jpeg", "png", "webp")):
        best_image = target.get("url")

    is_photo = (not has_video) and bool(best_image or target.get("is_photo"))
    return target, is_photo, best_image

def cleanup_old_files():
    """Periodically remove files and task states older than 15 minutes."""
    while True:
        try:
            time.sleep(300)  # every 5 minutes
            now = time.time()
            for directory in [DOWNLOADS_DIR, TASKS_DIR]:
                if os.path.exists(directory):
                    for f in os.listdir(directory):
                        f_path = os.path.join(directory, f)
                        if os.path.isfile(f_path):
                            if now - os.path.getmtime(f_path) > 900:  # 15 minutes
                                try:
                                    os.remove(f_path)
                                except Exception:
                                    pass
        except Exception:
            pass

# Start cleanup thread in daemon mode
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

# Auto-update yt-dlp periodically in background so YouTube algorithm changes never break downloads
def auto_update_ytdlp_daemon():
    """Continuously keep yt-dlp and yt-dlp-ejs updated to the latest version."""
    time.sleep(20)  # wait 20s after boot
    while True:
        try:
            print("[+] Running auto-update check for yt-dlp and yt-dlp-ejs...")
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "yt-dlp-ejs", "--no-cache-dir"],
                capture_output=True,
                text=True,
                timeout=180
            )
            print("[+] yt-dlp and yt-dlp-ejs auto-update check completed.")
        except Exception as e:
            print(f"[-] yt-dlp auto-update notice: {e}")
        time.sleep(43200)  # Check every 12 hours

auto_update_thread = threading.Thread(target=auto_update_ytdlp_daemon, daemon=True)
auto_update_thread.start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/sw.js")
def serve_sw():
    """Serve Monetag verification & PWA service worker at site root."""
    return send_file(os.path.join(BASE_DIR, "sw.js"), mimetype="application/javascript")

@app.route("/service-worker.js")
def serve_service_worker():
    return send_file(os.path.join(BASE_DIR, "sw.js"), mimetype="application/javascript")

@app.route("/robots.txt")
def serve_robots():
    return send_file(os.path.join(BASE_DIR, "robots.txt"), mimetype="text/plain")

@app.route("/sitemap.xml")
def serve_sitemap():
    return send_file(os.path.join(BASE_DIR, "sitemap.xml"), mimetype="application/xml")

@app.route("/google84aae8bf3db6dc70.html")
def serve_google_verification():
    return send_file(os.path.join(BASE_DIR, "google84aae8bf3db6dc70.html"), mimetype="text/html")

# --- YouTube RapidAPI Engine Configuration ---
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "7ca6e5ac3emsh2d893690354b277p1b1451jsn7aa5be9f0834")
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST", "yt-api.p.rapidapi.com")

def extract_youtube_video_id(url):
    """Extract 11-char YouTube video ID from various URL formats."""
    m = re.search(r'(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})', url or "")
    return m.group(1) if m else None

YOUTUBE_API_CACHE = {}

def fetch_youtube_rapidapi(vid):
    """Fetch video metadata and direct CDN streaming URLs from YT-API with in-memory caching."""
    now = time.time()
    if vid in YOUTUBE_API_CACHE:
        cached_time, cached_data = YOUTUBE_API_CACHE[vid]
        if now - cached_time < 3600:
            return cached_data

    req = urllib.request.Request(
        f"https://{RAPIDAPI_HOST}/dl?id={vid}",
        headers={
            "x-rapidapi-key": RAPIDAPI_KEY,
            "x-rapidapi-host": RAPIDAPI_HOST,
            "User-Agent": "Mozilla/5.0"
        }
    )
    with urllib.request.urlopen(req, timeout=25) as res:
        data = json.loads(res.read().decode("utf-8"))
        if data.get("status") == "OK" or data.get("title"):
            YOUTUBE_API_CACHE[vid] = (now, data)
            return data
        raise Exception(data.get("message") or "RapidAPI could not resolve YouTube video.")

def download_stream_to_file(stream_url, dest_path, task_id, start_pct=0, end_pct=100):
    """Download stream sequentially in chunks (fallback or small streams)."""
    req = urllib.request.Request(
        stream_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=40) as response, open(dest_path, "wb") as out_file:
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 512 * 1024
        start_time = time.time()
        last_update = start_time

        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_update >= 0.35:
                last_update = now
                pct_range = end_pct - start_pct
                curr_pct = round(start_pct + (downloaded / total_size * pct_range), 1) if total_size else round((start_pct + end_pct) / 2, 1)
                speed_bytes = downloaded / max(now - start_time, 0.001)
                speed_str = f"{speed_bytes / (1024 * 1024):.1f} MB/s" if speed_bytes > 1024 * 1024 else f"{speed_bytes / 1024:.0f} KB/s"
                update_task_state(task_id, percent=curr_pct, speed=speed_str, status="downloading")
    return True

def download_stream_turbo(stream_url, dest_path, task_id, start_pct=0, end_pct=100, max_workers=6):
    """
    High-speed turbo multi-threaded chunked downloader for Google Video CDN streams.
    Bypasses YouTube's ~0.09 MB/s playback throttle by downloading parallel HTTP Range chunks
    at 3-10+ MB/s, reducing download time from minutes to just a few seconds.
    """
    total_size = 0
    try:
        probe_req = urllib.request.Request(
            stream_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Range": "bytes=0-0"}
        )
        with urllib.request.urlopen(probe_req, timeout=12) as probe_res:
            cr = probe_res.headers.get("Content-Range", "")
            if cr and "/" in cr:
                total_size = int(cr.split("/")[-1])
            else:
                total_size = int(probe_res.headers.get("Content-Length", 0))
    except Exception as e:
        print(f"[Info] Probe Range header error: {e}")
        total_size = 0

    if total_size <= 2 * 1024 * 1024:
        return download_stream_to_file(stream_url, dest_path, task_id, start_pct, end_pct)

    try:
        with open(dest_path, "wb") as f:
            f.truncate(total_size)

        chunk_size = 2 * 1024 * 1024  # 2MB chunks for optimal throughput
        ranges = []
        for offset in range(0, total_size, chunk_size):
            end = min(offset + chunk_size - 1, total_size - 1)
            ranges.append((offset, end))

        downloaded = 0
        file_lock = threading.Lock()
        start_time = time.time()
        last_update = start_time

        def fetch_range(offset, end):
            nonlocal downloaded, last_update
            range_req = urllib.request.Request(
                stream_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Range": f"bytes={offset}-{end}"
                }
            )
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(range_req, timeout=20) as r:
                        chunk_bytes = r.read()
                        with file_lock:
                            with open(dest_path, "r+b") as out_f:
                                out_f.seek(offset)
                                out_f.write(chunk_bytes)
                            downloaded += len(chunk_bytes)
                            now = time.time()
                            if now - last_update >= 0.35:
                                last_update = now
                                pct_range = end_pct - start_pct
                                curr_pct = round(start_pct + (downloaded / total_size * pct_range), 1)
                                speed_bytes = downloaded / max(now - start_time, 0.001)
                                speed_str = f"{speed_bytes / (1024 * 1024):.1f} MB/s" if speed_bytes > 1024 * 1024 else f"{speed_bytes / 1024:.0f} KB/s"
                                update_task_state(task_id, percent=curr_pct, speed=speed_str, status="downloading")
                    return True
                except Exception as e:
                    if attempt == 2:
                        raise e
                    time.sleep(0.4)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(fetch_range, r[0], r[1]) for r in ranges]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        return True
    except Exception as e:
        print(f"[Warning] Turbo chunk download fallback ({e}), falling back to sequential stream...")
        return download_stream_to_file(stream_url, dest_path, task_id, start_pct, end_pct)

def download_youtube_via_rapidapi(task_id, vid, quality, title_hint):
    """Download YouTube video/audio using high-speed direct CDN streams from YT-API."""
    yt_data = fetch_youtube_rapidapi(vid)
    sanitized_title = sanitize_filename(title_hint or yt_data.get("title") or "youtube_video")
    
    prog_formats = [f for f in yt_data.get("formats", []) if f.get("url")]
    adapt_formats = [f for f in yt_data.get("adaptiveFormats", []) if f.get("url")]
    
    audio_formats = [f for f in adapt_formats if "audio" in f.get("mimeType", "")]
    best_audio = None
    for f in audio_formats:
        if f.get("itag") == 140 or "mp4" in f.get("mimeType", ""):
            best_audio = f
            break
    if not best_audio and audio_formats:
        best_audio = audio_formats[0]

    final_output = None

    if quality == "mp3":
        if not best_audio:
            raise Exception("No audio stream found.")
        temp_audio = os.path.join(DOWNLOADS_DIR, f"{task_id}_temp_audio.m4a")
        final_mp3 = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.mp3")
        
        update_task_state(task_id, status="downloading", speed="Downloading Audio...")
        download_stream_turbo(best_audio["url"], temp_audio, task_id, 0, 85, max_workers=4)
        
        update_task_state(task_id, status="processing", speed="Converting to MP3...", percent=90)
        if FFMPEG_PATH and os.path.exists(FFMPEG_PATH):
            cmd = [FFMPEG_PATH, "-y", "-i", temp_audio, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", final_mp3]
            res = subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except Exception: pass
            if not os.path.exists(final_mp3):
                raise Exception("FFmpeg MP3 conversion failed.")
            final_output = final_mp3
        else:
            final_output = temp_audio
            
    else:
        target_prog = None
        for f in prog_formats:
            ql = f.get("qualityLabel", "")
            if quality == "360" and "360" in ql:
                target_prog = f
                break
            elif quality == "720" and "720" in ql:
                target_prog = f
                break

        if target_prog:
            final_mp4 = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.mp4")
            download_stream_turbo(target_prog["url"], final_mp4, task_id, 0, 98, max_workers=6)
            final_output = final_mp4
        else:
            target_vid = None
            video_formats = [f for f in adapt_formats if "video" in f.get("mimeType", "")]
            
            if quality == "1080":
                for f in video_formats:
                    if "1080" in f.get("qualityLabel", "") and "mp4" in f.get("mimeType", ""):
                        target_vid = f
                        break
                if not target_vid:
                    for f in video_formats:
                        if "1080" in f.get("qualityLabel", ""):
                            target_vid = f
                            break
            elif quality == "720":
                for f in video_formats:
                    if "720" in f.get("qualityLabel", "") and "mp4" in f.get("mimeType", ""):
                        target_vid = f
                        break
            elif quality == "480":
                for f in video_formats:
                    if "480" in f.get("qualityLabel", ""):
                        target_vid = f
                        break

            if not target_vid and video_formats:
                mp4_vids = [f for f in video_formats if "mp4" in f.get("mimeType", "")]
                target_vid = mp4_vids[0] if mp4_vids else video_formats[0]

            if not target_vid:
                raise Exception("Could not find suitable video stream.")

            temp_video = os.path.join(DOWNLOADS_DIR, f"{task_id}_temp_vid.mp4")
            temp_audio = os.path.join(DOWNLOADS_DIR, f"{task_id}_temp_aud.m4a")
            final_mp4 = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.mp4")

            update_task_state(task_id, status="downloading", speed="Downloading Video...")
            download_stream_turbo(target_vid["url"], temp_video, task_id, 0, 75, max_workers=6)

            if best_audio:
                update_task_state(task_id, status="downloading", speed="Downloading Audio...")
                download_stream_turbo(best_audio["url"], temp_audio, task_id, 75, 92, max_workers=4)

            update_task_state(task_id, status="processing", speed="Finalizing HD Video...", percent=95)
            if FFMPEG_PATH and os.path.exists(temp_video) and os.path.exists(temp_audio):
                cmd = [
                    FFMPEG_PATH, "-y",
                    "-i", temp_video,
                    "-i", temp_audio,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-shortest",
                    final_mp4
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=120)
                try: os.remove(temp_video)
                except Exception: pass
                try: os.remove(temp_audio)
                except Exception: pass

                if not os.path.exists(final_mp4):
                    raise Exception("FFmpeg video/audio muxing failed.")
                final_output = final_mp4
            else:
                final_output = temp_video

    if final_output and os.path.exists(final_output):
        final_ext = os.path.splitext(final_output)[1]
        display_name = f"{sanitized_title}{final_ext}"
        update_task_state(
            task_id,
            status="completed",
            percent=100,
            filepath=final_output,
            filename=display_name,
            speed="Done!"
        )
        return True
    return False

def extract_video_info_resilient(url):
    """
    Extract video or photo metadata with resilient fallback strategies.
    YouTube: VisionOS and Android clients waterfall to bypass BotGuard.
    Instagram / Facebook: Desktop User-Agent rotation with optional cookies.txt.
    Public Photos: OpenGraph fallback when video formats are absent.
    """
    is_yt = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    cookie_path = os.path.join(BASE_DIR, "cookies.txt")
    has_cookies = os.path.isfile(cookie_path) and os.path.getsize(cookie_path) > 10

    env_cookies = os.environ.get("YOUTUBE_COOKIES") or os.environ.get("INSTAGRAM_COOKIES") or os.environ.get("COOKIES")
    if env_cookies and not has_cookies:
        try:
            with open(cookie_path, "w", encoding="utf-8") as cf:
                cf.write(env_cookies)
            has_cookies = True
        except Exception:
            pass

    if is_yt:
        strategies = []
        if has_cookies:
            strategies.extend([
                {"extractor_args": {"youtube": {"player_client": ["android"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["mweb"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["ios"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["visionos"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["visionos", "web_embedded"]}}, "use_cookies": True},
                {"extractor_args": {}, "use_cookies": True},
            ])
        strategies.extend([
            {"extractor_args": {"youtube": {"player_client": ["android"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["android"], "player_skip": ["webpage"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["mweb"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["ios"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["visionos"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["visionos", "web_embedded"]}}, "use_cookies": False},
            {"extractor_args": {}, "use_cookies": False},
        ])
    else:
        strategies = []
        if has_cookies:
            strategies.append({"extractor_args": {}, "use_cookies": True})
        strategies.append({"extractor_args": {}, "use_cookies": False})

    last_err = None
    for strat in strategies:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 25,
            "cachedir": False,
            "source_address": "0.0.0.0",
            "js_runtimes": {"deno": {}, "node": {}},
        }
        if not is_yt:
            ydl_opts["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ydl_opts["format_sort"] = ["hasaud", "res", "fps"]

        if strat.get("extractor_args"):
            ydl_opts["extractor_args"] = strat["extractor_args"]
        if strat.get("use_cookies") and has_cookies:
            ydl_opts["cookiefile"] = cookie_path

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info
        except Exception as e:
            last_err = e
            continue

    # Fallback for public photos when yt-dlp reports no video
    if not is_yt:
        og_info = fetch_opengraph_fallback(url)
        if og_info:
            return og_info

    raise last_err or Exception("Could not fetch media information.")

@app.route("/api/info", methods=["POST"])
def get_video_info():
    """Fetch video or photo metadata securely with robust fallback clients."""
    data = request.get_json(force=True, silent=True) or {}
    raw_url = (data.get("url") or "").strip()

    if not raw_url:
        return jsonify({"success": False, "error": "Kripya video ya photo URL enter karein (Please provide a URL)"}), 400

    if len(raw_url) > 2048:
        return jsonify({"success": False, "error": "URL bahut lamba hai (URL length exceeded limit)"}), 400

    if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
        raw_url = "https://" + raw_url

    # SSRF Protection
    is_safe, err_reason = is_safe_url(raw_url)
    if not is_safe:
        return jsonify({"success": False, "error": err_reason or "Invalid URL entered."}), 400

    # Follow redirects for short and share URLs (facebook.com/share/r, fb.watch, instagram.com/share)
    url = resolve_canonical_url(raw_url)
    platform = detect_platform(url)

    # High-speed RapidAPI engine for YouTube
    if platform == "youtube" and RAPIDAPI_KEY:
        vid = extract_youtube_video_id(url)
        if vid:
            try:
                yt_data = fetch_youtube_rapidapi(vid)
                title = yt_data.get("title") or "YouTube Video"
                uploader = yt_data.get("channelTitle") or "YouTube"
                dur_secs = yt_data.get("lengthSeconds")
                duration_str = format_duration(dur_secs)

                thumbs = yt_data.get("thumbnail", [])
                thumbnail = thumbs[-1].get("url") if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

                adapt = yt_data.get("adaptiveFormats", [])
                prog = yt_data.get("formats", [])
                has_1080 = any("1080" in f.get("qualityLabel", "") for f in adapt + prog)
                has_720 = any("720" in f.get("qualityLabel", "") for f in adapt + prog)

                quality_options = []
                quality_options.append({"id": "best", "label": "Maximum Quality (Best)", "ext": "mp4", "badge": "Best MP4", "desc": "Auto Highest Res (Sound Included)"})
                if has_1080:
                    quality_options.append({"id": "1080", "label": "Full HD (1080p)", "ext": "mp4", "badge": "1080p MP4", "desc": "Best Quality"})
                if has_720:
                    quality_options.append({"id": "720", "label": "HD (720p)", "ext": "mp4", "badge": "720p MP4", "desc": "Standard HD"})
                quality_options.append({"id": "480", "label": "SD (480p)", "ext": "mp4", "badge": "480p MP4", "desc": "Fast & Light"})
                quality_options.append({"id": "360", "label": "SD (360p)", "ext": "mp4", "badge": "360p MP4", "desc": "Fast & Mobile Friendly"})
                quality_options.append({"id": "mp3", "label": "Audio Only (MP3)", "ext": "mp3", "badge": "MP3 Audio", "desc": "High Quality 192k"})

                return jsonify({
                    "success": True,
                    "data": {
                        "url": url,
                        "title": title,
                        "uploader": uploader,
                        "duration": duration_str,
                        "thumbnail": thumbnail,
                        "platform": platform,
                        "media_type": "video",
                        "qualities": quality_options
                    }
                })
            except Exception as e:
                print(f"[Warning] RapidAPI fetch failed, falling back to yt-dlp: {e}")

    try:
        raw_info = extract_video_info_resilient(url)
        if not raw_info:
            return jsonify({"success": False, "error": "Media details fetch nahi ho paye. Kripya URL check karein."}), 400

        target, is_photo, best_image = parse_media_info(raw_info)
        title = target.get("title") or raw_info.get("title") or "Media"
        uploader = target.get("uploader") or target.get("channel") or raw_info.get("uploader") or platform.capitalize()
        thumbnail = best_image or target.get("thumbnail") or raw_info.get("thumbnail") or ""

        quality_options = []

        if is_photo:
            duration_str = "HD Photo"
            media_type = "photo"
            quality_options.append({
                "id": "photo_hd",
                "label": "Original Photo (High-Res JPG)",
                "ext": "jpg",
                "badge": "HD Photo",
                "desc": "Original Full Resolution Image"
            })
            quality_options.append({
                "id": "photo_sd",
                "label": "Standard Quality Photo (JPG)",
                "ext": "jpg",
                "badge": "Standard JPG",
                "desc": "Compressed & Fast Download"
            })
        else:
            duration_secs = target.get("duration") or raw_info.get("duration")
            duration_str = format_duration(duration_secs)
            media_type = "video"

            formats = target.get("formats") or []
            heights = set()
            for f in formats:
                h = f.get("height")
                if h and isinstance(h, int):
                    heights.add(h)

            quality_options.append({
                "id": "best",
                "label": "Maximum Quality (Best MP4)",
                "ext": "mp4",
                "badge": "Best MP4",
                "desc": "Auto Highest Res (Sound Included)"
            })

            if any(h >= 1080 for h in heights) or platform in ["instagram", "facebook"]:
                quality_options.append({
                    "id": "1080",
                    "label": "Full HD (1080p)",
                    "ext": "mp4",
                    "badge": "1080p MP4",
                    "desc": "Crisp 1080p Audio+Video"
                })

            if any(h >= 720 for h in heights) or platform in ["instagram", "facebook"]:
                quality_options.append({
                    "id": "720",
                    "label": "HD (720p)",
                    "ext": "mp4",
                    "badge": "720p MP4",
                    "desc": "Standard HD Audio+Video"
                })

            quality_options.append({
                "id": "480",
                "label": "SD (480p)",
                "ext": "mp4",
                "badge": "480p MP4",
                "desc": "Fast & Light"
            })

            quality_options.append({
                "id": "mp3",
                "label": "Audio Only (MP3)",
                "ext": "mp3",
                "badge": "MP3 Audio",
                "desc": "High Quality 192k Audio"
            })

            if best_image:
                quality_options.append({
                    "id": "photo_hd",
                    "label": "Download Cover Photo (JPG)",
                    "ext": "jpg",
                    "badge": "HD Photo",
                    "desc": "High-Res Post Thumbnail / Cover"
                })

        return jsonify({
            "success": True,
            "data": {
                "url": url,
                "title": title,
                "uploader": uploader,
                "duration": duration_str,
                "thumbnail": thumbnail,
                "platform": platform,
                "media_type": media_type,
                "qualities": quality_options
            }
        })

    except Exception as e:
        err_msg = str(e)
        err_lower = err_msg.lower()
        if "/stories/" in url.lower():
            err_text = "Instagram / Facebook Stories 24-ghante ke liye hoti hain aur Meta inke liye login maangta hai. Story download karne ke liye Admin panel (/admin) me cookies save karein, ya public Reel / Post ka link paste karein."
        elif "private video" in err_lower or "login" in err_lower or "this content is only available" in err_lower:
            err_text = "Yeh video ya photo private hai ya isme login ki zaroorat hai. Kripya public link use karein ya Admin panel me cookies add karein."
        elif "this video is unavailable" in err_lower:
            err_text = "Yeh video available nahi hai ya platform se remove ho chuka hai."
        elif "unsupported url" in err_lower:
            err_text = "Unsupported URL. Kripya valid YouTube, Instagram ya Facebook link dalein."
        else:
            first_line = err_msg.strip().splitlines()[-1] if err_msg else ""
            clean_hint = re.sub(r'ERROR:\s*\[.*?\]\s*', '', first_line).strip()
            err_text = f"Fetch error: {clean_hint[:90]}" if clean_hint else "Details fetch karne me error aaya. Kripya link verify karein."
        return jsonify({"success": False, "error": err_text}), 400

def run_download_task(task_id, url, quality, title_hint):
    """Execute video or photo download in background thread with audio guarantee and shared disk state."""
    update_task_state(
        task_id,
        status="downloading",
        percent=0,
        speed="Connecting...",
        eta="--",
        filename="",
        filepath="",
        error=None
    )

    sanitized_title = sanitize_filename(title_hint)
    is_yt = "youtube.com" in url.lower() or "youtu.be" in url.lower()

    # --- PHOTO DOWNLOAD BRANCH ---
    if quality in ("photo_hd", "photo_sd", "photo", "image"):
        try:
            update_task_state(task_id, status="downloading", percent=25, speed="Fetching HD Photo...", eta="1s")
            info = extract_video_info_resilient(url)
            target, is_photo, best_image = parse_media_info(info)

            target_img = best_image or (target.get("thumbnail") if target else None) or (info.get("thumbnail") if info else None)
            if not target_img:
                raise Exception("Could not find photo image URL.")

            output_file = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.jpg")
            download_stream_to_file(target_img, output_file, task_id, 25, 95)

            if os.path.exists(output_file) and os.path.getsize(output_file) > 100:
                display_name = f"{sanitized_title}.jpg"
                update_task_state(
                    task_id,
                    status="completed",
                    percent=100,
                    filepath=output_file,
                    filename=display_name,
                    speed="Done!"
                )
                return
            else:
                raise Exception("Photo download could not be verified.")
        except Exception as e:
            print(f"[Error] Photo download failed: {e}")
            update_task_state(task_id, status="error", error=f"Photo download failed: {str(e)[:100]}")
            return

    # --- YOUTUBE RAPIDAPI BRANCH ---
    if is_yt and RAPIDAPI_KEY:
        vid = extract_youtube_video_id(url)
        if vid:
            try:
                print(f"[+] Downloading YouTube video {vid} via RapidAPI engine...")
                success = download_youtube_via_rapidapi(task_id, vid, quality, title_hint)
                if success:
                    return
            except Exception as e:
                print(f"[Warning] RapidAPI download failed: {e}, falling back to yt-dlp...")

    # --- YT-DLP VIDEO & AUDIO ENGINE ---
    output_template = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = round((downloaded / total * 100), 1) if total > 0 else 0

            speed_bytes = d.get("speed")
            if speed_bytes:
                speed_str = f"{speed_bytes / (1024 * 1024):.1f} MB/s" if speed_bytes > 1024 * 1024 else f"{speed_bytes / 1024:.0f} KB/s"
            else:
                speed_str = "Downloading..."

            eta_seconds = d.get("eta")
            eta_str = f"{eta_seconds}s" if eta_seconds is not None else "--"

            update_task_state(
                task_id,
                percent=percent,
                speed=speed_str,
                eta=eta_str,
                status="downloading"
            )

        elif d.get("status") == "finished":
            update_task_state(
                task_id,
                percent=100,
                status="processing",
                speed="Processing / Merging..."
            )

    cookie_path = os.path.join(BASE_DIR, "cookies.txt")
    has_cookies = os.path.isfile(cookie_path) and os.path.getsize(cookie_path) > 10

    env_cookies = os.environ.get("YOUTUBE_COOKIES") or os.environ.get("INSTAGRAM_COOKIES") or os.environ.get("COOKIES")
    if env_cookies and not has_cookies:
        try:
            with open(cookie_path, "w", encoding="utf-8") as cf:
                cf.write(env_cookies)
            has_cookies = True
        except Exception:
            pass

    ydl_opts = {
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "cachedir": False,
        "source_address": "0.0.0.0",
        "js_runtimes": {"deno": {}, "node": {}},
    }

    if not is_yt:
        ydl_opts["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    if is_yt:
        if quality == "mp3":
            ydl_opts["format"] = "ba/b"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif quality == "1080":
            ydl_opts["format"] = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        elif quality == "720":
            ydl_opts["format"] = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        elif quality == "480":
            ydl_opts["format"] = "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:  # best
            ydl_opts["format"] = "bv*+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"

        yt_strategies = []
        if has_cookies:
            yt_strategies.extend([
                {"extractor_args": {"youtube": {"player_client": ["android"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["mweb"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["visionos"]}}, "use_cookies": True},
                {"extractor_args": {"youtube": {"player_client": ["visionos", "web_embedded"]}}, "use_cookies": True},
                {"extractor_args": {}, "use_cookies": True},
            ])
        yt_strategies.extend([
            {"extractor_args": {"youtube": {"player_client": ["android"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["mweb"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["visionos"]}}, "use_cookies": False},
            {"extractor_args": {"youtube": {"player_client": ["visionos", "web_embedded"]}}, "use_cookies": False},
            {"extractor_args": {}, "use_cookies": False},
        ])
    else:
        # Instagram and Facebook logic:
        # Prioritize progressive combined streams (b) first so audio is 100% baked in and never missing!
        ydl_opts["format_sort"] = ["hasaud", "res", "fps"]
        if quality == "mp3":
            ydl_opts["format"] = "ba/b"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif quality == "1080":
            ydl_opts["format"] = "b[height<=1080]/bv*[height<=1080]+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        elif quality == "720":
            ydl_opts["format"] = "b[height<=720]/bv*[height<=720]+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        elif quality == "480":
            ydl_opts["format"] = "b[height<=480]/bv*[height<=480]+ba/b/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:  # best
            ydl_opts["format"] = "b/bv*+ba/best"
            ydl_opts["merge_output_format"] = "mp4"

        yt_strategies = []
        if has_cookies:
            yt_strategies.append({"extractor_args": {}, "use_cookies": True})
        yt_strategies.append({"extractor_args": {}, "use_cookies": False})

    download_success = False
    last_download_err = None

    for strat in yt_strategies:
        current_opts = dict(ydl_opts)
        if strat.get("extractor_args"):
            current_opts["extractor_args"] = strat["extractor_args"]
        if strat.get("use_cookies") and has_cookies:
            current_opts["cookiefile"] = cookie_path

        try:
            with yt_dlp.YoutubeDL(current_opts) as ydl:
                ydl.download([url])
            download_success = True
            break
        except Exception as e:
            last_download_err = e
            err_str = str(e).lower()
            if "cookies are no longer valid" in err_str or ("cookie" in err_str and "expired" in err_str):
                has_cookies = False
            continue

    try:
        if not download_success:
            raise last_download_err or Exception("Download failed.")

        # Locate generated file
        target_file = None
        for f in os.listdir(DOWNLOADS_DIR):
            if f.startswith(task_id):
                candidate = os.path.join(DOWNLOADS_DIR, f)
                if os.path.isfile(candidate) and not f.endswith(".json"):
                    target_file = candidate
                    break

        if target_file and os.path.exists(target_file):
            final_ext = os.path.splitext(target_file)[1]
            display_name = f"{sanitized_title}{final_ext}"
            update_task_state(
                task_id,
                status="completed",
                percent=100,
                filepath=target_file,
                filename=display_name,
                speed="Done!"
            )
        else:
            update_task_state(
                task_id,
                status="error",
                error="Downloaded file could not be located on server."
            )
    except Exception as e:
        err_msg = str(e)
        clean_hint = re.sub(r'ERROR:\s*\[.*?\]\s*', '', err_msg.strip().splitlines()[-1] if err_msg else "").strip()
        update_task_state(
            task_id,
            status="error",
            error=f"Download failed: {clean_hint[:120]}"
        )

@app.route("/api/download/start", methods=["POST"])
def start_download():
    """Start asynchronous download task with strict validation."""
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip().lower()
    title = (data.get("title") or "media").strip()

    if not url:
        return jsonify({"success": False, "error": "URL missing"}), 400

    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL length exceeded limit"}), 400

    allowed_qualities = {"best", "1080", "720", "480", "360", "mp3", "photo_hd", "photo_sd", "photo", "image"}
    if quality not in allowed_qualities:
        quality = "best"

    # SSRF Protection
    is_safe, err_reason = is_safe_url(url)
    if not is_safe:
        return jsonify({"success": False, "error": err_reason or "Invalid URL entered."}), 400

    task_id = uuid.uuid4().hex[:12]

    # Pre-initialize task state on disk immediately before returning
    update_task_state(
        task_id,
        status="starting",
        percent=0,
        speed="Initializing...",
        eta="--",
        filename="",
        filepath="",
        error=None
    )

    thread = threading.Thread(
        target=run_download_task,
        args=(task_id, url, quality, title),
        daemon=True
    )
    thread.start()

    return jsonify({"success": True, "task_id": task_id})

@app.route("/api/download/status/<task_id>", methods=["GET"])
def check_status(task_id):
    """Check progress of a download task from shared disk state."""
    if not re.match(r"^[a-fA-F0-9]{8,32}$", task_id):
        return jsonify({"status": "error", "error": "Invalid task ID format"}), 400

    task = get_task_state(task_id)
    if not task:
        # Check if file has already completed and is in downloads directory
        for f in os.listdir(DOWNLOADS_DIR):
            if f.startswith(task_id) and not f.endswith(".json"):
                return jsonify({
                    "status": "completed",
                    "percent": 100,
                    "speed": "Done!",
                    "eta": "0s",
                    "filename": f,
                    "error": None
                })
        return jsonify({"status": "not_found", "error": "Task not found"}), 404

    return jsonify({
        "status": task.get("status", "pending"),
        "percent": task.get("percent", 0),
        "speed": task.get("speed", ""),
        "eta": task.get("eta", ""),
        "filename": task.get("filename", ""),
        "error": task.get("error")
    })

@app.route("/api/download/file/<task_id>", methods=["GET"])
def download_file(task_id):
    """Send completed file to client with fallback file detection and path validation."""
    if not re.match(r"^[a-fA-F0-9]{8,32}$", task_id):
        abort(400, description="Invalid task ID format")

    task = get_task_state(task_id)
    target_file = None

    if task and task.get("filepath") and os.path.exists(task.get("filepath")):
        target_file = task.get("filepath")
    else:
        # Fallback file discovery in DOWNLOADS_DIR
        for f in os.listdir(DOWNLOADS_DIR):
            if f.startswith(task_id) and not f.endswith(".json"):
                candidate = os.path.join(DOWNLOADS_DIR, f)
                if os.path.isfile(candidate):
                    target_file = candidate
                    break

    if not target_file or not os.path.exists(target_file):
        abort(404, description="File not found or expired on server")

    # Path traversal validation
    real_downloads_dir = os.path.realpath(DOWNLOADS_DIR)
    real_filepath = os.path.realpath(target_file)
    if not real_filepath.startswith(real_downloads_dir + os.sep):
        abort(403, description="Access forbidden: Path outside downloads directory")

    filename = (task.get("filename") if task else None) or os.path.basename(target_file)
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".mp3":
        mimetype = "audio/mpeg"
    elif ext in (".jpg", ".jpeg"):
        mimetype = "image/jpeg"
    elif ext == ".png":
        mimetype = "image/png"
    elif ext == ".webp":
        mimetype = "image/webp"
    else:
        mimetype = "video/mp4"

    return send_file(
        real_filepath,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )

# --- Admin Engine Health & Cookie Manager ---
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin888")

@app.route("/admin", methods=["GET"])
def admin_panel():
    """Lightweight admin dashboard to monitor engine health and update YouTube cookies on the fly."""
    key = request.args.get("key", "").strip()
    is_authenticated = (key == ADMIN_SECRET)
    
    cookie_path = os.path.join(BASE_DIR, "cookies.txt")
    has_cookies = os.path.isfile(cookie_path) and os.path.getsize(cookie_path) > 10
    cookie_size = os.path.getsize(cookie_path) if os.path.isfile(cookie_path) else 0

    ytdlp_ver = getattr(yt_dlp, "__version__", "Unknown")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Downloader Engine Admin</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; margin: 0; }}
        .container {{ max-width: 760px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 24px; border: 1px solid #334155; }}
        h1 {{ color: #38bdf8; font-size: 22px; margin-top: 0; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; margin-right: 6px; }}
        .badge-green {{ background: #065f46; color: #34d399; }}
        .badge-yellow {{ background: #854d0e; color: #fde047; }}
        .card {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin: 16px 0; }}
        textarea {{ width: 100%; box-sizing: border-box; height: 160px; background: #1e293b; border: 1px solid #475569; color: #f8fafc; font-family: monospace; font-size: 12px; border-radius: 6px; padding: 10px; margin-top: 8px; }}
        input[type="text"], input[type="password"] {{ background: #1e293b; border: 1px solid #475569; color: #f8fafc; padding: 8px 12px; border-radius: 6px; font-size: 14px; width: 100%; box-sizing: border-box; }}
        button {{ background: #2563eb; color: white; border: none; padding: 10px 18px; border-radius: 6px; font-weight: 600; cursor: pointer; margin-top: 10px; transition: 0.2s; }}
        button:hover {{ background: #1d4ed8; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }}
        .stat-box {{ background: #0f172a; border: 1px solid #334155; padding: 12px; border-radius: 8px; }}
        .stat-label {{ font-size: 12px; color: #94a3b8; }}
        .stat-val {{ font-size: 15px; font-weight: 600; color: #f8fafc; margin-top: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚙️ Downloader Engine Control & Health</h1>
        
        <div class="stat-grid">
            <div class="stat-box">
                <div class="stat-label">YouTube Engine</div>
                <div class="stat-val"><span class="badge badge-green">VisionOS Waterfall</span></div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Instagram / FB</div>
                <div class="stat-val"><span class="badge badge-green">Operational</span></div>
            </div>
            <div class="stat-box">
                <div class="stat-label">yt-dlp Version</div>
                <div class="stat-val">{ytdlp_ver}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Cookies Status</div>
                <div class="stat-val"><span class="badge {'badge-green' if has_cookies else 'badge-yellow'}">{'Active (' + str(cookie_size) + ' B)' if has_cookies else 'No Cookie (VisionOS Active)'}</span></div>
            </div>
        </div>

        <div class="card">
            <h3 style="margin-top:0; font-size: 16px; color:#38bdf8;">🍪 Universal Cookie Updater (YouTube, Instagram & Facebook)</h3>
            <p style="font-size: 13px; color: #94a3b8; margin: 4px 0 12px;">Paste Netscape format cookies here for YouTube, Instagram, or Facebook. This instantly unlocks Instagram Stories, Private Reels, Facebook media, and YouTube bot walls without restarting or redeploying code.</p>
            <form method="POST" action="/api/admin/update-cookies">
                <label style="font-size:12px; color:#94a3b8;">Admin Secret PIN:</label>
                <input type="password" name="admin_secret" value="{key}" placeholder="Enter admin PIN (default: admin888)" style="margin-bottom: 10px;">
                <label style="font-size:12px; color:#94a3b8;">Paste Netscape Cookie File Content:</label>
                <textarea name="cookies_content" placeholder="# Netscape HTTP Cookie File&#10;.youtube.com\tTRUE\t/\tTRUE...&#10;.instagram.com\tTRUE\t/\tTRUE...&#10;.facebook.com\tTRUE\t/\tTRUE..."></textarea>
                <button type="submit">💾 Save & Activate Cookies</button>
            </form>
        </div>

        <div style="font-size: 12px; color: #64748b; text-align: center; margin-top: 20px;">
            One Click Downloader Enterprise Resilience Engine
        </div>
    </div>
</body>
</html>"""
    return html

@app.route("/api/admin/update-cookies", methods=["POST"])
def admin_update_cookies():
    """Instantly save updated cookies without redeployment."""
    secret = request.form.get("admin_secret", "").strip() or (request.json or {}).get("admin_secret", "").strip()
    if secret != ADMIN_SECRET:
        return jsonify({"success": False, "error": "Unauthorized: Invalid admin secret PIN"}), 403

    content = request.form.get("cookies_content", "").strip() or (request.json or {}).get("cookies_content", "").strip()
    if not content:
        return jsonify({"success": False, "error": "Cookie content cannot be empty"}), 400

    cookie_path = os.path.join(BASE_DIR, "cookies.txt")
    try:
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(content)
        return """<body style="background:#0f172a;color:#34d399;font-family:sans-serif;padding:40px;text-align:center;">
            <h2>✅ Cookies updated successfully!</h2>
            <p style="color:#94a3b8;">Fresh YouTube cookies have been saved to disk and activated.</p>
            <a href="/admin?key=""" + secret + """" style="color:#38bdf8;text-decoration:none;">⬅ Back to Admin</a>
        </body>"""
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.after_request
def add_security_headers(response):
    """Enterprise security headers configuration."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    csp_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://*.monetag.com https://*.alwingulla.com https://*.3nbf4.com https://3nbf4.com https://*.quge5.com https://quge5.com https://*.nap5k.com https://nap5k.com; "
        "worker-src 'self' blob: https: https://*.3nbf4.com https://*.quge5.com https://*.nap5k.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https: https://*.3nbf4.com https://*.monetag.com https://*.quge5.com https://*.nap5k.com; "
        "frame-src 'self' https: https://*.quge5.com https://*.nap5k.com; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    response.headers["Content-Security-Policy"] = csp_policy

    return response

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"[+] One Click Downloader starting on port {port}...")
    print(f"[+] Open in browser: http://127.0.0.1:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)

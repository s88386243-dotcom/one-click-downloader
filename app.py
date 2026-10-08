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
    except Exception as e:
        print(f"[Warning] Could not initialize imageio-ffmpeg: {e}")

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
    """Continuously keep yt-dlp updated to the latest version."""
    time.sleep(20)  # wait 20s after boot
    while True:
        try:
            print("[+] Running auto-update check for yt-dlp...")
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "--no-cache-dir"],
                capture_output=True,
                text=True,
                timeout=180
            )
            print("[+] yt-dlp auto-update check completed.")
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

def extract_video_info_resilient(url):
    """
    Extract video info with resilient fallback strategies.
    YouTube's web client blocks cloud IPs and requires JS challenges.
    We prioritize 'ios' and 'android' mobile clients which are immune to web challenge blocks.
    """
    is_yt = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    if is_yt:
        strategies = [
            {"player_client": ["default", "-android_sdkless"]},
            {"player_client": ["android"], "player_skip": ["webpage"]},
            {"player_client": ["mweb", "android"]},
            {"player_client": ["android"]},
            {}
        ]
    else:
        strategies = [{}]

    last_err = None
    for strat in strategies:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 25,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        if strat:
            ydl_opts["extractor_args"] = {"youtube": strat}

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info
        except Exception as e:
            last_err = e
            continue

    raise last_err or Exception("Could not fetch video information.")

@app.route("/api/info", methods=["POST"])
def get_video_info():
    """Fetch video metadata securely with robust fallback clients."""
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "Kripya video URL enter karein (Please provide a URL)"}), 400

    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL bahut lamba hai (URL length exceeded limit)"}), 400

    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    # SSRF Protection
    is_safe, err_reason = is_safe_url(url)
    if not is_safe:
        return jsonify({"success": False, "error": err_reason or "Invalid URL entered."}), 400

    platform = detect_platform(url)

    try:
        info = extract_video_info_resilient(url)
        
        if not info:
            return jsonify({"success": False, "error": "Video details fetch nahi ho paye. Kripya URL check karein."}), 400

        title = info.get("title") or "Video"
        uploader = info.get("uploader") or info.get("channel") or info.get("creator") or platform.capitalize()
        duration_secs = info.get("duration")
        duration_str = format_duration(duration_secs)
        thumbnail = info.get("thumbnail") or ""
        
        # Parse available video qualities
        formats = info.get("formats") or []
        heights = set()
        for f in formats:
            h = f.get("height")
            if h and isinstance(h, int):
                heights.add(h)

        quality_options = []
        
        # 1080p Full HD
        if any(h >= 1080 for h in heights) or platform in ["instagram", "facebook"]:
            quality_options.append({"id": "1080", "label": "Full HD (1080p)", "ext": "mp4", "badge": "1080p MP4", "desc": "Best Quality"})
        
        # 720p HD
        if any(h >= 720 for h in heights) or platform in ["instagram", "facebook"]:
            quality_options.append({"id": "720", "label": "HD (720p)", "ext": "mp4", "badge": "720p MP4", "desc": "Standard HD"})

        # 480p / 360p SD
        quality_options.append({"id": "480", "label": "SD (480p)", "ext": "mp4", "badge": "480p MP4", "desc": "Fast & Light"})
        
        # Always offer Best Available as default first option
        quality_options.insert(0, {"id": "best", "label": "Maximum Quality (Best)", "ext": "mp4", "badge": "Best MP4", "desc": "Auto Highest Res"})

        # MP3 Audio option
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
                "qualities": quality_options
            }
        })
    except Exception as e:
        err_msg = str(e)
        if "Private video" in err_msg or "login" in err_msg.lower():
            err_text = "Yeh video private hai ya isme login ki zaroorat hai."
        elif "This video is unavailable" in err_msg:
            err_text = "Yeh video YouTube par available nahi hai ya remove ho chuka hai."
        elif "Unsupported URL" in err_msg:
            err_text = "Unsupported URL. Kripya valid YouTube, Instagram ya Facebook link dalein."
        else:
            first_line = err_msg.strip().splitlines()[-1] if err_msg else ""
            clean_hint = re.sub(r'ERROR:\s*\[.*?\]\s*', '', first_line).strip()
            err_text = f"Fetch error: {clean_hint[:90]}" if clean_hint else "Video fetch karne me error aaya. Kripya link verify karein."
        return jsonify({"success": False, "error": err_text}), 400

def run_download_task(task_id, url, quality, title_hint):
    """Execute download in background thread and update shared persistent task state."""
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
    output_template = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = round((downloaded / total * 100), 1) if total > 0 else 0
            
            speed_bytes = d.get("speed")
            if speed_bytes:
                if speed_bytes > 1024 * 1024:
                    speed_str = f"{speed_bytes / (1024 * 1024):.1f} MB/s"
                else:
                    speed_str = f"{speed_bytes / 1024:.0f} KB/s"
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

    ydl_opts = {
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "-android_sdkless"],
                "player_skip": ["webpage"]
            }
        },
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    if quality == "mp3":
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif quality == "1080":
        ydl_opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        ydl_opts["merge_output_format"] = "mp4"
    elif quality == "720":
        ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        ydl_opts["merge_output_format"] = "mp4"
    elif quality == "480":
        ydl_opts["format"] = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        ydl_opts["merge_output_format"] = "mp4"
    else:  # best
        ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

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
        update_task_state(
            task_id,
            status="error",
            error=f"Download failed: {str(e)[:150]}"
        )

@app.route("/api/download/start", methods=["POST"])
def start_download():
    """Start asynchronous download task with strict validation."""
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip().lower()
    title = (data.get("title") or "video").strip()

    if not url:
        return jsonify({"success": False, "error": "URL missing"}), 400

    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL length exceeded limit"}), 400

    allowed_qualities = {"best", "1080", "720", "480", "360", "mp3"}
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
    return send_file(
        real_filepath,
        as_attachment=True,
        download_name=filename,
        mimetype="audio/mpeg" if filename.endswith(".mp3") else "video/mp4"
    )

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

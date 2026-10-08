# ⚡ One Click Downloader

**One Click Downloader** ek modern, fast aur user-friendly web application hai jisme aap **Instagram Reels**, **Facebook Videos**, aur **YouTube Videos / Shorts** ka link paste karke **1-Click** me High Quality (1080p, 720p, MP3) me download kar sakte hain.

---

## ✨ Features

- 🎯 **All-in-One Support:**
  - **Instagram**: Reels, Posts, IGTV, Video Clips
  - **Facebook**: Public Videos, Watch, Reels
  - **YouTube**: Videos, Shorts, Music Videos
  - **Other**: TikTok, Twitter/X, aur 1000+ anya platforms
- ⚡ **1-Click Auto Platform Detection:** Link paste karte hi platform identify hota hai.
- 📋 **Direct Clipboard Paste:** 'Paste' button se 1 click me link input ho jata hai.
- 🎬 **Multi-Quality Support:**
  - Full HD (1080p MP4)
  - HD (720p MP4)
  - SD (480p MP4)
  - Audio Only (MP3 192kbps)
- 📊 **Real-time Live Progress Bar:** Live download percentage, speed (MB/s), aur ETA dekh sakte hain.
- 🕒 **Recent Download History:** Browser me aapke recent downloads save rehte hain.
- 🎨 **Modern Dark Glassmorphic Design:** Ultra-sleek neon accent dark theme, mobile & desktop responsive.
- 🧹 **Automatic Storage Cleanup:** Server par 15 minute purani downloaded files automatically delete ho jati hain taaki storage full na ho.

---

## 🚀 Kaise Start Karein (How to Run)

### Method 1: Double-click Launcher (Windows)
Bas `run.bat` par double-click karein!
Yeh automatically server start karke aapke browser me `http://127.0.0.1:5000` open kar dega.

### Method 2: Command Line (PowerShell / Terminal)
```powershell
# 1. Project folder me jayein
cd C:\Users\hp\.gemini\antigravity\scratch\one-click-downloader

# 2. Dependencies install karein
pip install -r requirements.txt

# 3. Application start karein
python app.py
```

Fir browser me open karein:
👉 **http://127.0.0.1:5000**

---

## 🛠️ Tech Stack
- **Backend:** Python, Flask, `yt-dlp`, `imageio-ffmpeg` (bundled FFmpeg binary)
- **Frontend:** HTML5, CSS3 (Glassmorphism, CSS Variables, Responsive), Modern Vanilla JavaScript
- **Icons:** Font Awesome 6.5
- **Fonts:** Plus Jakarta Sans & Outfit

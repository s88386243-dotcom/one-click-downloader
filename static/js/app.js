// One Click Downloader - Interactive App Logic

document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const urlInput = document.getElementById("videoUrlInput");
    const pasteBtn = document.getElementById("pasteBtn");
    const clearBtn = document.getElementById("clearBtn");
    const fetchBtn = document.getElementById("fetchBtn");
    const detectedIcon = document.getElementById("detectedIcon");
    const detectionBadge = document.getElementById("detectionBadge");
    const detectedName = document.getElementById("detectedName");
    const errorAlert = document.getElementById("errorAlert");
    const errorMessage = document.getElementById("errorMessage");

    // Result Card Elements
    const resultCard = document.getElementById("resultCard");
    const videoThumb = document.getElementById("videoThumb");
    const videoDuration = document.getElementById("videoDuration");
    const videoSourceTag = document.getElementById("videoSourceTag");
    const videoTitle = document.getElementById("videoTitle");
    const videoAuthorName = document.getElementById("videoAuthorName");
    const qualitiesList = document.getElementById("qualitiesList");
    const startDownloadBtn = document.getElementById("startDownloadBtn");

    // Progress Elements
    const progressBox = document.getElementById("progressBox");
    const progressStatusText = document.getElementById("progressStatusText");
    const progressPercent = document.getElementById("progressPercent");
    const progressBarFill = document.getElementById("progressBarFill");
    const progressSpeed = document.getElementById("progressSpeed");
    const progressEta = document.getElementById("progressEta");
    const downloadReadyBox = document.getElementById("downloadReadyBox");
    const directDownloadLink = document.getElementById("directDownloadLink");

    // History Elements
    const historyToggleBtn = document.getElementById("historyToggleBtn");
    const historyDrawer = document.getElementById("historyDrawer");
    const drawerOverlay = document.getElementById("drawerOverlay");
    const closeHistoryBtn = document.getElementById("closeHistoryBtn");
    const clearHistoryBtn = document.getElementById("clearHistoryBtn");
    const historyList = document.getElementById("historyList");

    // State Variables
    let currentVideoData = null;
    let selectedQuality = "best";
    let activePollTimer = null;

    // Toast helper
    function showToast(msg) {
        const toast = document.getElementById("toast");
        const toastMsg = document.getElementById("toastMsg");
        toastMsg.textContent = msg;
        toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 3500);
    }

    // Platform detection helper
    function detectPlatform(url) {
        const lower = (url || "").toLowerCase();
        if (lower.includes("instagram.com")) {
            return {
                name: "Instagram",
                badge: "Instagram Reel / Post",
                icon: "fa-brands fa-instagram",
                color: "#e1306c"
            };
        } else if (lower.includes("facebook.com") || lower.includes("fb.watch")) {
            return {
                name: "Facebook",
                badge: "Facebook Video / Reel",
                icon: "fa-brands fa-facebook",
                color: "#1877f2"
            };
        } else if (lower.includes("youtube.com") || lower.includes("youtu.be")) {
            return {
                name: "YouTube",
                badge: "YouTube Video / Short",
                icon: "fa-brands fa-youtube",
                color: "#ff0000"
            };
        } else if (lower.includes("tiktok.com")) {
            return {
                name: "TikTok",
                badge: "TikTok Video",
                icon: "fa-brands fa-tiktok",
                color: "#00f2fe"
            };
        } else if (lower.includes("twitter.com") || lower.includes("x.com")) {
            return {
                name: "Twitter / X",
                badge: "Twitter/X Video",
                icon: "fa-brands fa-x-twitter",
                color: "#ffffff"
            };
        }
        return null;
    }

    function updateUrlState() {
        const val = urlInput.value.trim();
        clearBtn.style.display = val ? "inline-flex" : "none";

        const platform = detectPlatform(val);
        if (platform) {
            detectedIcon.className = platform.icon;
            detectedIcon.style.color = platform.color;
            detectionBadge.style.display = "inline-flex";
            detectedName.innerHTML = `🎯 <strong>${platform.badge}</strong> detect ho gaya!`;
        } else {
            detectedIcon.className = "fa-solid fa-link";
            detectedIcon.style.color = "var(--text-muted)";
            detectionBadge.style.display = "none";
        }
    }

    urlInput.addEventListener("input", updateUrlState);

    clearBtn.addEventListener("click", () => {
        urlInput.value = "";
        updateUrlState();
        hideError();
        urlInput.focus();
    });

    pasteBtn.addEventListener("click", async () => {
        try {
            const clipText = await navigator.clipboard.readText();
            if (clipText) {
                urlInput.value = clipText.trim();
                updateUrlState();
                showToast("Link clipboard se paste kar diya!");
                // Trigger auto fetch
                fetchVideoDetails();
            } else {
                showToast("Clipboard me koi text nahi mila.");
            }
        } catch (err) {
            urlInput.focus();
            showToast("Clipboard access nahi mil saka. Kripya Ctrl+V karein.");
        }
    });

    urlInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            fetchVideoDetails();
        }
    });

    fetchBtn.addEventListener("click", fetchVideoDetails);

    function showError(msg) {
        errorMessage.textContent = msg;
        errorAlert.style.display = "flex";
    }

    function hideError() {
        errorAlert.style.display = "none";
    }

    function setFetchLoading(isLoading) {
        const btnText = fetchBtn.querySelector(".btn-text");
        const btnLoader = fetchBtn.querySelector(".btn-loader");
        if (isLoading) {
            btnText.style.display = "none";
            btnLoader.style.display = "inline-flex";
            fetchBtn.disabled = true;
        } else {
            btnText.style.display = "inline-flex";
            btnLoader.style.display = "none";
            fetchBtn.disabled = false;
        }
    }

    async function fetchVideoDetails() {
        const url = urlInput.value.trim();
        if (!url) {
            showError("Kripya ek valid video link enter karein!");
            urlInput.focus();
            return;
        }

        hideError();
        resultCard.style.display = "none";
        progressBox.style.display = "none";
        downloadReadyBox.style.display = "none";
        if (activePollTimer) clearInterval(activePollTimer);

        setFetchLoading(true);

        try {
            const resp = await fetch("/api/info", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url })
            });

            const data = await resp.json();

            if (!resp.ok || !data.success) {
                showError(data.error || "Video fetch nahi ho saka. Kripya link verify karein.");
                return;
            }

            renderVideoDetails(data.data);
        } catch (err) {
            showError("Network error: Server connect nahi ho saka.");
        } finally {
            setFetchLoading(false);
        }
    }

    function renderVideoDetails(data) {
        currentVideoData = data;
        
        // Thumbnail & Duration
        videoThumb.src = data.thumbnail || "https://placehold.co/600x340/111827/ffffff?text=Video+Preview";
        videoDuration.textContent = data.duration || "00:00";
        videoSourceTag.textContent = data.platform.toUpperCase();

        // Title & Uploader
        videoTitle.textContent = data.title || "Video";
        videoAuthorName.textContent = data.uploader || "Author";

        // Render Qualities
        qualitiesList.innerHTML = "";
        selectedQuality = data.qualities[0] ? data.qualities[0].id : "best";

        data.qualities.forEach((q, idx) => {
            const opt = document.createElement("div");
            opt.className = `quality-option ${idx === 0 ? "selected" : ""}`;
            opt.dataset.id = q.id;

            const iconClass = q.ext === "mp3" ? "fa-solid fa-music" : "fa-solid fa-video";

            opt.innerHTML = `
                <div class="quality-header">
                    <span class="quality-name"><i class="${iconClass}"></i> ${q.label}</span>
                    <span class="quality-badge">${q.badge}</span>
                </div>
                <span class="quality-desc">${q.desc}</span>
            `;

            opt.addEventListener("click", () => {
                document.querySelectorAll(".quality-option").forEach(el => el.classList.remove("selected"));
                opt.classList.add("selected");
                selectedQuality = q.id;
            });

            qualitiesList.appendChild(opt);
        });

        // Show Result Card
        resultCard.style.display = "block";
        resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // Start Download
    startDownloadBtn.addEventListener("click", async () => {
        if (!currentVideoData) return;

        startDownloadBtn.disabled = true;
        progressBox.style.display = "block";
        downloadReadyBox.style.display = "none";
        progressBarFill.style.width = "0%";
        progressPercent.textContent = "0%";
        progressStatusText.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Initializing download...';
        progressSpeed.innerHTML = '<i class="fa-solid fa-gauge-high"></i> Speed: Connecting...';
        progressEta.innerHTML = '<i class="fa-regular fa-clock"></i> ETA: --';

        try {
            const resp = await fetch("/api/download/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    url: currentVideoData.url,
                    quality: selectedQuality,
                    title: currentVideoData.title
                })
            });

            const resData = await resp.json();
            if (!resp.ok || !resData.success) {
                showError(resData.error || "Download start karne me error aaya.");
                startDownloadBtn.disabled = false;
                progressBox.style.display = "none";
                return;
            }

            const taskId = resData.task_id;
            pollDownloadProgress(taskId);
        } catch (err) {
            showError("Network error: Server response nahi de raha.");
            startDownloadBtn.disabled = false;
            progressBox.style.display = "none";
        }
    });

    let retryNotFoundCount = 0;

    function pollDownloadProgress(taskId) {
        if (activePollTimer) clearInterval(activePollTimer);
        retryNotFoundCount = 0;

        activePollTimer = setInterval(async () => {
            try {
                const resp = await fetch(`/api/download/status/${taskId}`);
                const data = await resp.json();

                if (!resp.ok || data.status === "error") {
                    // Allow up to 4 retries for initial task registration across workers
                    if (data.status === "not_found" && retryNotFoundCount < 4) {
                        retryNotFoundCount++;
                        return; // continue polling on next interval
                    }

                    clearInterval(activePollTimer);
                    startDownloadBtn.disabled = false;
                    progressStatusText.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Error';
                    showError(data.error || "Download me issue aaya. Kripya dubara try karein.");
                    return;
                }

                if (data.status === "downloading") {
                    const pct = Math.min(100, Math.max(0, data.percent || 0));
                    progressBarFill.style.width = `${pct}%`;
                    progressPercent.textContent = `${pct}%`;
                    progressStatusText.innerHTML = `<i class="fa-solid fa-cloud-arrow-down fa-bounce"></i> Downloading video...`;
                    progressSpeed.innerHTML = `<i class="fa-solid fa-gauge-high"></i> Speed: ${data.speed || "--"}`;
                    progressEta.innerHTML = `<i class="fa-regular fa-clock"></i> ETA: ${data.eta || "--"}`;
                } else if (data.status === "processing") {
                    progressBarFill.style.width = "100%";
                    progressPercent.textContent = "100%";
                    progressStatusText.innerHTML = `<i class="fa-solid fa-arrows-rotate fa-spin"></i> Finalizing file & audio merge...`;
                    progressSpeed.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i> Processing...`;
                    progressEta.innerHTML = `<i class="fa-regular fa-clock"></i> Almost ready!`;
                } else if (data.status === "completed") {
                    clearInterval(activePollTimer);
                    progressBarFill.style.width = "100%";
                    progressPercent.textContent = "100%";
                    progressStatusText.innerHTML = `<i class="fa-solid fa-circle-check" style="color:#10b981;"></i> Complete!`;
                    progressSpeed.innerHTML = `<i class="fa-solid fa-check"></i> Finished`;
                    progressEta.innerHTML = `--`;

                    startDownloadBtn.disabled = false;

                    // Trigger direct file download
                    const downloadUrl = `/api/download/file/${taskId}`;
                    window.location.href = downloadUrl;

                    // Show fallback download link
                    directDownloadLink.href = downloadUrl;
                    downloadReadyBox.style.display = "flex";

                    // Save to history
                    saveToHistory({
                        url: currentVideoData.url,
                        title: currentVideoData.title,
                        thumbnail: currentVideoData.thumbnail,
                        platform: currentVideoData.platform,
                        date: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    });

                    showToast("Download shuru ho gaya! 🎉");
                }
            } catch (e) {
                console.error("Progress poll error", e);
            }
        }, 800);
    }

    // History Logic
    const STORAGE_KEY = "one_click_downloader_history";

    function getHistory() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
        } catch {
            return [];
        }
    }

    function saveToHistory(item) {
        let hist = getHistory();
        // Avoid duplicate top
        hist = hist.filter(h => h.url !== item.url);
        hist.unshift(item);
        if (hist.length > 20) hist.pop(); // keep last 20
        localStorage.setItem(STORAGE_KEY, JSON.stringify(hist));
        renderHistory();
    }

    function renderHistory() {
        const hist = getHistory();
        if (!hist || hist.length === 0) {
            historyList.innerHTML = `<p class="empty-history-text">Abhi tak koi video download nahi kiya gaya hai.</p>`;
            return;
        }

        historyList.innerHTML = "";
        hist.forEach(h => {
            const div = document.createElement("div");
            div.className = "history-item";
            div.innerHTML = `
                <img src="${h.thumbnail || 'https://placehold.co/60x45'}" alt="Thumb" />
                <div class="history-item-meta">
                    <div class="history-item-title" title="${h.title}">${h.title}</div>
                    <div class="history-item-date">${h.platform.toUpperCase()} • ${h.date}</div>
                </div>
            `;
            div.style.cursor = "pointer";
            div.addEventListener("click", () => {
                urlInput.value = h.url;
                updateUrlState();
                closeHistory();
                fetchVideoDetails();
            });
            historyList.appendChild(div);
        });
    }

    function openHistory() {
        renderHistory();
        historyDrawer.classList.add("open");
        drawerOverlay.classList.add("active");
    }

    function closeHistory() {
        historyDrawer.classList.remove("open");
        drawerOverlay.classList.remove("active");
    }

    historyToggleBtn.addEventListener("click", openHistory);
    closeHistoryBtn.addEventListener("click", closeHistory);
    drawerOverlay.addEventListener("click", closeHistory);

    clearHistoryBtn.addEventListener("click", () => {
        localStorage.removeItem(STORAGE_KEY);
        renderHistory();
        showToast("History clear kar di gayi.");
    });

    // Check if URL has ?url= query param on load
    const urlParams = new URLSearchParams(window.location.search);
    const queryUrl = urlParams.get("url");
    if (queryUrl) {
        urlInput.value = queryUrl;
        updateUrlState();
        fetchVideoDetails();
    }

    // Register Service Worker for Monetag and PWA
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js').catch(err => {
                console.log('SW registration note:', err);
            });
        });
    }
});

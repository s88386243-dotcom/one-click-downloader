# 🚀 Deployment & Monetag Earning Guide for One Click Downloader

Is guide me hum dekhenge ki kaise aap **One Click Downloader** ko **24/7 internet par live deploy** kar sakte hain aur **Monetag (monetag.com)** ke ads lagakar earning shuru kar sakte hain.

---

## 🌐 PART 1: 24/7 Free Live Deployment

Video downloaders ke liye **Render.com** ya **Railway** sabse behtar aur free platform hain kyunki yahan background processing aur FFmpeg aasaani se chalte hain.

### Method: Render.com par Free Deploy karein (Recommended)

#### Step 1: Code ko GitHub par Dalein
1. [GitHub.com](https://github.com/) par jayein aur ek naya repository banayein (e.g. `one-click-downloader`).
2. Apne Downloads folder wale project ko GitHub par push karein:
   ```powershell
   cd C:\Users\hp\Downloads\one-click-downloader
   git init
   git add .
   git commit -m "One Click Downloader ready for deploy"
   git branch -M main
   git remote add origin https://github.com/AAPKA_USERNAME/one-click-downloader.git
   git push -u origin main
   ```

#### Step 2: Render.com par Account Banayein
1. [Render.com](https://render.com/) par jayein aur **Sign Up (Free)** karein (GitHub se direct login kar sakte hain).
2. Dashboard me **"New +"** par click karein aur **"Web Service"** select karein.
3. Apni GitHub repository `one-click-downloader` ko connect karein.

#### Step 3: Settings Configure Karein
- **Name:** `one-click-downloader` (ya jo aap chahein)
- **Region:** Singapore ya Frankfurt (fast response ke liye)
- **Branch:** `main`
- **Runtime:** **Docker** (Recommended - kyunki Dockerfile me FFmpeg automatically install ho jata hai)
  - *Ya fir agar Python chunte hain:*
    - **Build Command:** `pip install -r requirements.txt`
    - **Start Command:** `gunicorn app:app --workers 2 --timeout 180 --bind 0.0.0.0:$PORT`
- **Instance Type:** **Free**

#### Step 4: Click "Create Web Service"
2 se 3 minute me aapki website live ho jayegi aur aapko ek free live link mil jayega:
👉 `https://one-click-downloader-xxxx.onrender.com`

---

## 💰 PART 2: Monetag (monetag.com) Setup & Earning

Monetag (formerly PropellerAds) downloaders aur tools websites ke liye sabse zyada earning dene wala ad network hai. Isme bina kisi approval delay ke ads chalu ho jate hain.

### Step 1: Monetag par Account Banayein
1. [Monetag.com](https://monetag.com/) par jayein.
2. **"Sign Up"** par click karein aur **"Publisher"** account banayein.
3. Apna email verify karein aur dashboard me login karein.

### Step 2: Website Add Karein
1. Monetag Dashboard me left menu se **"Sites"** par click karein.
2. **"Add Site"** button dabayein.
3. Apni live website ka URL dalein (e.g. `https://one-click-downloader-xxxx.onrender.com`).
4. **"Add Site"** par click karein.

### Step 3: Site Verify Karein
Monetag aapko verify karne ke liye ek **HTML Meta Tag** dega:
`<meta name="monetag" content="xxxxxx">`

1. Apne project me [`templates/index.html`](templates/index.html) file kholein.
2. Line 18 ke aas-paas jahan likha hai:
   ```html
   <!-- <meta name="monetag" content="YOUR_MONETAG_VERIFICATION_CODE_HERE"> -->
   ```
   Wahan apna code paste kar dein.
3. GitHub par commit & push karein (Render automatically 1 minute me update ho jayega).
4. Monetag dashboard par jakar **"Verify"** button click karein. Aapki site verify ho jayegi! ✅

### Step 4: Best Earning Ad Formats Create Karein

Downloader websites par sabse zyada kamai in formats se hoti hai:

#### 1. OnClick (Popunder) - Highest CPM ($3 - $10+ per 1000 visits)
- Monetag dashboard me **"Add Zone"** -> **"OnClick (Popunder)"** select karein.
- Jo JavaScript code mile, use [`templates/index.html`](templates/index.html) me `<!-- PASTE_MONETAG_POPUNDER_SCRIPT_HERE -->` ki jagah paste karein.
- Jab bhi koi user website par kahin bhi ya download button click karega, background me ad open hoga aur aapko har click par earning hogi.

#### 2. In-Page Push / Banner Ad
- Monetag dashboard me **"In-Page Push"** ya **"Banner"** zone banayein.
- Script ko [`templates/index.html`](templates/index.html) ke andar `monetag-banner-slot` me paste karein:
  ```html
  <div class="ad-slot" id="monetag-banner-slot">
      <!-- Aapka Monetag Banner Code Yahan Aayega -->
  </div>
  ```

#### 3. Vignette Banner (Mobile friendly fullscreen ad)
- Mobile users ke liye Vignette ad high revenue deta hai. Iska code bhi `<head>` me paste kar sakte hain.

---

## 📈 Earning Tips (Zyada Kamai Ke Tarike)

1. **Traffic Sources:**
   - Instagram bio me "Free Instagram Reel Downloader" link lagayein.
   - YouTube shorts ke description ya comment me "1-Click Video Downloader" link share karein.
   - Facebook groups me post karein.
2. **Payouts:**
   - Monetag weekly payouts deta hai via **PayPal, Bank Wire Transfer, WebMoney, ya Payoneer**. Minimum payout sirf **$5** hai!

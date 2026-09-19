# 🚀 Deployment Guide — RepoTalks AI

RepoTalks AI supports two deployment architectures:
1. **Split Deployment (Recommended for Free Tier)**: Next.js frontend hosted on **Vercel** + FastAPI Python backend hosted on **Render Free Tier**.
2. **Unified Deployment**: Single Web Service on Render running Docker, serving both the API and the compiled static frontend.

---

## 🌟 Method 1: Vercel (Frontend) + Render (Backend) — 100% Free

### Step 1: Deploy Backend on Render (Free Tier)
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** $\rightarrow$ **Web Service**.
3. Connect your GitHub repository.
4. Configure the service:
   - **Name**: `repotalks-backend`
   - **Region**: Choose closest to you (e.g. Frankfurt, Oregon, Ohio, Singapore)
   - **Environment**: `Docker`
   - **Instance Type**: `Free`
5. Set Environment Variables:
   - `PORT`: `8080`
   - `DATA_DIR`: `/var/data`
   - `GEMINI_API_KEY`: *(Optional)* Your server-side Gemini API key (users can also provide BYOK in the app Settings)
   - `GROQ_API_KEY`: *(Optional)* Your server-side Groq API key
   - `CORS_ORIGINS`: `https://<your-vercel-domain>.vercel.app` *(Optional: `*.vercel.app` domains are automatically supported by regex)*
6. Click **Create Web Service**.
7. Once deployed, copy your backend URL (e.g., `https://repotalks-backend.onrender.com`).

> [!NOTE]
> **Render Free Tier Storage & Spin-down**:
> - Free web services use ephemeral container storage. Ingested repositories and SQLite embeddings are preserved during active use, but reset if the free service spins down after 15 minutes of inactivity.
> - The first request after a sleep period may take 30-50 seconds as Render wakes the container.

---

### Step 2: Deploy Frontend on Vercel (Free)
1. Log in to [Vercel Dashboard](https://vercel.com).
2. Click **Add New...** $\rightarrow$ **Project**.
3. Import your GitHub repository.
4. In the configuration screen:
   - **Root Directory**: Click *Edit* and select `frontend`.
   - **Framework Preset**: `Next.js` (automatically detected).
   - **Build Command**: `npm run build`
5. In **Environment Variables**, add:
   - `NEXT_PUBLIC_API_URL`: Paste your Render backend URL (e.g. `https://repotalks-backend.onrender.com`).
6. Click **Deploy**.
7. Once complete, open your Vercel URL (e.g. `https://repotalks.vercel.app`).

---

## 📦 Method 2: Single Unified Service on Render (Docker)

If you prefer everything in a single URL on Render:

1. Push code to GitHub.
2. In Render, select **New +** $\rightarrow$ **Blueprint**.
3. Select the repository (Render will read `render.yaml`).
4. Apply the Blueprint.
5. In the Render Dashboard under **Environment**, configure:
   - `GEMINI_API_KEY` (secret)
   - `GROQ_API_KEY` (secret)

---

## 💻 Local Development Instructions

### Running Both Frontend and Backend Locally
1. **Backend** (PowerShell):
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --reload-dir backend
   ```

2. **Frontend** (Separate terminal):
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```
   Open `http://localhost:3000`.

### Local Docker Run (Unified Image)
```bash
docker build -t repotalks .
docker run -p 8080:8080 -e GEMINI_API_KEY="your-key" repotalks
```
Open `http://localhost:8080`.

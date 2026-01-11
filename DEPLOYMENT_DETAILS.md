# Deployment Details

## Live URLs
- **Backend API**: https://rate-dashboard.onrender.com
- **API Documentation**: https://rate-dashboard.onrender.com/docs
- **Frontend**: Check your [Netlify Dashboard](https://app.netlify.com) for the live URL (typically `https://<site-name>.netlify.app`).

## Infrastructure Reference

### Backend (Render)
- **Service Type**: Web Service
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn api:app --host 0.0.0.0 --port $PORT`
- **Database**: `backend/clean_rates.db` (SQLite, Read-Only in cloud)
    - *To update data*: Replace the `.db` file locally, commit, and push.

### Frontend (Netlify)
- **Build Command**: `npm run build`
- **Publish Directory**: `dist`
- **Configuration**: `netlify.toml`
    - Handles SPA redirects (`/*` -> `/index.html`)
    - Enforces Production API URL (`VITE_API_BASE_URL`)

## How to Update
1.  **Code Changes**: Simply commit and push to the `main` branch.
2.  ** Automatic Deployment**:
    - **Render** watches for changes in `backend/` and redeploys the API.
    - **Netlify** watches the repo and rebuilds the React app.

# AI Sales Assistant — Frontend

Vanilla HTML / CSS / JavaScript frontend for the AI Sales Assistant multi-tenant SaaS platform.

---

## Project Structure

```
frontend/
├── index.html                        ← Root splash / redirect page
├── pages/
│   ├── login.html                    ← Unified login page (all roles)
│   └── superadmin/
│       └── dashboard.html            ← Super Admin dashboard
├── assets/
│   ├── css/
│   │   ├── global.css                ← Design tokens, reset, utilities
│   │   ├── login.css                 ← Login page styles
│   │   └── superadmin.css            ← Dashboard shell styles
│   └── js/
│       ├── config.js                 ← API URL + storage key config
│       ├── api.js                    ← Backend API client
│       ├── auth.js                   ← Auth state + route guards
│       ├── login.js                  ← Login page controller
│       └── superadmin.js             ← Dashboard controller
└── README.md
```

---

## Running the Frontend

No build step required — serve with any static file server.

### Option 1: VS Code Live Server

Install the **Live Server** extension in VS Code.  
Right-click `index.html` → **Open with Live Server**.  
Default URL: `http://127.0.0.1:5500`

### Option 2: Python HTTP server

```bash
cd frontend
python -m http.server 5500
```

Visit: http://localhost:5500

### Option 3: Node.js serve

```bash
npx serve frontend -p 5500
```

---

## Environment Configuration

The only file to change when switching environments is:

```
frontend/assets/js/config.js
```

Change `API_BASE_URL` to match your backend:

```javascript
const CONFIG = Object.freeze({
  API_BASE_URL: 'http://localhost:8000',  // ← change this per environment
  ...
});
```

**Do not scatter API URLs across multiple files.**

---

## Login Flow

1. Open `pages/login.html`
2. Enter Super Admin email and password from your `.env`
3. Frontend calls: `POST /api/v1/auth/login`
4. On success: JWT stored, user redirected to role-appropriate dashboard
5. `SUPER_ADMIN` → `pages/superadmin/dashboard.html`

---

## Route Guards

Every protected page calls `Auth.requireAuth()` on load.

Unauthenticated users are immediately redirected to login.

The backend enforces authorization independently on every API call.

---

## CORS

The backend must include the frontend origin in `CORS_ORIGINS`:

```env
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
```

---

## Design

- Light mode only
- Professional, minimal SaaS aesthetic
- Primary accent: Indigo `#4F46E5`
- Typography: Inter (Google Fonts)
- No dark mode
- No framework dependencies (no React, Vue, Bootstrap, Tailwind)

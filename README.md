<p align="center">
  <img src="frontend/src/assets/logo.png" alt="BU Big Backs logo" width="180">
</p>

<h1 align="center">BU Big Backs</h1>

<p align="center"><b>Big eats, big backs!</b><br>Find, filter and rate the best food in BU's dining halls.</p>

---

## Why we built it

BU's dining hall menus live on separate sites in different formats, which makes it hard to find a meal that fits your diet, especially if you have allergies or dietary restrictions. BU Big Backs pulls every hall's menu into one app so students can search, filter by allergen and diet, and see what other students love. It also gives dining staff data on how well each hall serves students with dietary needs.

It covers the three Fresh Food Co. halls: **West Campus**, **Marciano Commons** and **Warren Towers**.

## Features

| Feature | Where |
| --- | --- |
| **Search and filter** every dish on campus by name, hall, meal, station, diet (vegetarian, vegan, gluten-free, halal, Cool Food) or allergen | `/` |
| **Dish details**: description, ingredients, nutrition facts, allergens and student comments | `/dish/:id` |
| **Most loved dishes**: tap the heart to like a dish; the home page ranks the favorites | `/` |
| **Near me**: what's being served at the closest halls, matched to your restrictions | `/near-me` |
| **Dining hall stats**: compares halls on dietary coverage and nutrition over time, so staff can spot gaps | `/stats` |
| **Anonymous feedback**: rate a meal, comment on a dish or request a new one | `/feedback` |
| **Hall forums**: anonymous threads for each dining hall | `/forums` |
| **Profile and settings**: dietary restrictions, allergies, home hall and meal plan | `/profile`, `/settings` |
| **Google sign-in** through Supabase Auth | header |

## Tech stack

- **Frontend:** TanStack Start, React 19, TypeScript, Tailwind CSS, shadcn/ui
- **Database and auth:** Supabase (Postgres, Google OAuth), migrations managed with Drizzle
- **Backend API:** Python standard-library HTTP server, with pandas and numpy for the menu analysis
- **Data source:** BU's public Dining API

## Project structure

```
BU-Big-Backs-v2/
├── backend/
│   ├── server.py                 # JSON API + daily menu poll
│   ├── dining_poller.py          # pulls a 7-day menu window into backend/data/
│   ├── collect_menu_dfs.py       # menu snapshots → dataframes
│   ├── marciano_json_to_df.py
│   ├── dining_diet_analysis.py   # per-hall diet and nutrition summary (/api/stats)
│   ├── API.md                    # full API reference
│   └── start.sh / start.bat      # sets up a venv and starts the server
└── frontend/
    ├── src/routes/               # one file per page (file-based routing)
    ├── src/components/           # DishCard, SiteHeader, shadcn/ui
    ├── src/lib/                  # API client, halls, profile, auth, stats helpers
    ├── src/assets/               # logo
    ├── public/dishes/            # dish photos
    ├── drizzle/migrations/       # Supabase database setup
    └── scripts/                  # dish image generation
```

## Getting started

You'll need **Python 3** and **[Bun](https://bun.sh)**.

### 1. Backend API

```sh
cp .env.example .env      # at the repo root; set API_KEY to your BU Dining API key
cd backend
./start.sh                # Windows: start.bat
```

`start.sh` creates a virtualenv in `backend/.venv`, installs `requirements.txt` and starts the API on <http://localhost:8000>. On startup, and then once a day, the server polls BU's menus for today plus the next 6 days into `backend/data/`.

### 2. Web app

In a second terminal:

```sh
cd frontend
cp .env.example .env
bun install
bun run dev               # http://localhost:8080
```

In development, Vite proxies `/api` to the backend on port 8000, so leave `VITE_API_URL` empty.

| Command | What it does |
| --- | --- |
| `bun run build` | Production build |
| `bun run test` | Run the tests (Vitest) |
| `bun run lint` | Lint with ESLint |
| `bun run images` | Generate AI dish photos (needs `POLLINATIONS_KEY` in `frontend/.env`) |

### Google sign-in (optional)

The app works without signing in. To turn on Google sign-in, create a Google OAuth client, add `https://<your-project>.supabase.co/auth/v1/callback` as a redirect URI, then enable the Google provider in Supabase under **Authentication → Providers** and add `http://localhost:8080/**` to the redirect URLs.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/health` | Service, poller and data status |
| `GET /api/metadata` | Filter options: halls, campuses, dates, meals, stations, diets |
| `GET /api/menu` | Filtered menu rows (`?group=true` nests by hall → meal → station) |
| `GET /api/items/{location}/{item_id}` | Full item detail (`?allergens=true` adds allergens) |
| `GET /api/stats` | Per-hall dietary and nutrition summary |

See [`backend/API.md`](backend/API.md) for every endpoint, filter and config option.

## Security

Keep real keys in `.env` files and never commit them. The BU Dining `API_KEY` and `POLLINATIONS_KEY` are secrets; only the Supabase publishable values in `frontend/.env.example` are safe to share.

# 🚗 Vehicle Data Scrapers

This project automates the process of **scraping vehicle data** from two major European platforms — **AutoScout24** and **Mobile.de** — and **saves the data directly into PostgreSQL** for analysis or dashboard use.

It includes **daily** and **hourly** scrapers, and a built-in database setup utility.

---

## 📁 Project Overview

### ✅ Features
- 🔍 Scrapes detailed vehicle listings from **AutoScout24** and **Mobile.de**
- 🧩 Supports both **daily full scrapes** and **hourly incremental updates**
- 🗄️ Automatically saves all scraped data into a **PostgreSQL database**
- 🧱 Includes automatic database and table creation
- ⚙️ Built with modular structure for easy maintenance

---

## 🧰 Tech Stack
- **Python 3.10+**
- **PostgreSQL** (as database)
- **Scrapy / Requests / BeautifulSoup / Parsel**
- **psycopg2** for database integration
- **pandas** for data transformation

---

## 🚀 Setup Instructions

### 1️⃣ Clone the Repository
```bash
git clone <your-repo-url>
cd <your-project-folder>
```

---

### 2️⃣ Create and Activate Virtual Environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

### 3️⃣ Install Dependencies
Make sure all required Python packages are installed:
```bash
pip install -r requirements.txt
```

---

### 4️⃣ Configure Database Connection
Inside your project, open the **.env** or **constants.env** file and update PostgreSQL credentials(PLUS change the threads limits for both scrappers):

```bash
SCRAPE_DO_TOKEN=SCRAPE_DO_TOKEN

WEBSHARE_PROXY_USER=WEBSHARE_PROXY_USER
WEBSHARE_PROXY_PASSWORD=WEBSHARE_PROXY_PASSWORD
WEBSHARE_PROXY_HOST=WEBSHARE_PROXY_HOST
WEBSHARE_PROXY_PORT=WEBSHARE_PROXY_PORT

DB_NAME=vehicles_db
DB_USER=postgres
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=5432

AUTOSCOUT_THREAD_COUNT=10
MOBILE_THREAD_COUNT=5
```

> 🧠 The script automatically ensures that the database and required tables exist. No manual setup needed.

---

## 🏃 How to Run

The project uses a **launcher system** via `main.py`. You can run any scraper by passing an argument.

---

### ▶️ AutoScout24 Daily Scraper
Runs a full scrape of all listings:
```bash
python main.py autoscout24_complete
```

---

### ▶️ Mobile.de Daily Scraper
Runs a full scrape of all listings:
```bash
python main.py mobile_complete
```

---

### ⏱ AutoScout24 Hourly Scraper
Scrapes new:
```bash
python main.py autoscout24_recent
```

---

### ⏱ Mobile.de Hourly Scraper
Scrapes new :
```bash
python main.py mobile_recent
```

---

## ✅ Available Launchers

| Command                | Description                                          |
|------------------------|------------------------------------------------------|
| `autoscout24_complete`          | Run scraper for AutoScout24 to extract complete data |
| `mobile_complete`               | Run scraper for Mobile.de to extract complete data   |
| `autoscout24_recent`   | Run scraper for AutoScout24 to extract recent data   |
| `mobile_recent`        | Run scraper for Mobile.de to extract recent data     |

---

## 🧩 Code Entry Point

Here’s the main entry script:
```python
from scrapper import autoscout24_complete, autoscout24_recent, mobile_de_complete, mobile_de_recent
from database.db import ensure_database_exists
import sys

if __name__ == '__main__':
    arguments = sys.argv[1:]
    ensure_database_exists()
    # arguments = ['mobile']
    if arguments[0] == 'autoscout24_complete':
        autoscout24_complete.main()
    elif arguments[0] == 'mobile_complete':
        mobile_de_complete.main()
    elif arguments[0] == 'autoscout24_recent':
        autoscout24_recent.main()
    elif arguments[0] == 'mobile_recent':
        mobile_de_recent.main()
    else:
        print('Available launcher names are: \n- autoscout24_complete\n- mobile_complete\n- autoscout24_recent\n- mobile_recent')

```

---

## 🧠 Notes
- Make sure PostgreSQL is running before starting any scraper.
- Each scraper automatically handles retries, pagination, and structured data parsing.
- All data is **stored in PostgreSQL** tables with proper indexing.

---

## 📦 Output
- Data is stored directly in the PostgreSQL database.
- You can query the data using any SQL client or connect to a dashboard tool (e.g. Metabase, Superset).

---

## 🤝 Contributing
Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## 📄 License
This project is licensed under the **Codifyrs License**.

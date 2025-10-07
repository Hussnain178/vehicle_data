# 🚗 Vehicle Data Scrapers

This project contains scrapers for collecting vehicle data from **AutoScout24** and **Mobile.de**, and a utility to combine both datasets.

---

## 🚀 How to Run the Scraper

### 1. Install Requirements
Before running any scrapers, make sure all required dependencies are installed:

```bash
pip install -r requirements.txt
```

---

### 2. Run AutoScout24 Scraper
To run the **AutoScout24** scraper, use:

```bash
python main.py autoscout24
```

This command will execute the `autoscout24_final` scraper and start collecting data from AutoScout24.

---

### 3. Run Mobile.de Scraper
To run the **Mobile.de** scraper, use:

```bash
python main.py mobile
```

This command will execute the `mobile_de_final` scraper and start collecting data from Mobile.de.

---

### 4. Combine Both Scraped Files
After both scrapers have finished, you can combine their results into a single dataset by running:

```bash
python main.py combine
```

This command will execute the `combine_data()` function from `utils/combined_data.py` to merge both scraped files.

---

✅ **Available launcher names**:
- `autoscout24`
- `mobile`
- `combine`

Example:
```bash
python main.py autoscout24
```

---

"""
Step 1 of 2: download the REAL public datasets used by CoursePath Planner into data/raw/.

  py -3 download_data.py

Sources
-------
1. University of Illinois Urbana-Champaign (UIUC) Course Catalog, scraped from the official
   Course Explorer API (https://courses.illinois.edu/cisapp/) and published by Wade Fagen-Ulmschneider:
   https://github.com/wadefagen/datasets/tree/main/course-catalog
     -> real courses, credit hours, prerequisites (in the description text), sections and
        their enrollment status, and which terms each course runs.

2. UIUC GPA Dataset: real grade distributions (A+ ... F, W) for every course section,
   released by the university under FOIA / Senate item EP.25.072:
   https://github.com/wadefagen/datasets/tree/main/gpa
     -> pass rates, average GPA, withdrawal rates = "historical grade & pass-rate data".

3. FiveThirtyEight "college-majors": earnings and employment of graduates by major
   (US Census American Community Survey 2010-2012):
   https://github.com/fivethirtyeight/data/tree/master/college-majors
     -> median salary, unemployment, share in college-level jobs = "career outcome data".

After downloading, run build_data.py to turn the raw files into the small files the agents read.
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")

WADE = "https://raw.githubusercontent.com/wadefagen/datasets/main"
FILES = {
    # catalog: the last two years of Fall/Spring terms (tells us when each course is offered)
    "catalog-2025-sp.csv": f"{WADE}/course-catalog/data/2025-sp.csv",
    "catalog-2025-fa.csv": f"{WADE}/course-catalog/data/2025-fa.csv",
    "catalog-2026-sp.csv": f"{WADE}/course-catalog/data/2026-sp.csv",
    "catalog-2026-fa.csv": f"{WADE}/course-catalog/data/2026-fa.csv",
    "catalog-2027-sp.csv": f"{WADE}/course-catalog/data/2027-sp.csv",   # upcoming registration term
    "uiuc-gpa-dataset.csv": f"{WADE}/gpa/uiuc-gpa-dataset.csv",
    "recent-grads.csv": "https://raw.githubusercontent.com/fivethirtyeight/data/master/college-majors/recent-grads.csv",
    "grad-students.csv": "https://raw.githubusercontent.com/fivethirtyeight/data/master/college-majors/grad-students.csv",
}


def main():
    os.makedirs(RAW, exist_ok=True)
    for name, url in FILES.items():
        dest = os.path.join(RAW, name)
        if os.path.exists(dest) and "--force" not in sys.argv:
            print(f"  already have {name}")
            continue
        print(f"  downloading {name} ...", end=" ", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "CoursePathPlanner-student-project"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
            f.write(r.read())
        os.replace(dest + ".part", dest)
        print(f"{os.path.getsize(dest) / 1e6:.1f} MB")
    print("Done. Now run:  py -3 build_data.py")


if __name__ == "__main__":
    main()

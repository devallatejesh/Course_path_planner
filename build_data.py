"""
Step 2 of 2: turn the raw downloaded datasets (data/raw/) into the files the agents read (data/).

  py -3 build_data.py

Outputs
  data/courses.csv          real UIUC courses used in the plan: credits, real prerequisite text and the
                            parsed prerequisite rules, terms offered, Fall 2026 / Spring 2027 offering,
                            average GPA, pass rate, withdrawal rate, typical enrolment, career tracks
  data/grade_history.csv    real grade distributions per course per term (subset of the UIUC GPA dataset)
  data/career_outcomes.csv  real earnings & employment by major (FiveThirtyEight / US Census ACS), mapped to tracks
  data/requirements.json    degree rules (modelled on the UIUC B.S. Computer Science curriculum)
  data/tracks.json          career tracks, the keywords the Goal Agent understands, and their related majors
  data/students.json        demo students (fictional people, real course codes)
  data/sources.json         where every dataset came from + row counts
"""
import csv
import json
import os
import re
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
OUT = os.path.join(HERE, "data")
TERMS = ["2025-sp", "2025-fa", "2026-sp", "2026-fa", "2027-sp"]

# ---------------------------------------------------------------------------
# Degree structure (modelled on the UIUC Computer Science B.S. major requirements)
# ---------------------------------------------------------------------------
REQUIRED = {
    "core": ["CS 100", "CS 124", "CS 128", "CS 173", "CS 211", "CS 222", "CS 225", "CS 233", "CS 341",
             "CS 357", "CS 361", "CS 374", "CS 421"],
    "project": ["CS 427"],                         # team software project
    "math": ["MATH 221", "MATH 231", "MATH 241", "MATH 257"],
    "science": ["PHYS 211", "PHYS 212"],
    "writing": ["RHET 105"],
}
GENED_POOL = ["ECON 102", "ECON 103", "PSYC 100", "SOC 100", "PHIL 101", "PHIL 105", "ANTH 101",
              "ENGL 101", "MUS 133", "CMN 101", "HIST 172"]
GENED_COUNT = 4
ELECTIVES_MIN = 6
EXCLUDE_ELECTIVES = {"CS 491", "CS 492", "CS 493", "CS 494", "CS 495", "CS 496", "CS 497", "CS 498", "CS 499",
                     "CS 400", "CS 401", "CS 402", "CS 403", "CS 404", "CS 405", "CS 406", "CS 409"}

TRACKS = {
    "ai_ml": {"label": "AI / Machine Learning", "roles": ["ML Engineer", "AI Research Engineer", "Applied Scientist"],
              "majors": ["MATHEMATICS AND COMPUTER SCIENCE", "COMPUTER SCIENCE"],
              "keywords": ["ai", "artificial intelligence", "machine learning", "ml", "deep learning", "neural",
                           "nlp", "natural language", "language model", "llm", "computer vision", "vision", "robot",
                           "robotics", "chatbot", "reinforcement", "intelligent", "agents", "generative", "learning"]},
    "data_science": {"label": "Data Science & Analytics", "roles": ["Data Scientist", "Data Analyst", "Analytics Engineer"],
                     "majors": ["STATISTICS AND DECISION SCIENCE", "MANAGEMENT INFORMATION SYSTEMS AND STATISTICS"],
                     "keywords": ["data", "data science", "analytics", "analyst", "statistics", "statistical",
                                  "big data", "visualization", "dashboards", "insights", "forecasting", "database",
                                  "databases", "sql", "mining", "data mining", "text", "retrieval", "information"]},
    "cybersecurity": {"label": "Cybersecurity", "roles": ["Security Analyst", "Penetration Tester", "Security Engineer"],
                      "majors": ["COMPUTER ADMINISTRATION MANAGEMENT AND SECURITY", "COMPUTER AND INFORMATION SYSTEMS"],
                      "keywords": ["security", "cyber", "cybersecurity", "hacking", "ethical hacking", "crypto",
                                   "cryptography", "forensics", "penetration", "malware", "privacy", "attacks",
                                   "secure", "threat", "verification", "ctf", "defend"]},
    "software_dev": {"label": "Software Development", "roles": ["Software Engineer", "Full-Stack Developer", "Mobile Developer"],
                     "majors": ["COMPUTER SCIENCE", "COMPUTER PROGRAMMING AND DATA PROCESSING"],
                     "keywords": ["software", "developer", "development", "full stack", "full-stack", "web", "app",
                                  "apps", "mobile", "android", "backend", "frontend", "startup", "product", "coding",
                                  "programmer", "programming", "testing", "devops", "engineering", "compilers"]},
    "cloud_systems": {"label": "Cloud & Systems", "roles": ["Cloud Engineer", "Site Reliability Engineer", "Systems Engineer"],
                      "majors": ["COMPUTER ENGINEERING", "COMPUTER NETWORKING AND TELECOMMUNICATIONS"],
                      "keywords": ["cloud", "aws", "azure", "systems", "distributed", "infrastructure", "iot",
                                   "internet of things", "embedded", "kubernetes", "operating systems", "operating system",
                                   "networks", "networking", "parallel", "sre", "scalable", "hardware", "architecture"]},
    "ux_design": {"label": "UX & Interaction Design", "roles": ["UX Designer", "Product Designer", "Game Developer"],
                  "majors": ["COMMERCIAL ART AND GRAPHIC DESIGN", "INFORMATION SCIENCES"],
                  "keywords": ["ux", "ui", "design", "designer", "user experience", "interface", "interfaces", "hci",
                               "human-computer", "game", "games", "creative", "visual", "interaction", "graphics",
                               "virtual reality", "accessibility", "users"]},
}

LETTER_POINTS = {"A+": 4.0, "A": 4.0, "A-": 3.67, "B+": 3.33, "B": 3.0, "B-": 2.67, "C+": 2.33, "C": 2.0,
                 "C-": 1.67, "D+": 1.33, "D": 1.0, "D-": 0.67, "F": 0.0}
CODE_RE = re.compile(r"\b([A-Z]{2,4}) (\d{3})\b")


def clean(s):
    return (s or "").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').strip()


def read(name):
    # the GPA file mixes encodings (instructor names), so undecodable bytes are replaced
    with open(os.path.join(RAW, name), encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 1. Course catalog (5 terms) -> one record per course
# ---------------------------------------------------------------------------
def load_catalog():
    courses, terms = {}, defaultdict(set)
    sections = defaultdict(lambda: defaultdict(set))
    for t in TERMS:
        for r in read(f"catalog-{t}.csv"):
            code = f"{r['Subject']} {r['Number']}"
            terms[code].add(t)
            sections[code][t].add(r["Section"])
            courses[code] = r          # keep the most recent description
    return courses, terms, sections


def parse_credits(s):
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else 3


def parse_prereqs(info):
    """'Prerequisite: One of CS 173, MATH 213; CS 225; credit or concurrent registration in MATH 257.'
       -> [{'any': ['CS 173','MATH 213'], 'concurrent': False}, {'any': ['CS 225']...}, ...]"""
    m = re.search(r"Prerequisites?:(.*)", info or "", re.S)
    if not m:
        return [], ""
    text = m.group(1)
    text = re.split(r"(?:Restricted to|Junior standing|Senior standing|Sophomore standing|Approval|Consent of)", text)[0]
    text = text.strip().rstrip(".")
    groups = []
    for part in re.split(r";|\.\s+(?=[A-Z])|, and (?=one of|One of|credit|[A-Z]{2,4} \d)| and (?=one of|credit)", text):
        if "recommend" in part.lower():
            continue                                  # "CS 441 completion is recommended" is not a rule
        conc = "concurrent" in part.lower()
        if " and " in part and "one of" not in part.lower() and " or " not in part:
            for sub in part.split(" and "):           # "CS 225 and CS 233" -> two separate rules
                codes = [f"{a} {b}" for a, b in CODE_RE.findall(sub)]
                if codes:
                    groups.append({"any": codes, "concurrent": conc})
            continue
        codes = [f"{a} {b}" for a, b in CODE_RE.findall(part)]
        if codes:
            groups.append({"any": codes, "concurrent": conc})
    return groups, clean("Prerequisite: " + m.group(1).strip())


# ---------------------------------------------------------------------------
# 2. GPA dataset -> pass rate, average GPA, withdrawal rate, typical enrolment per term
# ---------------------------------------------------------------------------
def load_grades(wanted):
    per_term = defaultdict(lambda: defaultdict(int))
    for r in read("uiuc-gpa-dataset.csv"):
        code = f"{r['Subject']} {r['Number']}"
        if code not in wanted or r["YearTerm"] < "2021-fa" or r["Term"] not in ("Fall", "Spring"):
            continue
        row = per_term[(code, r["YearTerm"])]
        for g in list(LETTER_POINTS) + ["W"]:
            row[g] += int(r[g] or 0)
        row["sections"] += 1
    return per_term


def summarise(rows):
    tot = sum(sum(r[g] for g in list(LETTER_POINTS) + ["W"]) for r in rows)
    graded = sum(sum(r[g] for g in LETTER_POINTS) for r in rows)
    passed = sum(sum(r[g] for g in LETTER_POINTS if g != "F") for r in rows)
    gp = sum(sum(r[g] * LETTER_POINTS[g] for g in LETTER_POINTS) for r in rows)
    w = sum(r["W"] for r in rows)
    return {"students": tot, "pass_rate": passed / tot if tot else None,
            "avg_gpa": gp / graded if graded else None, "withdraw_rate": w / tot if tot else 0}


# Stricter keywords used to tag real courses (from their official title + description) with career tracks
COURSE_KEYWORDS = {
    "ai_ml": ["artificial intelligence", "machine learning", "deep learning", "neural", "natural language",
              "computer vision", "reinforcement learning", "robot", "learning algorithms", "photography"],
    "data_science": ["data mining", "database", "databases", "data science", "statistical", "text information",
                     "retrieval", "social and information network", "visualization", "big data", "data management",
                     "analytics", "bioinformatics"],
    "cybersecurity": ["security", "cryptograph", "privacy", "attacks", "verification", "trustworthy", "secure",
                      "ethical"],
    "software_dev": ["software", "programming languages", "web", "mobile", "testing", "formal software",
                     "compiler", "game development", "agile", "software engineering"],
    "cloud_systems": ["distributed", "cloud", "operating system", "operating systems", "networks", "networking",
                      "embedded", "internet of things", "parallel", "real-time", "system organization",
                      "computer architecture", "hardware", "system programming"],
    "ux_design": ["user interface", "human-computer", "game", "graphics", "extended reality", "virtual reality",
                  "augmented reality", "interaction design", "audio", "accessibility", "visual design"],
}


def tracks_of(name, desc):
    text = " " + (name + " " + desc).lower() + " "
    scores = {}
    for t, words in COURSE_KEYWORDS.items():
        hits = sum(1 for k in words if k in text) + 2 * sum(1 for k in words if k in name.lower())
        if hits:
            scores[t] = hits
    if not scores:
        return []
    best = sorted(scores.items(), key=lambda x: -x[1])
    top = best[0][1]
    return [t for t, sc in best[:2] if sc == top or (sc >= 2 and sc >= 0.6 * top)]


def main():
    t0 = time.time()
    print("Reading course catalog (5 terms)...")
    catalog, terms, sections = load_catalog()

    required = [c for v in REQUIRED.values() for c in v]
    elective_codes = sorted(
        c for c, r in catalog.items()
        if r["Subject"] == "CS" and r["Number"].startswith("4") and c not in EXCLUDE_ELECTIVES
        and c not in required and "undergraduate hours" in (r["Section Info"] + r["Credit Hours"] + r["Description"]).lower()
        and {"2026-fa", "2027-sp"} & terms[c])
    wanted = set(required) | set(GENED_POOL) | set(elective_codes)
    missing = [c for c in wanted if c not in catalog]
    if missing:
        print("  warning: not in catalog:", missing)
        wanted -= set(missing)

    print("Reading UIUC GPA dataset (grade distributions)...")
    per_term = load_grades(wanted)
    by_course = defaultdict(list)
    for (code, yt), row in per_term.items():
        by_course[code].append((yt, row))

    category = {}
    for k, v in REQUIRED.items():
        for c in v:
            category[c] = k
    for c in GENED_POOL:
        category[c] = "gened"
    for c in elective_codes:
        category[c] = "elective"

    records = []
    for code in sorted(wanted, key=lambda c: (category[c] != "core", c)):
        r = catalog[code]
        groups, ptext = parse_prereqs(r["Section Info"])
        # keep only prerequisite alternatives that are part of this degree plan
        kept = []
        for g in groups:
            alts = [a for a in g["any"] if a in wanted and a != code]
            if alts:
                kept.append(("~" if g["concurrent"] else "") + "|".join(alts))
        st = summarise([row for _, row in by_course.get(code, [])])
        offered_terms = terms[code]
        fa = any(t.endswith("fa") for t in offered_terms)
        sp = any(t.endswith("sp") for t in offered_terms)
        offered = "both" if fa and sp else ("odd" if fa else "even")
        avg = st["avg_gpa"]
        difficulty = (3 if avg is None else 1 if avg >= 3.75 else 2 if avg >= 3.5 else 3 if avg >= 3.25 else 4 if avg >= 3.0 else 5)
        n_terms = len({yt for yt, _ in by_course.get(code, [])}) or 1
        per_term_students = int(st["students"] / n_terms) if st["students"] else 60
        desc = clean(r["Description"])
        name = clean(r["Name"])
        records.append({
            "code": code, "name": name, "credits": parse_credits(r["Credit Hours"]), "category": category[code],
            "prereqs": ";".join(kept), "prereq_text": ptext, "offered": offered,
            "fall_2026": int("2026-fa" in offered_terms), "spring_2027": int("2027-sp" in offered_terms),
            "difficulty": difficulty,
            "avg_gpa": round(avg, 3) if avg is not None else "",
            "pass_rate": round(st["pass_rate"], 4) if st["pass_rate"] is not None else "",
            "withdraw_rate": round(st["withdraw_rate"], 4),
            "students_per_term": per_term_students,
            "tracks": ";".join(tracks_of(name, desc)) if category[code] in ("elective", "core", "project") else "",
            "attributes": clean(r["Degree Attributes"]).rstrip("."),
            "description": desc[:400],
        })

    # electives need real grade data and must be reachable
    records = [x for x in records if x["category"] != "elective" or x["pass_rate"] != ""]
    codes = {x["code"] for x in records}
    for x in records:
        x["prereqs"] = ";".join(g for g in x["prereqs"].split(";") if g and any(a in codes for a in g.lstrip("~").split("|")))

    fields = list(records[0])
    with open(os.path.join(OUT, "courses.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)

    # grade history: real per-term distributions for the courses in the plan
    hist_rows = []
    for code in sorted(codes):
        for yt, row in sorted(by_course.get(code, [])):
            s = summarise([row])
            hist_rows.append([code, yt, row["sections"], s["students"]] + [row[g] for g in list(LETTER_POINTS) + ["W"]]
                             + [round(s["avg_gpa"], 3) if s["avg_gpa"] else "", round(s["pass_rate"], 4) if s["pass_rate"] else ""])
    with open(os.path.join(OUT, "grade_history.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["course", "term", "sections", "students"] + list(LETTER_POINTS) + ["W", "avg_gpa", "pass_rate"])
        w.writerows(hist_rows)

    # career outcomes: real ACS earnings by major (recent grads + graduate-degree holders)
    recent = {r["Major"]: r for r in read("recent-grads.csv")}
    grad = {r["Major"]: r for r in read("grad-students.csv")}
    career_rows = []
    for t, info in TRACKS.items():
        for major in info["majors"]:
            r, g = recent[major], grad.get(major, {})
            career_rows.append([t, major.title(), r["Total"], r["Median"], r["P25th"], r["P75th"],
                                round(float(r["Unemployment_rate"]), 4),
                                round(int(r["College_jobs"]) / max(1, int(r["College_jobs"]) + int(r["Non_college_jobs"])), 3),
                                g.get("Grad_median", ""), g.get("Grad_premium", "")])
    with open(os.path.join(OUT, "career_outcomes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["track", "major", "graduates", "median_salary_usd", "p25_salary_usd", "p75_salary_usd",
                    "unemployment_rate", "college_job_share", "grad_degree_median_usd", "grad_premium"])
        w.writerows(career_rows)

    elective_pool = sorted(x["code"] for x in records if x["category"] == "elective")
    requirements = {
        "degree": "B.S. Computer Science (modelled on the University of Illinois Urbana-Champaign curriculum)",
        "semesters": 8, "first_term": "Fall",
        "required": [c for c in required if c in codes],
        "gened_choose": {"from": [c for c in GENED_POOL if c in codes], "count": GENED_COUNT},
        "electives_min": ELECTIVES_MIN, "elective_pool": elective_pool,
        "min_credits_per_sem": 12, "max_credits_per_sem": 18,
        "final_project": None, "final_project_min_sem": 0,
        "pass_grade": 0.67, "grade_scale": "4.0",
    }
    with open(os.path.join(OUT, "requirements.json"), "w", encoding="utf-8") as f:
        json.dump(requirements, f, indent=2)
    with open(os.path.join(OUT, "tracks.json"), "w", encoding="utf-8") as f:
        json.dump(TRACKS, f, indent=2)

    # demo students: fictional people, real course codes, letter grades on the 4.0 scale
    def tr(*sems):
        out = []
        for i, sem in enumerate(sems, 1):
            for code, letter in sem:
                out.append({"course": code, "semester": i, "grade": letter, "grade_point": LETTER_POINTS[letter]})
        return out
    s1 = [("CS 100", "A"), ("CS 124", "A-"), ("MATH 221", "B+"), ("RHET 105", "A"), ("ECON 102", "B")]
    s2 = [("CS 128", "B+"), ("CS 173", "B"), ("MATH 231", "B"), ("PHYS 211", "B-")]
    students = [
        {"id": "S1001", "name": "Ananya R", "completed_semesters": 0, "transcript": [],
         "goal": "I want to become a machine learning engineer and build AI products. Graduate on time."},
        {"id": "S1002", "name": "Rahul K", "completed_semesters": 1, "transcript": tr(s1),
         "goal": "Interested in data science and analytics, databases and data mining. Balanced workload."},
        {"id": "S1003", "name": "Meera S", "completed_semesters": 3,
         "transcript": tr([("CS 100", "A"), ("CS 124", "B"), ("MATH 221", "C+"), ("RHET 105", "A-"), ("PSYC 100", "B+")],
                          [("CS 128", "B-"), ("CS 173", "C"), ("MATH 231", "C"), ("PHYS 211", "C+")],
                          [("CS 225", "F"), ("MATH 257", "B-"), ("MATH 241", "C"), ("ECON 102", "B")]),
         "goal": "I want a career in cybersecurity and cryptography. Light workload please, I failed data structures."},
        {"id": "S1004", "name": "Arjun P", "completed_semesters": 5,
         "transcript": tr(s1, s2,
                          [("CS 225", "B"), ("CS 222", "A"), ("MATH 241", "B-"), ("MATH 257", "B+"), ("PHIL 105", "A-")],
                          [("CS 233", "B-"), ("CS 361", "B"), ("PHYS 212", "C+"), ("CS 211", "A")],
                          [("CS 341", "B"), ("CS 374", "C+"), ("CS 357", "B-"), ("SOC 100", "A")]),
         "goal": "I love building web apps and user interface design. I want to work at a startup."},
    ]
    with open(os.path.join(OUT, "students.json"), "w", encoding="utf-8") as f:
        json.dump(students, f, indent=2)

    sources = {
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "datasets": [
            {"name": "UIUC Course Catalog", "files": [f"raw/catalog-{t}.csv" for t in TERMS],
             "url": "https://github.com/wadefagen/datasets/tree/main/course-catalog",
             "origin": "University of Illinois Course Explorer API (courses.illinois.edu)",
             "used_for": "courses, credit hours, prerequisites, terms offered, Fall 2026 / Spring 2027 offerings",
             "rows": sum(1 for t in TERMS for _ in read(f"catalog-{t}.csv"))},
            {"name": "UIUC GPA Dataset", "files": ["raw/uiuc-gpa-dataset.csv"],
             "url": "https://github.com/wadefagen/datasets/tree/main/gpa",
             "origin": "Grade distributions released by the University of Illinois (FOIA / Senate EP.25.072)",
             "used_for": "pass rates, average GPA, withdrawal rates, typical class size (2021-2026)",
             "rows": len(read("uiuc-gpa-dataset.csv"))},
            {"name": "FiveThirtyEight College Majors", "files": ["raw/recent-grads.csv", "raw/grad-students.csv"],
             "url": "https://github.com/fivethirtyeight/data/tree/master/college-majors",
             "origin": "US Census American Community Survey 2010-2012 PUMS",
             "used_for": "career outcomes: median salary, unemployment, college-level job share by major",
             "rows": len(recent) + len(grad)},
        ],
        "built_files": {"courses.csv": len(records), "grade_history.csv": len(hist_rows),
                        "career_outcomes.csv": len(career_rows), "students.json": len(students)},
        "notes": "Degree rules are modelled on the UIUC CS B.S. curriculum (simplified). Demo students are fictional. "
                 "Live seat changes in the registrar feed are simulated around each course's real typical enrolment.",
    }
    with open(os.path.join(OUT, "sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2)

    n_el = len(elective_pool)
    print(f"courses.csv          {len(records)} courses ({n_el} technical electives)")
    print(f"grade_history.csv    {len(hist_rows)} course-term grade distributions")
    print(f"career_outcomes.csv  {len(career_rows)} majors")
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

"""
Loads the real datasets (built by build_data.py) once and exposes the facts the agents need:
  - course catalog: credits, prerequisite rules, terms offered, next-term offering  (UIUC Course Catalog)
  - pass rates, average GPA, withdrawal rates, class sizes, grade distributions       (UIUC GPA dataset)
  - career outcomes: salary / unemployment of related majors                         (FiveThirtyEight / US Census ACS)
"""
import csv
import json
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
GRADES = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F", "W"]


def _csv(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _groups(s):
    """'CS 128;~CS 225;CS 440|CS 441' -> [{'any': ['CS 128'], 'conc': False}, {'any': ['CS 225'], 'conc': True}, ...]"""
    out = []
    for g in (s or "").split(";"):
        if g:
            out.append({"any": g.lstrip("~").split("|"), "conc": g.startswith("~")})
    return out


class Catalog:
    def __init__(self):
        self.requirements = _json("requirements.json")
        self.tracks = _json("tracks.json")
        self.students = {s["id"]: s for s in _json("students.json")}
        self.sources = _json("sources.json")
        rows = _csv("courses.csv")
        known_pass = [_num(r["pass_rate"]) for r in rows if _num(r["pass_rate"]) is not None]
        known_gpa = [_num(r["avg_gpa"]) for r in rows if _num(r["avg_gpa"]) is not None]
        self.mean_pass = sum(known_pass) / len(known_pass)
        self.mean_gpa = sum(known_gpa) / len(known_gpa)

        self.courses = {}
        for r in rows:
            self.courses[r["code"]] = {
                "code": r["code"], "name": r["name"], "credits": int(r["credits"]), "category": r["category"],
                "groups": _groups(r["prereqs"]), "prereq_text": r["prereq_text"], "coreqs": [],
                "offered": r["offered"], "fall_2026": r["fall_2026"] == "1", "spring_2027": r["spring_2027"] == "1",
                "difficulty": int(r["difficulty"]),
                "avg_gpa": _num(r["avg_gpa"]), "pass_rate": _num(r["pass_rate"]),
                "withdraw_rate": _num(r["withdraw_rate"], 0.0),
                "capacity": max(25, int(r["students_per_term"])),
                "tracks": [t for t in r["tracks"].split(";") if t],
                "attributes": r["attributes"], "description": r["description"],
            }
        # courses without published grade data (small classes are withheld by the university)
        self.pass_rate = {c: (v["pass_rate"] if v["pass_rate"] is not None else self.mean_pass) for c, v in self.courses.items()}
        self.avg_grade = {c: (v["avg_gpa"] if v["avg_gpa"] is not None else self.mean_gpa) for c, v in self.courses.items()}

        # any course that can serve as a prerequisite of another
        self.dependents_any = defaultdict(set)
        for c in self.courses.values():
            for g in c["groups"]:
                for a in g["any"]:
                    self.dependents_any[a].add(c["code"])

        self._load_history()
        self._load_careers()

    # ---- Real grade distributions per course and term ---------------------------
    def _load_history(self):
        self.history = defaultdict(list)
        self.dist = defaultdict(lambda: [0] * len(GRADES))
        rows = _csv("grade_history.csv")
        self.history_rows = len(rows)
        for r in rows:
            c = r["course"]
            self.history[c].append({"term": r["term"], "students": int(r["students"]),
                                    "avg_gpa": _num(r["avg_gpa"]), "pass_rate": _num(r["pass_rate"])})
            for i, g in enumerate(GRADES):
                self.dist[c][i] += int(r[g] or 0)

    # ---- Real career outcomes by major -------------------------------------------
    def _load_careers(self):
        self.careers = defaultdict(list)
        for r in _csv("career_outcomes.csv"):
            self.careers[r["track"]].append({
                "major": r["major"], "graduates": int(r["graduates"]), "median": int(r["median_salary_usd"]),
                "p25": int(r["p25_salary_usd"]), "p75": int(r["p75_salary_usd"]),
                "unemployment": float(r["unemployment_rate"]), "college_jobs": float(r["college_job_share"]),
                "grad_median": int(float(r["grad_degree_median_usd"])) if r["grad_degree_median_usd"] else None})

    def outlook(self, track):
        """Graduate-weighted career outlook of the majors related to a track."""
        rows = self.careers.get(track, [])
        n = sum(r["graduates"] for r in rows) or 1
        return {"median": round(sum(r["median"] * r["graduates"] for r in rows) / n),
                "unemployment": round(sum(r["unemployment"] * r["graduates"] for r in rows) / n, 4),
                "college_jobs": round(sum(r["college_jobs"] * r["graduates"] for r in rows) / n, 3),
                "majors": [r["major"] for r in rows]}

    # ---- Helpers -------------------------------------------------------------------
    def offered_in(self, code, sem):
        o = self.courses[code]["offered"]
        return o == "both" or (o == "odd" and sem % 2 == 1) or (o == "even" and sem % 2 == 0)

    def offered_next_term(self, code, sem):
        """Real schedule for the coming term: Spring 2027 for even semesters, Fall 2026 catalog for odd ones."""
        c = self.courses[code]
        return c["spring_2027"] if sem % 2 == 0 else c["fall_2026"]

    def popularity(self, code):
        return math.log10(max(10, self.courses[code]["capacity"]))

    def public(self):
        """Catalog data sent to the website."""
        return {
            "courses": self.courses,
            "requirements": self.requirements,
            "tracks": {k: {"label": v["label"], "roles": v["roles"]} for k, v in self.tracks.items()},
            "students": [{"id": s["id"], "name": s["name"], "completed_semesters": s["completed_semesters"],
                          "goal": s["goal"]} for s in self.students.values()],
        }

    def stats(self):
        graded = [c for c in self.courses if self.courses[c]["avg_gpa"] is not None]
        hardest = sorted(graded, key=lambda c: self.courses[c]["avg_gpa"])[:12]
        electives = self.requirements["elective_pool"]
        return {
            "counts": {"courses": len(self.courses), "electives": len(electives),
                       "grade_records": self.sources["datasets"][1]["rows"],
                       "catalog_rows": self.sources["datasets"][0]["rows"],
                       "course_terms": self.history_rows,
                       "majors": sum(len(v) for v in self.careers.values()),
                       "students": len(self.students)},
            "sources": self.sources,
            "hardest": [{"code": c, "name": self.courses[c]["name"], "avg_gpa": round(self.courses[c]["avg_gpa"], 2),
                         "pass_rate": round(self.pass_rate[c], 3)} for c in hardest],
            "careers": {t: {"label": self.tracks[t]["label"], "rows": rows, **self.outlook(t)}
                        for t, rows in self.careers.items()},
            "track_electives": {t: [e for e in electives if t in self.courses[e]["tracks"]] for t in self.tracks},
            "grades": GRADES,
            "dist": {c: v for c, v in self.dist.items()},
            "history": self.history,
        }

# CoursePath Planner: Multi-Agent Degree Planning Website

A team of four cooperating agents reads a student's goal, checks every rule, builds a
semester-by-semester roadmap and keeps it current in real time, all the way to graduation.
All course facts come from **real public datasets** downloaded from the internet.

**No machine learning.** The agents use rules, search and statistics taken directly from the data.
**Nothing to install.** It uses only built-in Python 3 (no pip, no NumPy).

## How to run (Windows)
1. Double-click **`run.bat`**. Your browser opens at http://localhost:8000.
2. To stop the server, press `Ctrl+C` in the black window.

On the very first run, if `data/courses.csv` is missing, the server downloads the datasets (about 57 MB)
and builds them automatically. You can also do this by hand:
```
py -3 download_data.py      # step 1: download the real datasets into data/raw/
py -3 build_data.py         # step 2: process them into data/*.csv for the agents
py -3 server.py             # start the website
```

## Online version (Vercel)

Vercel can only serve files and short functions, so it can't run `server.py` itself: that needs memory between requests, the live-registrar loop and the event stream. On the hosted site, the **same Python code runs inside the visitor's browser** with [Pyodide](https://pyodide.org):

- `web/common.js` first checks whether `server.py` is answering. If it is (`run.bat`), everything works exactly as before.
- If not, `web/browser-python.js` loads Pyodide, copies `server.py`, `agents.py`, `catalog.py`, `browser_api.py` and `data/` into it, and runs the registrar every 2.5 s like `registrar_loop()` does.
- `browser_api.py` imports `server.py` unchanged and answers each `/api/...` request with `server.Handler`, without a network.

The first visit takes a few seconds while Python downloads; after that it is cached. Each visitor gets their own registrar and plans, and each page starts fresh (the Planner page has its own "fill this course" button for the adaptation demo).

## The datasets (real, public)
| Dataset | Where it comes from | What the agents use it for |
|---|---|---|
| **UIUC Course Catalog** (5 terms: Spring 2025 to Spring 2027, about 64,600 rows) | University of Illinois Course Explorer API, published at [github.com/wadefagen/datasets](https://github.com/wadefagen/datasets/tree/main/course-catalog) | Real courses, credit hours, **prerequisite rules** (parsed from the official text, including "one of A, B or C" and "credit or concurrent registration"), Fall/Spring offerings, and the **actual Spring 2027 schedule** |
| **UIUC GPA Dataset** (about 110,000 rows) | Grade distributions released by the University of Illinois (FOIA / Senate EP.25.072), [github.com/wadefagen/datasets](https://github.com/wadefagen/datasets/tree/main/gpa) | Real **pass rates**, average GPA, withdrawal rates and typical class size (used as seat capacity) for every course, Fall 2021 to 2026 |
| **FiveThirtyEight College Majors** | US Census American Community Survey 2010–2012, [github.com/fivethirtyeight/data](https://github.com/fivethirtyeight/data/tree/master/college-majors) | **Career outcomes**: median salary, unemployment and college-level job share of the majors related to each career track |

Files produced by `build_data.py` in `data/`:
`courses.csv` (69 courses: 13 core, 37 technical electives, math, physics, gen-ed), `grade_history.csv`
(541 real course-term grade distributions), `career_outcomes.csv`, `requirements.json`, `tracks.json`,
`students.json`, `sources.json`. The raw downloads stay in `data/raw/`.

What is **not** real data: the degree rules are a simplified version of the UIUC CS B.S. curriculum, the
4 demo students are fictional (with real course codes), and the live seat changes in the registrar feed are
simulated around each course's real enrolment.

## Pages
| Page | What it shows |
|---|---|
| **Home** | Project overview, the agent pipeline and the data sources |
| **Planner** | Pick a student, type a goal, press **Run the agents**: the agents talk to each other live and build the roadmap |
| **Live Registrar** | Live seat counts (capacity = real enrolment) and the real Spring 2027 offering. Fill or cancel courses to trigger re-planning |
| **Datasets** | Each source, a grade-distribution viewer for any course, GPA trend by term, hardest courses, career outcomes, downloads |

## The four agents (`agents.py`)
| Agent | Job | How |
|---|---|---|
| **Goal Agent** | Turns free text ("ML engineer, light workload, graduate in 7 semesters") into an objective | Keyword matching against career tracks, regex for semesters/years, finds liked or avoided electives, looks up the track's real career outcome data |
| **Constraint Agent** | Knows every rule; filters valid options; validates the final plan (guardrails) | OR-prerequisites, concurrent enrolment, Fall/Spring offerings, the real next-term schedule, live seats, credit-hour limits, degree rules |
| **Planning Agent** | Builds the roadmap | Resolves "one of" prerequisites, then a constraint-satisfaction search (**depth-first search with backtracking**) with earliest/latest-semester analysis. Long prerequisite chains go first, and difficulty is balanced with **real pass rates** scaled by the student's GPA. Electives are chosen by career track (tagged from the real course descriptions) and real enrolment |
| **Adaptation Agent** | Keeps the roadmap valid | Reacts to: course FULL or CANCELLED (live registrar), course dropped, semester results (graded with real historical grade odds; fails become retakes), goal changed. Re-plans automatically and shows what moved |

A human-in-the-loop **advisor review** (Approve / Request changes) is applied to every new version of the plan.

## Demo script
1. Planner → choose **Meera S** (she failed CS 225) → **Run the agents**. CS 225 is scheduled as a retake.
2. Hover any course to see its **real prerequisite text** from the UIUC catalog.
3. Click **⚡ Demo: fill a seat**. The roadmap re-plans by itself.
4. Hover a course → **✕ drop**. The agents re-plan the following semesters.
5. **⏭ Simulate end of semester**. Results come in using the real pass rates, and the plan adapts.
6. Edit the goal (e.g. "machine learning and computer vision") → **✎ Apply changed goal**. The electives change.
7. **✓ Approve roadmap** (advisor review). Then open **Datasets** and pick a course to show its real grade distribution.

## Files
```
download_data.py   downloads the real datasets (step 1)
build_data.py      parses prerequisites, computes pass rates, builds data/*.csv (step 2)
catalog.py         loads the built data for the agents
agents.py          Goal, Constraint, Planning, Adaptation agents + Orchestrator
server.py          web server + API + live registrar feed (standard library only)
web/               website: index, planner, registrar, data pages + style.css, common.js, planner.js
run.bat            double-click to start
```

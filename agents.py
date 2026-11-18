"""
CoursePath Planner - the four cooperating agents.

  Student input --> GoalAgent --> ConstraintAgent --> PlanningAgent --> AdaptationAgent
                    sets the       filters valid      builds the        keeps it current
                    objective      options            roadmap

All facts come from real datasets (see catalog.py / build_data.py):
  UIUC course catalog (prerequisites, terms), UIUC grade distributions (pass rates, GPA),
  US Census ACS earnings by major (career outcomes).

Every agent writes what it did into a shared trace (the "blackboard"), so the website can
show the hand-offs between agents live.
"""
import copy
import math
import random
import re
import time

TERM = {1: "Fall", 0: "Spring"}
LETTERS = [("A", 4.0), ("A-", 3.67), ("B+", 3.33), ("B", 3.0), ("B-", 2.67), ("C+", 2.33), ("C", 2.0),
           ("C-", 1.67), ("D+", 1.33), ("D", 1.0), ("D-", 0.67)]


class Trace:
    """Shared message log the agents write into."""

    def __init__(self):
        self.items = []
        self.t0 = time.perf_counter()

    def log(self, agent, text, kind="info"):
        self.items.append({"agent": agent, "text": text, "kind": kind,
                           "ms": round((time.perf_counter() - self.t0) * 1000, 1)})


def student_record(cat, student):
    """Summarise a transcript: passed courses (latest grade), failed-but-not-yet-passed, GPA (4.0 scale)."""
    passed, failed = {}, set()
    for t in sorted(student["transcript"], key=lambda x: x["semester"]):
        c = t["course"]
        if t["grade_point"] >= cat.requirements["pass_grade"]:
            passed[c] = t["grade_point"]
            failed.discard(c)
        elif c not in passed:
            failed.add(c)
    credits = sum(cat.courses[c]["credits"] for c in passed if c in cat.courses)
    attempted = [t for t in student["transcript"] if t["course"] in cat.courses]
    acr = sum(cat.courses[t["course"]]["credits"] for t in attempted)
    gpa = sum(cat.courses[t["course"]]["credits"] * t["grade_point"] for t in attempted) / acr if acr else None
    return {"passed": passed, "failed": failed, "credits": credits,
            "cgpa": round(gpa, 2) if gpa is not None else None,
            "next_sem": student["completed_semesters"] + 1}


# =============================================================================
# 1. GOAL AGENT - turns free-text goals + form options into a structured objective
# =============================================================================
class GoalAgent:
    name = "Goal Agent"
    LIGHT = ["light", "easy", "less stress", "relaxed", "struggl", "failed", "part-time", "part time", "slow"]
    HEAVY = ["heavy", "accelerat", "fast track", "fast-track", "graduate early", "early", "maximum", "challenge"]
    NEG = ["avoid", "no ", "not ", "don't", "dont", "without", "skip", "hate"]

    def __init__(self, cat):
        self.cat = cat

    def run(self, student, goal_text, options, trace):
        cat, text = self.cat, " " + (goal_text or "").lower() + " "
        trace.log(self.name, f"Reading goal of {student['name']}: \"{(goal_text or '').strip()}\"")

        # --- career track: keyword evidence -------------------------------------------
        scores, hits = {}, {}
        for t, info in cat.tracks.items():
            found = [k for k in info["keywords"] if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text)]
            scores[t] = sum(2 if " " in k else 1 for k in found)
            hits[t] = found
        if options.get("track"):
            scores[options["track"]] = scores.get(options["track"], 0) + 10
        total = sum(scores.values())
        if total == 0:
            t = "software_dev"
            scores[t], total = 1, 1
            trace.log(self.name, "No career keywords found; defaulting to Software Development", "warn")
        ranked = sorted(((t, s / total) for t, s in scores.items() if s > 0), key=lambda x: -x[1])[:2]
        for t, w in ranked:
            trace.log(self.name, f"Career track {cat.tracks[t]['label']}: weight {w:.0%}"
                                 + (f" (matched: {', '.join(hits[t][:5])})" if hits.get(t) else ""))
        out = cat.outlook(ranked[0][0])
        trace.log(self.name, f"Career outcome data (US Census ACS) for related majors "
                             f"({', '.join(out['majors'])}): median salary ${out['median']:,}, "
                             f"unemployment {out['unemployment']:.1%}")

        # --- workload ---------------------------------------------------------------------
        workload = options.get("workload") or (
            "light" if any(w in text for w in self.LIGHT) else
            "heavy" if any(w in text for w in self.HEAVY) else "balanced")
        max_credits = int(options.get("max_credits") or {"light": 15, "balanced": 17, "heavy": 18}[workload])
        max_credits = min(max_credits, cat.requirements["max_credits_per_sem"])
        risk_budget = {"light": 0.12, "balanced": 0.2, "heavy": 0.3}[workload]
        trace.log(self.name, f"Workload '{workload}': at most {max_credits} credit hours and failure-risk "
                             f"budget {risk_budget} per semester")

        # --- graduation target -----------------------------------------------------------
        target = options.get("target_semesters")
        if not target:
            m = re.search(r"(\d+)\s*(?:semesters|sems)", text)
            m2 = re.search(r"(\d+(?:\.\d)?)\s*years", text)
            if m:
                target = int(m.group(1))
            elif m2:
                target = int(round(float(m2.group(1)) * 2))
            elif "graduate early" in text or "accelerat" in text:
                target = 7
            else:
                target = cat.requirements["semesters"]
        target = max(int(target), student["completed_semesters"] + 1)
        trace.log(self.name, f"Graduation target: end of semester {target}")

        # --- elective likes / dislikes mentioned in text or form ---------------------------
        prefer, avoid = set(options.get("prefer") or []), set(options.get("avoid") or [])
        for code in cat.requirements["elective_pool"]:
            name = cat.courses[code]["name"].lower()
            for needle in (name, code.lower()):
                i = text.find(needle)
                if i >= 0:
                    before = text[max(0, i - 14):i]
                    (avoid if any(n in before for n in self.NEG) else prefer).add(code)
        prefer -= avoid
        if prefer:
            trace.log(self.name, "Student asked for: " + ", ".join(sorted(prefer)))
        if avoid:
            trace.log(self.name, "Student wants to avoid: " + ", ".join(sorted(avoid)))

        objective = {"tracks": ranked, "primary": ranked[0][0], "workload": workload,
                     "max_credits": max_credits, "risk_budget": risk_budget, "target": target,
                     "prefer": sorted(prefer), "avoid": sorted(avoid), "outlook": out}
        trace.log(self.name, "Objective ready -> handing over to Constraint Agent", "handoff")
        return objective


# =============================================================================
# 2. CONSTRAINT AGENT - knows every rule; filters valid options; validates plans
# =============================================================================
class ConstraintAgent:
    name = "Constraint Agent"

    def __init__(self, cat, registrar):
        self.cat = cat
        self.registrar = registrar          # live seat data

    def seat_ok(self, code):
        return self.registrar.status(code) == "open"

    def can_take(self, code, sem, done, ctx, same_sem=()):
        """Hard constraints for putting `code` in semester `sem`.
        done = courses finished before `sem`; same_sem = courses that could be taken alongside it."""
        cat = self.cat
        if not cat.offered_in(code, sem):
            return False, f"not offered in {TERM[sem % 2]} terms (catalog history)"
        for p, conc in ctx["req"].get(code, []):
            if p in done or (conc and p in same_sem):
                continue
            return False, "missing prerequisite " + p
        if (code, sem) in ctx["banned"]:
            return False, "blocked for this semester"
        if sem == ctx["next_sem"]:
            if not cat.offered_next_term(code, sem):
                return False, f"not on the real {'Spring 2027' if sem % 2 == 0 else 'Fall'} schedule"
            if not self.seat_ok(code):
                return False, f"no seats ({self.registrar.status(code)}) in registrar"
        return True, ""

    def analyse(self, student, record, objective, trace):
        """Work out what is still needed and what the student is allowed to take."""
        cat, req = self.cat, self.cat.requirements
        passed = record["passed"]
        trace.log(self.name, f"Transcript: {len(passed)} courses passed, {record['credits']} credit hours, "
                             f"GPA {record['cgpa'] if record['cgpa'] is not None else 'n/a'}; next semester is {record['next_sem']}")
        needed = [c for c in req["required"] if c not in passed]
        if record["failed"]:
            trace.log(self.name, "Failed courses that must be retaken: " + ", ".join(sorted(record["failed"])), "warn")

        gened_done = [c for c in req["gened_choose"]["from"] if c in passed]
        electives_done = [c for c in req["elective_pool"] if c in passed]
        gened_left = max(0, req["gened_choose"]["count"] - len(gened_done))
        electives_left = max(0, req["electives_min"] - len(electives_done))
        allowed = [e for e in req["elective_pool"] if e not in passed and e not in objective["avoid"]]
        if len(allowed) < electives_left + 3:
            # the student avoided almost everything: the degree still needs its electives
            back = [e for e in req["elective_pool"] if e not in passed and e not in allowed]
            allowed += back
            trace.log(self.name, f"Too few electives left after the avoid list; allowing {len(back)} avoided "
                                 "course(s) again so the degree can be completed", "warn")
        trace.log(self.name, f"Still required: {len(needed)} courses; gen-ed picks left: {gened_left}; "
                             f"technical electives left: {electives_left} (from {len(allowed)} real UIUC 400-level courses)")
        nxt = record["next_sem"]
        not_next = [c for c in needed if not cat.offered_next_term(c, nxt)]
        if not_next:
            trace.log(self.name, f"Real {'Spring 2027' if nxt % 2 == 0 else 'Fall'} schedule: "
                                 f"{', '.join(not_next[:6])} not offered next term", "warn")
        full = [c for c in cat.courses if self.registrar.status(c) != "open"]
        if full:
            trace.log(self.name, "Live registrar: no seats in " + ", ".join(sorted(full)), "warn")
        trace.log(self.name, "Constraint set ready (OR-prerequisites, concurrent enrolment, term offered, "
                             "seats, credit limits) -> handing over to Planning Agent", "handoff")
        return {"needed": needed, "gened_left": gened_left,
                "gened_pool": [g for g in req["gened_choose"]["from"] if g not in passed],
                "electives_left": electives_left, "elective_pool": allowed}

    # ---- Guardrails: check a finished plan against every rule -----------------------
    def validate(self, plan, record, objective):
        cat, req = self.cat, self.cat.requirements
        checks = []
        when = {c: s["sem"] for s in plan["semesters"] for c in s["codes"]}
        done = set(record["passed"])

        bad_pre, bad_term = [], []
        for s in plan["semesters"]:
            for c in s["codes"]:
                for g in cat.courses[c]["groups"]:
                    ok = any(a in done or (a in when and (when[a] < s["sem"] or (g["conc"] and when[a] == s["sem"])))
                             for a in g["any"])
                    if not ok:
                        bad_pre.append(f"{c} needs {' or '.join(g['any'])}")
                if not cat.offered_in(c, s["sem"]):
                    bad_term.append(f"{c} in sem {s['sem']}")
        checks.append({"rule": "Prerequisites (incl. 'one of' rules) met before each course", "ok": not bad_pre, "detail": "; ".join(bad_pre)})
        checks.append({"rule": "Every course offered in its term (Fall/Spring history)", "ok": not bad_term, "detail": "; ".join(bad_term)})

        over = [f"sem {s['sem']}: {s['credits']}" for s in plan["semesters"] if s["credits"] > objective["max_credits"]]
        last = plan["semesters"][-1]["sem"] if plan["semesters"] else 0
        under = [f"sem {s['sem']}: {s['credits']}" for s in plan["semesters"]
                 if s["credits"] < req["min_credits_per_sem"] and s["sem"] != last]
        checks.append({"rule": f"Credit hours per semester <= {objective['max_credits']}", "ok": not over, "detail": "; ".join(over)})
        checks.append({"rule": f"Full-time load >= {req['min_credits_per_sem']} hours (except final)",
                       "ok": not under, "detail": "; ".join(under), "soft": True})

        allc = done | set(when)
        miss = [c for c in req["required"] if c not in allc]
        checks.append({"rule": "All required courses covered", "ok": not miss, "detail": ", ".join(miss)})
        g = sum(1 for c in req["gened_choose"]["from"] if c in allc)
        checks.append({"rule": f"{req['gened_choose']['count']} general-education courses", "ok": g >= req["gened_choose"]["count"], "detail": f"{g} planned"})
        e = sum(1 for c in req["elective_pool"] if c in allc)
        checks.append({"rule": f"At least {req['electives_min']} technical electives", "ok": e >= req["electives_min"], "detail": f"{e} planned"})
        nxt = [s for s in plan["semesters"] if s["sem"] == record["next_sem"]]
        nosched = [c for s in nxt for c in s["codes"] if not cat.offered_next_term(c, s["sem"])]
        checks.append({"rule": "Next-term courses are on the real schedule", "ok": not nosched, "detail": ", ".join(nosched)})
        nofull = [c for s in nxt for c in s["codes"] if not self.seat_ok(c)]
        checks.append({"rule": "Seats available for next semester (live)", "ok": not nofull, "detail": ", ".join(nofull)})
        checks.append({"rule": f"Graduates by semester {objective['target']}", "ok": plan["graduation_sem"] <= objective["target"],
                       "detail": f"graduates in semester {plan['graduation_sem']}", "soft": True})
        return checks


# =============================================================================
# 3. PLANNING AGENT - constraint-aware search (DFS with backtracking) for a roadmap
# =============================================================================
class PlanningAgent:
    name = "Planning Agent"
    NODE_LIMIT = 6000

    def __init__(self, cat, constraints):
        self.cat = cat
        self.ca = constraints

    # ---- scoring helpers -----------------------------------------------------------
    def risk(self, code, record):
        """Chance of failing or withdrawing, from the real UIUC pass rate, scaled by this student's GPA."""
        base = 1 - self.cat.pass_rate[code]
        g = record["cgpa"] if record["cgpa"] is not None else 3.3
        f = min(3.0, max(0.4, 1 + (3.3 - g) * 1.2))
        if code in record["failed"]:
            f *= 1.4
        return min(0.95, base * f)

    def elective_score(self, code, objective):
        cat, c = self.cat, self.cat.courses[code]
        s = 0.0
        for t, w in objective["tracks"]:
            s += w * (4.0 if t in c["tracks"] else 0.0)
        if code in objective["prefer"]:
            s += 6
        s += 0.3 * cat.popularity(code)                      # real enrolment: what students actually choose
        if objective["workload"] == "light":
            s += 1.5 * (cat.avg_grade[code] - cat.mean_gpa)  # historically easier courses
        return s

    def explain(self, code, record, objective, height, why_extra):
        cat, c = self.cat, self.cat.courses[code]
        stats = (f"UIUC avg GPA {cat.avg_grade[code]:.2f}, {cat.pass_rate[code]:.0%} pass"
                 if c["avg_gpa"] is not None else "no published grade data; degree average used")
        if code in record["failed"]:
            return f"Retake: failed earlier ({stats})"
        if code in why_extra:
            return f"Prerequisite for {why_extra[code]} ({stats})"
        if c["category"] == "elective":
            t = objective["primary"]
            tag = "you asked for it" if code in objective["prefer"] else (
                f"fits {cat.tracks[t]['label']}" if t in c["tracks"] else
                "fits " + ", ".join(cat.tracks[x]["label"] for x in c["tracks"]) if c["tracks"] else "technical elective")
            return f"Elective: {tag} ({stats})"
        if c["category"] == "gened":
            return f"Gen-ed: {c['attributes'] or 'general education'} ({stats})"
        if height > 1:
            return f"Required: starts a chain of {height - 1} later course(s) ({stats})"
        return f"Required for the degree ({stats})"

    # ---- prerequisite resolution: pick one option for every "one of" rule -----------
    def resolve(self, needed, record):
        cat, required = self.cat, set(self.cat.requirements["required"])
        needed, req, why = set(needed), {}, {}
        changed = True
        while changed:
            changed = False
            for c in sorted(needed):
                if c in req:
                    continue
                lst = []
                for g in cat.courses[c]["groups"]:
                    if any(a in record["passed"] for a in g["any"]):
                        continue
                    inside = [a for a in g["any"] if a in needed]
                    if inside:
                        choice = sorted(inside, key=lambda a: (a not in required, a))[0]
                    else:
                        choice = sorted(g["any"], key=lambda a: (a not in required, cat.courses[a]["difficulty"], a))[0]
                        needed.add(choice)
                        why[choice] = c
                        changed = True
                    lst.append((choice, g["conc"]))
                req[c] = lst
        return needed, req, why

    # ---- main entry ------------------------------------------------------------------
    def run(self, student, record, objective, needs, ctx, trace):
        cat = self.cat
        t0 = time.perf_counter()

        gened = sorted(needs["gened_pool"], key=lambda g: (
            -cat.avg_grade[g] if objective["workload"] == "light" else 0, -cat.popularity(g), g))[:needs["gened_left"]]
        ranked = sorted(needs["elective_pool"], key=lambda e: -self.elective_score(e, objective))
        locked_codes = {c for codes in ctx["locked"].values() for c in codes}
        keep = [e for e in ranked if e in locked_codes]
        base_needed = set(needs["needed"]) | set(gened) | set(keep) | locked_codes

        result = None
        last_locked = max(ctx["locked"], default=0)
        first_target = max(objective["target"], last_locked)
        for target in range(first_target, max(13, last_locked + 5)):
            electives = list(keep)
            for e in ranked:
                if len(electives) >= needs["electives_left"]:
                    break
                if e in electives:
                    continue
                cand, req, _ = self.resolve(base_needed | set(electives) | {e}, record)
                ctx2 = {**ctx, "req": req}
                if self._earliest(cand, record, ctx2)[e] <= target:
                    electives.append(e)
            if len(electives) < needs["electives_left"]:
                trace.log(self.name, f"Only {len(electives)} of {needs['electives_left']} electives can fit by "
                                     f"semester {target}; extending by one semester", "warn")
                continue
            needed, req, why = self.resolve(base_needed | set(electives), record)
            ctx["req"] = req
            if target == objective["target"]:
                trace.log(self.name, "Technical electives chosen for " + cat.tracks[objective["primary"]]["label"] + ": "
                          + ", ".join(f"{e} {cat.courses[e]['name']}" for e in electives if e not in record["passed"]))
                if gened:
                    trace.log(self.name, "Gen-ed chosen: " + ", ".join(gened))
                if why:
                    trace.log(self.name, "Resolved 'one of' prerequisites by adding: "
                              + ", ".join(f"{k} (for {v})" for k, v in why.items()))
            result = self._search(needed, record, objective, ctx, target, trace)
            if result:
                break
            if target >= objective["target"]:
                trace.log(self.name, f"No valid roadmap finishing by semester {target}; extending by one semester", "warn")
        if not result:
            trace.log(self.name, "Search failed - no valid roadmap found", "warn")
            return None
        plan_sems, nodes = result

        heights = self._heights(needed, req)
        semesters = []
        for sem, codes in plan_sems:
            courses = []
            for c in sorted(codes, key=lambda x: (cat.courses[x]["category"] not in ("core", "project"), x)):
                co = cat.courses[c]
                courses.append({"code": c, "name": co["name"], "credits": co["credits"], "category": co["category"],
                                "difficulty": co["difficulty"], "pass_rate": round(cat.pass_rate[c], 3),
                                "avg_gpa": round(cat.avg_grade[c], 2), "risk": round(self.risk(c, record), 3),
                                "prereq_text": co["prereq_text"],
                                "reason": self.explain(c, record, objective, heights.get(c, 1), why),
                                "locked": sem in ctx["locked"]})
            cr = sum(x["credits"] for x in courses)
            risk = sum(x["risk"] for x in courses)
            semesters.append({"sem": sem, "term": TERM[sem % 2], "codes": list(codes), "courses": courses,
                              "credits": cr, "risk": round(risk, 3),
                              "exp_gpa": round(sum(x["avg_gpa"] * x["credits"] for x in courses) / cr, 2) if cr else None,
                              "load": "heavy" if risk > objective["risk_budget"] or cr > 17 else ("light" if cr <= 13 else "balanced")})
        grad = semesters[-1]["sem"] if semesters else record["next_sem"] - 1
        ms = round((time.perf_counter() - t0) * 1000, 1)
        trace.log(self.name, f"Search explored {nodes} states in {ms} ms -> roadmap of {len(semesters)} semesters, "
                             f"graduating in semester {grad}", "ok")
        trace.log(self.name, "Roadmap handed to Constraint Agent for validation and to Adaptation Agent for monitoring", "handoff")
        return {"semesters": semesters, "graduation_sem": grad, "nodes": nodes,
                "delayed": grad > objective["target"],
                "electives": sorted(e for e in needed if cat.courses[e]["category"] == "elective")}

    # ---- graph helpers (on the resolved prerequisite graph) ----------------------------
    def _deps(self, needed, req):
        deps = {c: [] for c in needed}
        for c in needed:
            for p, conc in req.get(c, []):
                if p in deps:
                    deps[p].append((c, conc))
        return deps

    def _heights(self, needed, req):
        deps, memo = self._deps(needed, req), {}

        def h(c):
            if c not in memo:
                memo[c] = 1 + max((h(d) for d, conc in deps[c] if not conc), default=0)
            return memo[c]
        return {c: h(c) for c in needed}

    def _slot_ok(self, c, s, ctx):
        return self.cat.offered_in(c, s) and (c, s) not in ctx["banned"] and not (
            s == ctx["next_sem"] and not self.cat.offered_next_term(c, s))

    def _earliest(self, needed, record, ctx):
        memo, start = {}, record["next_sem"]
        lock_sem = {c: s for s, cs in ctx["locked"].items() for c in cs}

        def e(c):
            if c in memo:
                return memo[c]
            if c in lock_sem:
                memo[c] = lock_sem[c]
                return memo[c]
            memo[c] = 99                       # guards against cycles
            s = start
            for p, conc in ctx["req"].get(c, []):
                if p in record["passed"]:
                    continue
                s = max(s, e(p) + (0 if conc else 1)) if p in needed else 99
            while s < 20 and not self._slot_ok(c, s, ctx):
                s += 1
            memo[c] = s
            return s
        return {c: e(c) for c in needed}

    def _latest(self, needed, target, ctx):
        deps, memo = self._deps(needed, ctx["req"]), {}

        def l(c):
            if c in memo:
                return memo[c]
            memo[c] = target
            bound = min([l(d) - (0 if conc else 1) for d, conc in deps[c]], default=target)
            s = bound
            while s > 0 and not self._slot_ok(c, s, ctx):
                s -= 1
            memo[c] = s
            return s
        return {c: l(c) for c in needed}

    # ---- the search ---------------------------------------------------------------
    def _search(self, needed, record, objective, ctx, target, trace):
        cat, req_rules = self.cat, self.cat.requirements
        start = record["next_sem"]
        locked = ctx["locked"]
        locked_all = {x for v in locked.values() for x in v}
        remaining = frozenset(c for c in needed if c not in locked_all)
        if not remaining and not locked:
            return [], 0
        earliest = self._earliest(needed, record, ctx)
        latest = self._latest(needed, target, ctx)
        impossible = [c for c in remaining if earliest[c] > latest[c]]
        if impossible:
            trace.log(self.name, f"Target sem {target}: {', '.join(sorted(impossible)[:4])} cannot fit "
                                 f"(earliest > latest possible semester)", "warn")
            return None
        heights = self._heights(needed, ctx["req"])
        risk = {c: self.risk(c, record) for c in needed}
        maxc, minc = objective["max_credits"], req_rules["min_credits_per_sem"]
        nodes = [0]
        last_locked = max(locked) if locked else 0
        level = lambda c: 2 * int(c.split()[1][0]) if c.split()[1][0].isdigit() else 8

        def together(c, done):
            """Courses that must be taken in the same term as c (unfinished concurrent prerequisites)."""
            return [p for p, conc in ctx["req"].get(c, []) if conc and p not in done]

        def dfs(sem, done, remaining, acc):
            nodes[0] += 1
            if nodes[0] > self.NODE_LIMIT:
                return None
            if not remaining and sem > last_locked:
                return acc
            if sem > target:
                return None
            if sem in locked:
                sel = frozenset(locked[sem])
                return dfs(sem + 1, done | sel, remaining - sel, acc + [(sem, sorted(sel))])
            rem_credits = sum(cat.courses[c]["credits"] for c in remaining)
            sems_left = target - sem + 1
            if rem_credits > sems_left * maxc:
                return None
            # available = all hard constraints pass (concurrent prereqs may come along in the same term)
            pool = set(remaining)
            avail = set()
            for _ in range(3):
                for c in remaining:
                    ok, _ = self.ca.can_take(c, sem, done, ctx, same_sem=avail | (pool & avail))
                    if ok:
                        avail.add(c)
            must = [c for c in remaining if latest[c] <= sem]
            if any(c not in avail for c in must):
                return None
            order = sorted(avail, key=lambda c: (min(latest[c], level(c)) - 0.75 * (heights[c] - 1),
                                                 -heights[c], risk[c], c))
            # build units: a course plus ALL its unfinished concurrent prerequisites (taken together).
            # A course may appear in several units; only courses not yet selected are added.
            def closure(c):
                out, stack = [], [c]
                while stack:
                    x = stack.pop()
                    if x not in out:
                        out.append(x)
                        stack.extend(together(x, done))
                return out
            units = []
            for c in order:
                u = closure(c)
                if all(p in avail for p in u):
                    units.append(tuple(u))
            must_units = [u for u in units if u[0] in must]
            if len(must_units) < len(must):
                return None                       # a zero-slack course cannot bring its partner along
            other = [u for u in units if u[0] not in must]
            base = list(dict.fromkeys(c for u in must_units for c in u))
            base_cr = sum(cat.courses[c]["credits"] for c in base)
            if base_cr > maxc:
                return None
            goal = min(maxc, max(minc, math.ceil(rem_credits / sems_left)))
            tried = set()
            for tgt, use_budget in ((goal, True), (min(maxc, goal + 3), True), (maxc, False), (max(minc, goal - 3), True)):
                sel, cr = list(base), base_cr
                rk = sum(risk[c] for c in base)
                for u0 in other:
                    u = [c for c in u0 if c not in sel]
                    if not u:
                        continue
                    ucr = sum(cat.courses[c]["credits"] for c in u)
                    urk = sum(risk[c] for c in u)
                    urgent = min(latest[c] for c in u) <= sem + 1
                    if cr + ucr > maxc:
                        continue
                    # below full-time (12 h) always add; otherwise add if it overshoots less than it undershoots
                    if not urgent and cr >= minc and cr + ucr > tgt and (cr + ucr - tgt) >= (tgt - cr):
                        continue
                    if use_budget and not urgent and rk + urk > objective["risk_budget"]:
                        continue
                    sel += u
                    cr += ucr
                    rk += urk
                key = frozenset(sel)
                if key in tried:
                    continue
                tried.add(key)
                r = dfs(sem + 1, done | key, remaining - key, acc + [(sem, sorted(sel))])
                if r is not None:
                    return r
            return None

        result = dfs(start, frozenset(record["passed"]), remaining, [])
        if result is None:
            return None
        return [x for x in result if x[1]], nodes[0]


# =============================================================================
# 4. ADAPTATION AGENT - watches events and re-plans without a human restarting it
# =============================================================================
class AdaptationAgent:
    name = "Adaptation Agent"

    def __init__(self, cat, orchestrator):
        self.cat = cat
        self.orch = orchestrator

    def affected(self, state, course):
        """Is `course` in this student's next semester?"""
        plan = state.get("plan")
        if not plan or not plan["semesters"]:
            return False
        nxt = plan["semesters"][0]
        return nxt["sem"] == state["record"]["next_sem"] and course in nxt["codes"]

    def handle(self, state, event):
        cat = self.cat
        trace = Trace()
        kind, code = event["type"], event.get("course")
        old = copy.deepcopy(state["plan"])
        nxt = state["record"]["next_sem"]
        cname = f"{code} {cat.courses[code]['name']}" if code in cat.courses else code

        if kind in ("seat_full", "cancelled"):
            trace.log(self.name, f"Registrar event: {cname} is {'FULL' if kind == 'seat_full' else 'CANCELLED'} "
                                 f"for semester {nxt}", "warn")
            state["banned"].add((code, nxt))
            state["locked"] = {}
        elif kind == "drop":
            sem = int(event["semester"])
            trace.log(self.name, f"Student dropped {cname} from semester {sem}", "warn")
            # earlier semesters and the rest of this semester stay; later ones are re-planned.
            # Courses in this semester that needed the dropped one alongside it must move too.
            locked = {}
            for s in state["plan"]["semesters"]:
                if s["sem"] < sem:
                    locked[s["sem"]] = list(s["codes"])
                elif s["sem"] == sem:
                    locked[s["sem"]] = [c for c in s["codes"] if c != code and not any(
                        code in g["any"] and g["conc"] for g in cat.courses[c]["groups"])]
            state["locked"] = {s: v for s, v in locked.items() if v}
            state["banned"].add((code, sem))
        elif kind == "goal_change":
            trace.log(self.name, "Student changed their goal -> asking Goal Agent to re-interpret", "warn")
            state["goal"] = event["goal"]
            state["locked"] = {}
        elif kind == "complete_semester":
            return self.complete_semester(state, trace)
        else:
            trace.log(self.name, f"Unknown event {kind}", "warn")
            return None

        trace.log(self.name, "Re-planning the rest of the roadmap automatically", "handoff")
        self.orch.plan(state, trace, rerun_goal=(kind == "goal_change"))
        return self.finish(state, old, trace, event)

    def complete_semester(self, state, trace):
        """Simulate the next semester's results: each course passes with its real historical probability."""
        cat = self.cat
        old = copy.deepcopy(state["plan"])
        if not state["plan"]["semesters"]:
            trace.log(self.name, "Nothing left to take - student has graduated", "ok")
            return {"event": {"type": "complete_semester"}, "diff": [], "trace": trace.items}
        sem_plan = state["plan"]["semesters"][0]
        sem = sem_plan["sem"]
        trace.log(self.name, f"Semester {sem} finished - collecting results from registrar")
        rng = random.Random()
        failed = []
        g = state["record"]["cgpa"] if state["record"]["cgpa"] is not None else 3.3
        for c in sem_plan["courses"]:
            passed = rng.random() > c["risk"]
            if passed:
                x = max(0.67, min(4.0, rng.gauss(cat.avg_grade[c["code"]] + (g - 3.3) * 0.6, 0.45)))
                letter, gp = min(LETTERS, key=lambda lv: abs(lv[1] - x))
            else:
                letter, gp = "F", 0.0
                failed.append(c["code"])
            state["student"]["transcript"].append({"course": c["code"], "semester": sem, "grade": letter, "grade_point": gp})
        state["student"]["completed_semesters"] = sem
        state["locked"], state["banned"] = {}, set()
        if failed:
            trace.log(self.name, f"Failed: {', '.join(failed)} -> must be retaken; re-planning", "warn")
        else:
            trace.log(self.name, f"All {len(sem_plan['courses'])} courses passed", "ok")
        self.orch.plan(state, trace, rerun_goal=False)
        return self.finish(state, old, trace, {"type": "complete_semester", "failed": failed, "semester": sem})

    def finish(self, state, old, trace, event):
        new = state["plan"]
        diff = []
        if new:
            o = {c: s["sem"] for s in old["semesters"] for c in s["codes"]}
            n = {c: s["sem"] for s in new["semesters"] for c in s["codes"]}
            for c in sorted(set(o) | set(n)):
                if c in o and c in n and o[c] != n[c]:
                    diff.append({"course": c, "change": "moved", "from": o[c], "to": n[c]})
                elif c in n and c not in o:
                    diff.append({"course": c, "change": "added", "to": n[c]})
                elif c in o and c not in n and c not in state["record"]["passed"]:
                    diff.append({"course": c, "change": "removed", "from": o[c]})
            g0, g1 = old["graduation_sem"], new["graduation_sem"]
            trace.log(self.name, f"{len(diff)} change(s); graduation semester {g0} -> {g1}",
                      "ok" if g1 <= g0 else "warn")
        state["trace"] = state["trace"] + trace.items
        state["review"] = {"status": "pending", "note": "Plan changed - needs advisor review"}
        state["version"] += 1
        return {"event": event, "diff": diff, "trace": trace.items}


# =============================================================================
# ORCHESTRATOR - runs the agents in sequence and keeps each student's state
# =============================================================================
class Orchestrator:
    def __init__(self, cat, registrar):
        self.cat = cat
        self.goal = GoalAgent(cat)
        self.constraint = ConstraintAgent(cat, registrar)
        self.planning = PlanningAgent(cat, self.constraint)
        self.adaptation = AdaptationAgent(cat, self)

    def new_state(self, student, goal, options):
        return {"student": copy.deepcopy(student), "goal": goal, "options": options or {},
                "locked": {}, "banned": set(), "plan": None, "objective": None,
                "trace": [], "version": 0, "review": {"status": "pending", "note": ""}}

    def plan(self, state, trace=None, rerun_goal=True):
        trace = trace or Trace()
        student = state["student"]
        record = student_record(self.cat, student)
        state["record"] = record
        state["banned"] = {(c, s) for c, s in state["banned"] if s >= record["next_sem"]}
        state["locked"] = {s: v for s, v in state["locked"].items() if s >= record["next_sem"]}
        if rerun_goal or not state["objective"]:
            state["objective"] = self.goal.run(student, state["goal"], state["options"], trace)
        else:
            trace.log("Goal Agent", "Objective unchanged -> reusing it", "handoff")
        needs = self.constraint.analyse(student, record, state["objective"], trace)
        ctx = {"banned": state["banned"], "locked": state["locked"], "next_sem": record["next_sem"], "req": {}}
        plan = self.planning.run(student, record, state["objective"], needs, ctx, trace)
        if plan is None and state.get("plan") and state["plan"]["semesters"]:
            # never leave the student with nothing: keep the last valid roadmap and flag it
            trace.log("Adaptation Agent", "Could not find a new roadmap for this change; keeping the previous "
                                          "one - please ask an advisor", "warn")
            plan = copy.deepcopy(state["plan"])
            state["locked"], state["banned"] = {}, set()
        if plan is None:
            plan = {"semesters": [], "graduation_sem": 99, "nodes": 0, "delayed": True, "electives": []}
        checks = self.constraint.validate(plan, record, state["objective"])
        bad = [c for c in checks if not c["ok"] and not c.get("soft")]
        trace.log("Constraint Agent", f"Guardrail validation: {len(checks) - len(bad)}/{len(checks)} rules satisfied"
                  + ("" if not bad else " - FAILED: " + "; ".join(c["rule"] for c in bad)), "ok" if not bad else "warn")
        trace.log("Adaptation Agent", "Watching registrar feed for changes to this roadmap", "info")
        plan["checks"] = checks
        state["plan"] = plan
        return trace.items

    def view(self, sid, state):
        """JSON-safe view of a plan state for the website."""
        rec, obj = state["record"], state["objective"]
        return {
            "plan_id": sid, "version": state["version"],
            "student": {"id": state["student"]["id"], "name": state["student"]["name"],
                        "completed_semesters": state["student"]["completed_semesters"],
                        "cgpa": rec["cgpa"], "credits": rec["credits"],
                        "passed": rec["passed"], "failed": sorted(rec["failed"]),
                        "transcript": state["student"]["transcript"]},
            "goal": state["goal"],
            "objective": {**obj, "tracks": [[t, round(w, 2), self.cat.tracks[t]["label"]] for t, w in obj["tracks"]]},
            "plan": state["plan"], "review": state["review"],
            "banned": sorted([list(b) for b in state["banned"]]),
        }

#!/usr/bin/env python3
"""Product-mechanics ledger: validate it, render it, send questions, take answers.

The ledger (docs/product-mechanics/ledger.yml) records HOW Cortex turns signal
into a detection — what we know, what CortexSim assumes, and the questions we
still owe. Questions travel to the work Mac as a self-contained packet; answers
come back as an immutable answer file. Read docs/product-mechanics/README.md.

Every command previews by default and writes only with ``--apply``::

    python3 scripts/product_mechanics.py check
    python3 scripts/product_mechanics.py render [--apply]
    python3 scripts/product_mechanics.py packet --priority P0 [--slug p0-seed] [--apply [--vault]]
    python3 scripts/product_mechanics.py ingest <answer-file.md> [--apply]

The confidentiality boundary is enforced here, not in prose: an answer whose
source is not ``distribution: public`` raises ``ConfidentialityError`` and the
whole file is refused. This repo is public, and the vault forbids internal-only
PANW material on both Macs (vault SOP, Hosts table). Internal findings stay on
the work Mac; only their public or lab-observed confirmation crosses.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "docs" / "product-mechanics"
LEDGER = BASE / "ledger.yml"
RENDERED = BASE / "LEDGER.md"
ANSWERS_DIR = BASE / "answers"
PACKETS_DIR = BASE / "packets"

#: Vault-relative homes. The personal Mac writes the outbox; the work Mac writes
#: answers into `_RoB/`, its working area (vault SOP, Hosts table, rule 4).
VAULT_OUTBOX = Path("github-projects/cortex-pov-engine/product-mechanics/questions")
VAULT_INBOX = Path("_RoB/product-mechanics/answers")

PRIORITIES = ("P0", "P1", "P2")
AUTHORED_STATUSES = ("open", "retired")
ANSWER_STATUSES = ("answered", "partial", "unknown", "conflicting", "internal_only")
#: Source kinds that may cross into this public repo. `repo` is allowed for a
#: ledger `known` fact only; an answer from the work Mac never cites the repo.
CROSSABLE_KINDS = ("public_doc", "public_kb", "public_blog", "lab_observation", "operator_report")
KNOWN_KINDS = CROSSABLE_KINDS + ("repo",)
EXCERPT_MAX_WORDS = 25

#: Top-level directories whose paths a ref may name. Any token starting with one
#: of these must exist on disk, so a renamed file cannot rot silently.
_REPO_DIRS = ("agent", "core", "detection_scanner", "docs", "infra", "scenarios",
              "scripts", "sources", "tests", "tools", "ui")

_MECH_KEYS = {"id", "name", "summary", "known", "assumes", "contrasts"}
_Q_KEYS_REQUIRED = {"id", "mechanism", "priority", "status", "question", "context", "lever", "affects"}
_Q_KEYS_OPTIONAL = {"decides", "experiment"}
_ANSWER_KEYS = {"id", "status", "answer", "rule", "sources", "follow_ups"}
_SOURCE_KEYS_REQUIRED = {"kind", "title", "ref", "date", "distribution"}
_SOURCE_KEYS_OPTIONAL = {"excerpt"}
_QID = re.compile(r"^PM-([A-Z]{3})-(\d{2})$")
_FENCE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.S)


class LedgerError(ValueError):
    """The ledger, a packet or an answer file has a shape we do not recognise.

    Raised, never tolerated: an unrecognised shape read as empty is how a wrong
    claim about a customer's coverage greens a test.
    """


class ConfidentialityError(LedgerError):
    """An answer carries a source that may not leave the work Mac."""


# ── loading and validation ──────────────────────────────────────────────────

def _req(cond: bool, msg: str) -> None:
    if not cond:
        raise LedgerError(msg)


def _text(value: Any, where: str) -> str:
    _req(isinstance(value, str) and value.strip() != "", f"{where}: must be a non-empty string")
    return value


def _date(value: Any, where: str) -> str:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()[:10]
    _req(isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value or "") is not None,
         f"{where}: date must be YYYY-MM-DD")
    return value


def _check_keys(obj: dict, required: set, optional: set, where: str) -> None:
    _req(isinstance(obj, dict), f"{where}: must be a mapping")
    missing = required - obj.keys()
    unknown = obj.keys() - required - optional
    _req(not missing, f"{where}: missing {sorted(missing)}")
    _req(not unknown, f"{where}: unknown keys {sorted(unknown)}")


def check_refs(text: str, where: str, root: Path = ROOT) -> None:
    """Every repo-path token in ``text`` must exist (globs allowed)."""
    for token in re.split(r"[\s;,\[\]]+", text):
        token = token.strip("`'\"()")
        if "/" not in token or token.startswith("http"):
            continue
        if token.split("/", 1)[0] not in _REPO_DIRS:
            continue
        path = re.split(r"[:#]", token, 1)[0].rstrip(".")
        if any(ch in path for ch in "*?{"):
            hit = glob.glob(str(root / path))
        else:
            hit = [path] if (root / path).exists() else []
        _req(bool(hit), f"{where}: ref names a path that does not exist: {path}")


def validate_ledger(data: Any, root: Path = ROOT) -> dict:
    _req(isinstance(data, dict), "ledger: top level must be a mapping")
    _check_keys(data, {"schema_version", "updated", "mechanisms", "questions"}, set(), "ledger")
    _req(data["schema_version"] == 1, "ledger: schema_version must be 1")
    _date(data["updated"], "ledger.updated")

    mech_ids: list[str] = []
    for i, m in enumerate(data["mechanisms"] or []):
        where = f"mechanisms[{i}]"
        _check_keys(m, _MECH_KEYS, set(), where)
        _req(re.match(r"^[A-Z]{3}$", str(m["id"])) is not None, f"{where}.id: three capitals")
        _req(m["id"] not in mech_ids, f"{where}.id: duplicate {m['id']}")
        mech_ids.append(m["id"])
        _text(m["name"], f"{where}.name")
        _text(m["summary"], f"{where}.summary")
        for j, k in enumerate(m["known"] or []):
            w = f"{where}.known[{j}]"
            _check_keys(k, {"fact", "source"}, set(), w)
            _text(k["fact"], f"{w}.fact")
            _check_keys(k["source"], {"kind", "ref", "date"}, set(), f"{w}.source")
            _req(k["source"]["kind"] in KNOWN_KINDS,
                 f"{w}.source.kind: {k['source']['kind']!r} is not one of {KNOWN_KINDS}")
            _text(k["source"]["ref"], f"{w}.source.ref")
            _date(k["source"]["date"], f"{w}.source.date")
        for j, a in enumerate(m["assumes"] or []):
            w = f"{where}.assumes[{j}]"
            _check_keys(a, {"assumption", "ref"}, set(), w)
            _text(a["assumption"], f"{w}.assumption")
            check_refs(_text(a["ref"], f"{w}.ref"), f"{w}.ref", root)
    for i, m in enumerate(data["mechanisms"]):
        for j, c in enumerate(m["contrasts"] or []):
            w = f"mechanisms[{i}].contrasts[{j}]"
            _check_keys(c, {"vs", "point"}, set(), w)
            _req(c["vs"] in mech_ids, f"{w}.vs: unknown mechanism {c['vs']!r}")
            _text(c["point"], f"{w}.point")

    q_ids: set[str] = set()
    for i, q in enumerate(data["questions"] or []):
        where = f"questions[{i}]"
        _check_keys(q, _Q_KEYS_REQUIRED, _Q_KEYS_OPTIONAL, where)
        match = _QID.match(str(q["id"]))
        _req(match is not None, f"{where}.id: {q['id']!r} is not PM-XXX-NN")
        _req(q["id"] not in q_ids, f"{where}.id: duplicate {q['id']}")
        q_ids.add(q["id"])
        _req(q["mechanism"] in mech_ids, f"{q['id']}.mechanism: unknown {q['mechanism']!r}")
        _req(match.group(1) == q["mechanism"], f"{q['id']}: id prefix disagrees with mechanism {q['mechanism']}")
        _req(q["priority"] in PRIORITIES, f"{q['id']}.priority: one of {PRIORITIES}")
        _req(q["status"] in AUTHORED_STATUSES, f"{q['id']}.status: one of {AUTHORED_STATUSES}")
        for key in ("question", "context", "lever"):
            _text(q[key], f"{q['id']}.{key}")
        _req(isinstance(q["affects"], list) and q["affects"], f"{q['id']}.affects: non-empty list")
        check_refs(" ".join(map(str, q["affects"])), f"{q['id']}.affects", root)
        for j, d in enumerate(q.get("decides") or []):
            _check_keys(d, {"if", "then"}, set(), f"{q['id']}.decides[{j}]")
            _text(d["if"], f"{q['id']}.decides[{j}].if")
            _text(d["then"], f"{q['id']}.decides[{j}].then")
        if "experiment" in q:
            _text(q["experiment"], f"{q['id']}.experiment")
    return data


def load_ledger(path: Path = LEDGER, root: Path = ROOT) -> dict:
    with open(path, encoding="utf-8") as fh:
        return validate_ledger(yaml.safe_load(fh), root)


def _split_frontmatter(text: str, where: str) -> tuple[dict, str]:
    _req(text.startswith("---\n"), f"{where}: must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    _req(end != -1, f"{where}: frontmatter is not closed")
    front = yaml.safe_load(text[4:end]) or {}
    _req(isinstance(front, dict), f"{where}: frontmatter must be a mapping")
    return front, text[end + 5:]


def validate_answer_record(rec: Any, known_ids: set[str], where: str) -> dict:
    _check_keys(rec, {"id", "status"}, _ANSWER_KEYS - {"id", "status"}, where)
    _req(rec["id"] in known_ids, f"{where}: {rec['id']!r} is not a ledger question")
    status = rec["status"]
    _req(status in ANSWER_STATUSES, f"{where}.status: one of {ANSWER_STATUSES}")
    sources = rec.get("sources") or []
    _req(isinstance(sources, list), f"{where}.sources: must be a list")

    if status == "internal_only":
        # The work Mac keeps the content. Nothing but the verdict may cross.
        for key in ("answer", "rule", "sources"):
            _req(not rec.get(key), f"{where}: internal_only must not carry `{key}` — "
                                   "internal findings stay on the work Mac")
        return rec

    if status != "unknown":
        _text(rec.get("answer"), f"{where}.answer")
        _req(sources, f"{where}.sources: a {status} answer needs at least one source")
    for j, s in enumerate(sources):
        w = f"{where}.sources[{j}]"
        _check_keys(s, _SOURCE_KEYS_REQUIRED, _SOURCE_KEYS_OPTIONAL, w)
        if s["distribution"] != "public":
            raise ConfidentialityError(
                f"{w}: distribution is {s['distribution']!r}. Only `public` crosses into this "
                "repo or the vault; record the finding on the work Mac and mark the answer "
                "internal_only, or confirm it in the lab and cite the lab_observation.")
        _req(s["kind"] in CROSSABLE_KINDS,
             f"{w}.kind: {s['kind']!r} may not cross; allowed {CROSSABLE_KINDS}")
        _text(s["title"], f"{w}.title")
        _text(s["ref"], f"{w}.ref")
        _date(s["date"], f"{w}.date")
        if s.get("excerpt"):
            words = len(str(s["excerpt"]).split())
            _req(words <= EXCERPT_MAX_WORDS,
                 f"{w}.excerpt: {words} words; quote at most {EXCERPT_MAX_WORDS}")
    if rec.get("rule") is not None:
        _text(rec["rule"], f"{where}.rule")
    for j, f in enumerate(rec.get("follow_ups") or []):
        _text(f, f"{where}.follow_ups[{j}]")
    return rec


def parse_answer_file(text: str, known_ids: set[str], where: str = "answer file") -> dict:
    """Return ``{"front": {...}, "answers": [...]}`` or raise."""
    front, body = _split_frontmatter(text, where)
    _check_keys(front, {"packet", "answered"}, {"answered_by", "source"}, f"{where} frontmatter")
    _text(front["packet"], f"{where}.packet")
    _date(front["answered"], f"{where}.answered")
    blocks = _FENCE.findall(body)
    _req(len(blocks) == 1, f"{where}: needs exactly one ```yaml block, found {len(blocks)}")
    payload = yaml.safe_load(blocks[0])
    _check_keys(payload, {"answers"}, set(), f"{where} yaml block")
    _req(isinstance(payload["answers"], list) and payload["answers"],
         f"{where}: `answers` must be a non-empty list")
    seen: set[str] = set()
    for i, rec in enumerate(payload["answers"]):
        validate_answer_record(rec, known_ids, f"{where}.answers[{i}]")
        _req(rec["id"] not in seen, f"{where}: {rec['id']} answered twice in one file")
        seen.add(rec["id"])
    return {"front": front, "answers": payload["answers"]}


def load_answers(known_ids: set[str], answers_dir: Path = ANSWERS_DIR) -> dict[str, list[dict]]:
    """Map question id → answer records, oldest file first."""
    by_id: dict[str, list[dict]] = {}
    for path in sorted(answers_dir.glob("*.md")):
        parsed = parse_answer_file(path.read_text(encoding="utf-8"), known_ids, path.name)
        for rec in parsed["answers"]:
            by_id.setdefault(rec["id"], []).append(
                {**rec, "_file": path.name, "_answered": _date(parsed["front"]["answered"], path.name)})
    return by_id


def load_asked(known_ids: set[str], packets_dir: Path = PACKETS_DIR) -> dict[str, list[str]]:
    """Map question id → packet ids it went out in."""
    asked: dict[str, list[str]] = {}
    for path in sorted(packets_dir.glob("*.md")):
        front, _ = _split_frontmatter(path.read_text(encoding="utf-8"), path.name)
        _req(isinstance(front.get("questions"), list), f"{path.name}: frontmatter needs `questions`")
        for qid in front["questions"]:
            _req(qid in known_ids, f"{path.name}: {qid!r} is not a ledger question")
            asked.setdefault(qid, []).append(str(front.get("packet", path.stem)))
    return asked


# ── status ──────────────────────────────────────────────────────────────────

def promote(records: list[dict]) -> str:
    """Decide what a question's answers have EARNED.

    ``records`` are validated answer records for one question, oldest first.
    Each carries ``status`` (answered | partial | unknown | conflicting |
    internal_only) and ``sources`` whose ``kind`` is one of CROSSABLE_KINDS.

    Returns one of: ``internal_only`` · ``unknown`` · ``provisional`` ·
    ``settled`` · ``conflicting``.

    This is a doctrine call, the same seam as authored-vs-proven: what does it
    take before the generator may treat an answer as a rule?
    """
    crossing = [r for r in records if r["status"] != "internal_only"]
    if not crossing:
        return "internal_only"
    # TODO(henry): the promotion policy. Decide what earns `settled` (a public
    # doc alone? a lab_observation alone? both agreeing?), what makes it
    # `conflicting` (two answers whose `rule` differs? any `conflicting` status?),
    # and whether `unknown`/`partial` answers count at all. Until then nothing is
    # ever settled — the conservative default, because a settled rule is what the
    # generator gets changed to.
    return "provisional"


def effective_status(question: dict, answers: list[dict], asked: list[str]) -> str:
    if question["status"] == "retired":
        return "retired"
    if answers:
        return promote(answers)
    return "asked" if asked else "open"


# ── rendering ───────────────────────────────────────────────────────────────

_STATUS_ORDER = ("open", "asked", "internal_only", "unknown", "provisional",
                 "conflicting", "settled", "retired")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def render_ledger(data: dict, answers: dict[str, list[dict]], asked: dict[str, list[str]]) -> str:
    qs = data["questions"]
    status = {q["id"]: effective_status(q, answers.get(q["id"], []), asked.get(q["id"], [])) for q in qs}
    out: list[str] = []
    w = out.append
    w("<!-- GENERATED by scripts/product_mechanics.py render — edit ledger.yml, not this file. -->")
    w("")
    w("# Product-mechanics ledger")
    w("")
    w(f"How Cortex turns signal into a detection: what we know, what CortexSim assumes, and "
      f"what we still have to ask. Source: [`ledger.yml`](ledger.yml) (updated {data['updated']}). "
      f"Loop and rules: [`README.md`](README.md).")
    w("")
    w("> **Known is not assumed, and answered is not settled.** A `known` fact cites a public or "
      "lab source; an `assumes` row is what the engine does today and may be wrong. A question "
      "is `settled` only when its answers earn it (see `promote()`).")
    w("")
    w("## Where things stand")
    w("")
    head = "| mechanism | " + " | ".join(PRIORITIES) + " | " + " | ".join(_STATUS_ORDER) + " |"
    w(head)
    w("|" + "---|" * (1 + len(PRIORITIES) + len(_STATUS_ORDER)))
    totals_p = {p: 0 for p in PRIORITIES}
    totals_s = {s: 0 for s in _STATUS_ORDER}
    for m in data["mechanisms"]:
        mq = [q for q in qs if q["mechanism"] == m["id"]]
        cp = {p: sum(1 for q in mq if q["priority"] == p) for p in PRIORITIES}
        cs = {s: sum(1 for q in mq if status[q["id"]] == s) for s in _STATUS_ORDER}
        for p in PRIORITIES:
            totals_p[p] += cp[p]
        for s in _STATUS_ORDER:
            totals_s[s] += cs[s]
        w(f"| [{m['id']}](#{m['id'].lower()}) {m['name']} | "
          + " | ".join(str(cp[p]) for p in PRIORITIES) + " | "
          + " | ".join(str(cs[s]) for s in _STATUS_ORDER) + " |")
    w(f"| **all ({len(qs)})** | " + " | ".join(f"**{totals_p[p]}**" for p in PRIORITIES) + " | "
      + " | ".join(f"**{totals_s[s]}**" for s in _STATUS_ORDER) + " |")
    w("")
    w("Priority: **P0** a wrong guess produces a silent miss in a POV today · **P1** it weakens "
      "fidelity, attribution or MTTD · **P2** refinement.")
    w("")
    by_id = {m["id"]: m for m in data["mechanisms"]}
    for m in data["mechanisms"]:
        w(f"## {m['id']}")
        w("")
        w(f"### {m['name']}")
        w("")
        w(_one_line(m["summary"]))
        w("")
        w("**Known**")
        w("")
        if m["known"]:
            for k in m["known"]:
                s = k["source"]
                w(f"- {_one_line(k['fact'])} — *{s['kind']}*, {_one_line(s['ref'])} ({_date(s['date'], 'known')})")
        else:
            w("- Nothing with a crossable source yet.")
        w("")
        w("**What CortexSim assumes today**")
        w("")
        for a in m["assumes"] or []:
            w(f"- {_one_line(a['assumption'])} — `{_one_line(a['ref'])}`")
        if not m["assumes"]:
            w("- —")
        w("")
        if m["contrasts"]:
            w("**How this differs from**")
            w("")
            for c in m["contrasts"]:
                w(f"- **{c['vs']} ({by_id[c['vs']]['name']})** — {_one_line(c['point'])}")
            w("")
        mq = [q for q in qs if q["mechanism"] == m["id"]]
        w("**Questions**")
        w("")
        w("| id | priority | status | question |")
        w("|---|---|---|---|")
        for q in mq:
            w(f"| [{q['id']}](#{q['id'].lower()}) | {q['priority']} | {status[q['id']]} | "
              f"{_one_line(q['question']).replace('|', '/')} |")
        w("")
        for q in mq:
            w(f"#### {q['id']}")
            w("")
            w(f"**{q['priority']} · {status[q['id']]}** — {_one_line(q['question'])}")
            w("")
            w(f"- **Why we ask:** {_one_line(q['context'])}")
            w(f"- **What the answer moves:** {_one_line(q['lever'])}")
            for d in q.get("decides") or []:
                w(f"  - *if* {_one_line(d['if'])} → {_one_line(d['then'])}")
            if q.get("experiment"):
                w(f"- **Settle it in the lab:** {_one_line(q['experiment'])}")
            w(f"- **Touches:** " + ", ".join(f"`{a}`" for a in q["affects"]))
            if asked.get(q["id"]):
                w(f"- **Asked in:** " + ", ".join(asked[q["id"]]))
            for rec in answers.get(q["id"], []):
                kinds = ", ".join(sorted({s["kind"] for s in rec.get("sources") or []})) or "no sources"
                line = f"- **Answer ({rec['_answered']}, {rec['status']}, {kinds}):** "
                line += _one_line(rec.get("answer") or "content held on the work Mac")
                if rec.get("rule"):
                    line += f" **Rule:** {_one_line(rec['rule'])}"
                w(line)
            w("")
    return "\n".join(out).rstrip() + "\n"


# ── packets ─────────────────────────────────────────────────────────────────

ANSWER_INSTRUCTIONS = """\
## Instructions for the answer agent

You are answering product-mechanics questions about Cortex XSIAM / XDR for the CortexSim
detection-simulation engine. Each question names the generator behaviour its answer changes,
so answer in terms the generator can act on: field names, counts, windows, which side of a
join, which record type, which setting.

**Search** enterprise search (Glean), Confluence, Jira, Drive and the public docs
(https://cortex-docs.paloaltonetworks.com). Prefer engineering design docs and resolved Jira
tickets over sales material; prefer the newest source when two disagree, and say they disagree.

**The confidentiality rule decides what you write where. It is not optional.**

- The answer file you return (below) is stored in a personal Obsidian vault and a **public**
  GitHub repository. It may contain **only** facts whose source is public (public docs,
  public KB, public blog) or that were observed in our own lab, or an operator report.
- Anything found only in an internal source (Confluence, Jira, Drive, Slack, internal Glean
  results) stays on the work Mac, in work storage, outside the vault. In the answer file,
  mark that question `status: internal_only` with no `answer`, `rule` or `sources`.
- If an internal finding can be confirmed in the lab, say so on the work side; the lab result
  is what crosses later, as a `lab_observation`.
- Quote at most 25 words from any source (`excerpt`). Paraphrase the rest.

**Honesty.** `unknown` is a good answer when the sources do not settle it. Never infer a
threshold and present it as documented. If an answer depends on version, licence or
configuration, say which.

## Reply format

Write one Markdown file to `{reply_to}` (vault-relative) shaped exactly like this — frontmatter,
then exactly one ```yaml block. The ingest step rejects any other shape, and rejects the whole
file if any source is not `distribution: public`.

````text
---
packet: {packet}
answered: YYYY-MM-DD
answered_by: <agent or person>
source: work-mac
---
```yaml
answers:
  - id: PM-XXX-NN
    status: answered        # answered | partial | unknown | conflicting | internal_only
    answer: >-
      Plain explanation of how the product behaves and why.
    rule: >-
      The generator-facing constraint, e.g. "emit >= N distinct dst ports from one src
      within 1 h". Omit when there is no rule.
    sources:
      - kind: public_doc    # public_doc | public_kb | public_blog | lab_observation | operator_report
        title: Page or ticket title
        ref: https://... (or a description for lab/operator)
        date: YYYY-MM-DD
        distribution: public
        excerpt: optional, at most 25 words
    follow_ups:
      - Optional new question the answer raised.
```
````
"""


def select_questions(data: dict, answers: dict, asked: dict, *, priorities: Iterable[str] = (),
                     mechanisms: Iterable[str] = (), ids: Iterable[str] = (),
                     include_asked: bool = False) -> list[dict]:
    priorities, mechanisms, ids = set(priorities), set(mechanisms), set(ids)
    known = {q["id"] for q in data["questions"]}
    _req(ids <= known, f"--ids: unknown {sorted(ids - known)}")
    picked = []
    for q in data["questions"]:
        st = effective_status(q, answers.get(q["id"], []), asked.get(q["id"], []))
        if ids:
            if q["id"] in ids:
                picked.append(q)
            continue
        if st not in ("open",) + (("asked",) if include_asked else ()):
            continue
        if priorities and q["priority"] not in priorities:
            continue
        if mechanisms and q["mechanism"] not in mechanisms:
            continue
        picked.append(q)
    _req(bool(picked), "packet: no questions match the selection")
    return picked


def render_packet(data: dict, questions: list[dict], packet_id: str, created: str) -> str:
    reply_to = (VAULT_INBOX / f"{packet_id.replace('PMQ-', 'PMA-', 1)}.md").as_posix()
    by_id = {m["id"]: m for m in data["mechanisms"]}
    out: list[str] = []
    w = out.append
    w("---")
    w(yaml.safe_dump({
        "packet": packet_id,
        "created": created,
        "from": "personal-mac / cortex-pov-engine",
        "source": "personal-mac",
        "reply_to": reply_to,
        "questions": [q["id"] for q in questions],
        "tags": ["product-mechanics", "question-packet"],
    }, sort_keys=False).rstrip())
    w("---")
    w("")
    w(f"# Product-mechanics questions — {packet_id}")
    w("")
    w(f"{len(questions)} questions about how Cortex turns telemetry into a detection. CortexSim "
      "generates signal into customer tenants for POVs; when a signal does not fire, the POV "
      "report reads it as \"Cortex missed it\". These answers decide what the generator emits.")
    w("")
    w(ANSWER_INSTRUCTIONS.format(reply_to=reply_to, packet=packet_id))
    w("## Questions")
    w("")
    for q in questions:
        m = by_id[q["mechanism"]]
        w(f"### {q['id']} — {q['priority']} — {m['name']}")
        w("")
        w(f"**Question.** {_one_line(q['question'])}")
        w("")
        w(f"**Why we ask.** {_one_line(q['context'])}")
        w("")
        w(f"**What the answer changes.** {_one_line(q['lever'])}")
        for d in q.get("decides") or []:
            w(f"- *if* {_one_line(d['if'])} → {_one_line(d['then'])}")
        if q.get("experiment"):
            w("")
            w(f"**Could be settled in the lab.** {_one_line(q['experiment'])}")
        w("")
        w(f"**Background — our current working model of {m['id']}.** {_one_line(m['summary'])}")
        w("")
    return "\n".join(out).rstrip() + "\n"


# ── vault ───────────────────────────────────────────────────────────────────

def vault_root(env: Optional[dict] = None) -> Path:
    """Resolve the vault from $OBSIDIAN_ROOT/$OBSIDIAN_VAULT_NAME — never guess."""
    env = os.environ if env is None else env
    missing = [k for k in ("OBSIDIAN_ROOT", "OBSIDIAN_VAULT_NAME") if not env.get(k)]
    _req(not missing, f"vault: set {' and '.join(missing)} (per-host, in ~/.zshenv); not guessing")
    root = Path(env["OBSIDIAN_ROOT"]) / env["OBSIDIAN_VAULT_NAME"]
    _req(root.is_dir(), f"vault: {root} does not exist")
    # An existing folder is not proof of a vault: a typo'd name or a sibling vault
    # would take the write and the work Mac would never see it.
    _req((root / ".obsidian").is_dir(), f"vault: {root} is not an Obsidian vault (no .obsidian/)")
    return root.resolve()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_into_vault(vault: Path, rel: Path, text: str) -> tuple[Path, str]:
    """Write ``text`` at ``vault/rel`` and prove it landed: inside the vault, byte-identical.

    Returns (path, sha256). Raises LedgerError rather than report a write it did not see.
    """
    _req(not rel.is_absolute() and ".." not in rel.parts, f"vault: {rel} must be vault-relative")
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    _req(path.parent.resolve().is_relative_to(vault),
         f"vault: {path.parent} resolves outside {vault} (symlink?)")
    data = text.encode("utf-8")
    path.write_bytes(data)
    got = path.read_bytes()
    _req(_sha256(got) == _sha256(data), f"vault: read-back of {path} does not match what was written")
    return path, _sha256(got)


def vault_packet_status(vault: Path, packets_dir: Path = PACKETS_DIR) -> list[tuple[str, str]]:
    """Per repo packet: ``present`` (vault copy byte-identical), ``missing`` or ``differs``."""
    out = []
    for p in sorted(packets_dir.glob("PMQ-*.md")):
        v = vault / VAULT_OUTBOX / p.name
        if not v.is_file():
            out.append((p.name, "missing"))
        elif _sha256(v.read_bytes()) != _sha256(p.read_bytes()):
            out.append((p.name, "differs"))
        else:
            out.append((p.name, "present"))
    return out


# ── commands ────────────────────────────────────────────────────────────────

def _state() -> tuple[dict, dict, dict]:
    data = load_ledger()
    ids = {q["id"] for q in data["questions"]}
    return data, load_answers(ids), load_asked(ids)


def cmd_check(args: argparse.Namespace) -> int:
    data, answers, asked = _state()
    fresh = render_ledger(data, answers, asked)
    current = RENDERED.read_text(encoding="utf-8") if RENDERED.exists() else ""
    _req(current == fresh, f"{RENDERED.relative_to(ROOT)} is stale — run `render --apply`")
    print(f"OK  {len(data['mechanisms'])} mechanisms · {len(data['questions'])} questions · "
          f"{sum(len(v) for v in answers.values())} answers · {len(asked)} asked")
    if getattr(args, "vault", False):
        vault = vault_root()
        status = vault_packet_status(vault)
        for name, state in status:
            print(f"vault  {state:<8} {VAULT_OUTBOX / name}")
        bad = [n for n, s in status if s != "present"]
        _req(not bad, f"{len(bad)} packet(s) not in the vault outbox as sent: {', '.join(bad)} — "
                      "`send --apply` writes a missing one")
        print(f"OK  vault {vault}: {len(status)} packet(s) present and byte-identical")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    data, answers, asked = _state()
    fresh = render_ledger(data, answers, asked)
    current = RENDERED.read_text(encoding="utf-8") if RENDERED.exists() else ""
    if current == fresh:
        print(f"unchanged  {RENDERED.relative_to(ROOT)}")
        return 0
    if not args.apply:
        print(f"would write  {RENDERED.relative_to(ROOT)} ({len(fresh.splitlines())} lines) — rerun with --apply")
        return 0
    RENDERED.write_text(fresh, encoding="utf-8")
    print(f"wrote  {RENDERED.relative_to(ROOT)}")
    return 0


def cmd_packet(args: argparse.Namespace) -> int:
    data, answers, asked = _state()
    qs = select_questions(data, answers, asked, priorities=args.priority or (),
                          mechanisms=args.mechanism or (), ids=args.ids or (),
                          include_asked=args.include_asked)
    created = args.date or _dt.date.today().isoformat()
    _date(created, "--date")
    slug = re.sub(r"[^a-z0-9-]+", "-", (args.slug or "questions").lower()).strip("-")
    packet_id = f"PMQ-{created}-{slug}"
    text = render_packet(data, qs, packet_id, created)
    repo_path = PACKETS_DIR / f"{packet_id}.md"
    if not args.apply:
        sys.stdout.write(text)
        print(f"\n# preview only — {len(qs)} questions; --apply writes {repo_path.relative_to(ROOT)}"
              + (" and the vault outbox" if args.vault else ""), file=sys.stderr)
        return 0
    _req(not repo_path.exists(), f"{repo_path.relative_to(ROOT)} exists — packets are immutable, pick a new --slug")
    vault = vault_root() if args.vault else None   # resolve before any write, so a bad vault writes nothing
    PACKETS_DIR.mkdir(parents=True, exist_ok=True)
    repo_path.write_text(text, encoding="utf-8")
    print(f"wrote  {repo_path.relative_to(ROOT)}")
    if vault is not None:
        path, digest = write_into_vault(vault, VAULT_OUTBOX / repo_path.name, text)
        print(f"wrote + verified in vault  {path}  sha256={digest[:12]}")
    print("next: render --apply (marks these questions `asked`)"
          + ("" if vault else "; send --apply copies it to the vault outbox"))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    vault = vault_root()
    status = dict(vault_packet_status(vault))
    wanted = [f"{i}.md" if not i.endswith(".md") else i for i in (args.ids or [])] or sorted(status)
    unknown = [w for w in wanted if w not in status]
    _req(not unknown, f"not a packet under {PACKETS_DIR.relative_to(ROOT)}: {', '.join(unknown)}")
    differs = [w for w in wanted if status[w] == "differs"]
    _req(not differs, f"vault copy differs from the repo for {', '.join(differs)} — "
                      "packets are immutable; find out who changed it before overwriting")
    todo = [w for w in wanted if status[w] == "missing"]
    for name in wanted:
        if status[name] == "present":
            print(f"present  {VAULT_OUTBOX / name}")
    if not todo:
        return 0
    if not args.apply:
        for name in todo:
            print(f"would write  {vault / VAULT_OUTBOX / name} — rerun with --apply")
        return 0
    for name in todo:
        path, digest = write_into_vault(vault, VAULT_OUTBOX / name,
                                        (PACKETS_DIR / name).read_text(encoding="utf-8"))
        print(f"wrote + verified in vault  {path}  sha256={digest[:12]}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    data = load_ledger()
    ids = {q["id"] for q in data["questions"]}
    src = Path(args.path).expanduser()
    text = src.read_text(encoding="utf-8")
    parsed = parse_answer_file(text, ids, src.name)
    for rec in parsed["answers"]:
        kinds = ",".join(sorted({s["kind"] for s in rec.get("sources") or []})) or "-"
        rule = _one_line(rec.get("rule") or "")
        print(f"{rec['id']:<11} {rec['status']:<13} sources={kinds:<28} {rule[:70]}")
    dest = ANSWERS_DIR / src.name
    if not args.apply:
        print(f"# valid — {len(parsed['answers'])} answers; --apply copies to {dest.relative_to(ROOT)}",
              file=sys.stderr)
        return 0
    if dest.exists():
        _req(dest.read_text(encoding="utf-8") == text,
             f"{dest.relative_to(ROOT)} exists with different content — answer files are immutable")
        print(f"unchanged  {dest.relative_to(ROOT)}")
        return 0
    ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"wrote  {dest.relative_to(ROOT)}\nnext: render --apply")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate ledger, answers, packets; fail if LEDGER.md is stale")
    c.add_argument("--vault", action="store_true",
                   help="also prove every repo packet sits byte-identical in the vault outbox")
    r = sub.add_parser("render", help="regenerate LEDGER.md")
    r.add_argument("--apply", action="store_true")
    k = sub.add_parser("packet", help="build a self-contained question packet")
    k.add_argument("--priority", nargs="+", choices=PRIORITIES)
    k.add_argument("--mechanism", nargs="+")
    k.add_argument("--ids", nargs="+")
    k.add_argument("--include-asked", action="store_true")
    k.add_argument("--slug")
    k.add_argument("--date", help="YYYY-MM-DD (default today)")
    k.add_argument("--apply", action="store_true")
    k.add_argument("--vault", action="store_true", help="with --apply, also write the vault outbox")
    s = sub.add_parser("send", help="copy repo packets missing from the vault outbox; --apply writes + verifies")
    s.add_argument("ids", nargs="*", help="packet ids (default: every packet)")
    s.add_argument("--apply", action="store_true")
    i = sub.add_parser("ingest", help="validate an answer file; --apply copies it into answers/")
    i.add_argument("path")
    i.add_argument("--apply", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"check": cmd_check, "render": cmd_render, "packet": cmd_packet, "send": cmd_send,
               "ingest": cmd_ingest}[args.cmd]
    try:
        return handler(args)
    except ConfidentialityError as exc:
        print(f"REFUSED (confidentiality): {exc}", file=sys.stderr)
        return 1
    except (LedgerError, OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

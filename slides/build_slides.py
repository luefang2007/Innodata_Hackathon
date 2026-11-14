#!/usr/bin/env python
"""Fill the organizers' five-slide template with our numbers and draw the figures on it.

Every number on the slides is read from the evidence files of the frozen set (submission_v2/), so the
deck cannot drift from what is submitted. No model is called.

    python slides/build_slides.py --team "Team Name" --members "A, B, C, D, E"

Needs python-pptx (not a pipeline dependency). The template, `Presentation Template.pptx`, is organizer
material: it sits in the repo folder locally and is not committed, and neither is the finished deck
(it travels in the submission ZIP: `python -m taskfactory bundle` picks up `*Track1*.pptx`).
Close the deck in PowerPoint before running this: PowerPoint locks the file while it is open.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
BLUE, INK, CARD, WHITE = "0070C0", "474747", "F1F2F2", "FFFFFF"
GREY, PALE, EDGE, DARK = "9CA3AF", "9CC3E6", "C9CED6", "111827"
FONT, MONO, SYMBOL = "League Spartan", "Consolas", "Segoe UI Symbol"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Short human names for the five features (the instance ids are long).
FEATURE = {
    101: "Lifecycle hooks around each subcommand",
    102: "Effective value and its origin in help pages",
    103: "Completion from a raw command line",
    104: "Option shorthands expanded by the parser",
    105: "Numbered menus for Choice prompts",
}


# ---------------------------------------------------------------- evidence

def evidence(sub: Path) -> dict:
    run = sorted(p for p in (sub / "runs").iterdir() if (p / "summary.json").exists())[-1]
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    cont = json.loads((sub / "probe_container" / "summary.json").read_text(encoding="utf-8"))["tasks"]
    selfcheck = (sub / "selfcheck" / "SUMMARY.txt").read_text(encoding="utf-8")
    div = (sub / "diversity.md").read_text(encoding="utf-8")
    tasks = []
    for r in summary["tasks"]:
        iid = r["instance_id"]
        m = re.search(rf"== {re.escape(iid)} ==\s*\n\s*oracle\s+reward=(\S+).*\n\s*nop\s+reward=(\S+)", selfcheck)
        if not m:
            raise SystemExit(f"no Harbor self-check record for {iid}")
        c = cont[iid]
        rounds = r.get("jury") or []
        first = f"{rounds[0]['solved_strict']}/{rounds[0]['k']}"
        final = f"{c['container_resolved']}/{c['graded']}"
        tasks.append({
            "iid": iid, "number": r["number"], "f2p": r["n_f2p"], "p2p": r["n_p2p"],
            "killed": r["mutation"]["killed"], "mutants": r["mutation"]["n"],
            "cell": f"{r['cell']['module']} × {r['cell']['shape']}",
            "oracle": float(m.group(1)), "nop": float(m.group(2)),
            "first": first, "final": final, "solved": c["container_resolved"], "graded": c["graded"],
            "agree": c["agree"], "attempts": c["attempts"],
            "failed_f2p": (rounds[-1].get("failed_f2p") or {}) if c.get("round") else {},
            "note": r.get("jury_note") or "",
        })
    ev = {
        "run": run.name, "requested": summary["requested"], "valid": summary["valid"],
        "base": summary["base_commit"], "seed": summary["seed"], "tasks": tasks,
        "max_sim": float(re.search(r"Max similarity ([\d.]+)", div).group(1)),
        "solved": sum(t["solved"] for t in tasks), "graded": sum(t["graded"] for t in tasks),
    }
    # The prose on the slides relies on these; fail loudly if the evidence ever says otherwise.
    assert all(t["oracle"] == 1.0 and t["nop"] == 0.0 for t in tasks), "a self-check baseline is off"
    assert all(t["agree"] for t in tasks), "container and host grading disagree"
    assert ev["valid"] == ev["requested"] == len(tasks)
    return ev


# ---------------------------------------------------------------- template text helpers

def by_name(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KeyError(name)


def _style(rpr, style: dict | None) -> None:
    if not style:
        return
    if "b" in style:
        rpr.set("b", "1" if style["b"] else "0")
    if "size" in style:
        rpr.set("sz", str(int(round(style["size"] * 100))))
    if "color" in style:
        rpr.find(qn("a:solidFill")).find(qn("a:srgbClr")).set("val", style["color"])
    if "font" in style:
        for tag in ("a:latin", "a:ea"):
            rpr.find(qn(tag)).set("typeface", style["font"])


def set_par(p, segments, line: int | None = None, before: int | None = None) -> None:
    """Rewrite one <a:p>. `segments` is a list of (text, style); the first run is the format prototype."""
    runs = p.findall(qn("a:r"))
    proto = runs[0]
    for r in runs:
        p.remove(r)
    anchor = p.find(qn("a:endParaRPr"))
    for text, style in segments:
        r = copy.deepcopy(proto)
        t = r.find(qn("a:t"))
        t.text = text
        if text != text.strip():
            t.set(XML_SPACE, "preserve")
        _style(r.find(qn("a:rPr")), style)
        if anchor is not None:
            anchor.addprevious(r)
        else:
            p.append(r)
    ppr = p.find(qn("a:pPr"))
    if line is not None:
        ppr.find(qn("a:lnSpc")).find(qn("a:spcPct")).set("val", str(line))
    if before is not None:
        sb = ppr.find(qn("a:spcBef"))
        if sb is None:
            sb = copy.deepcopy(ppr.find(qn("a:lnSpc")))
            sb.tag = qn("a:spcBef")
            for ch in list(sb):
                sb.remove(ch)
            sb.append(sb.makeelement(qn("a:spcPts"), {"val": str(before)}))
            ppr.find(qn("a:lnSpc")).addnext(sb)
        else:
            sb.find(qn("a:spcPts")).set("val", str(before))


def fill(shape, body, base: dict | None = None, label: str | None = None, line: int | None = None) -> None:
    """Replace the last paragraph of a text box by `body` (a list; each item is a string, a list of
    (text, style) segments, or a dict {"segs": [...], "line": .., "before": ..}). Formatting is cloned
    from the template paragraph; `base` is a style applied to every run, `line` a line spacing in
    thousandths of a percent for every paragraph. An empty `body` leaves only the label."""
    tx = shape.text_frame._txBody
    ps = tx.findall(qn("a:p"))
    if label is not None:
        set_par(ps[0], [(label, None)])
    proto = ps[-1]
    for para in body:
        opts = {"line": line} if line is not None else {}
        if isinstance(para, dict):
            opts.update({k: para[k] for k in ("line", "before") if k in para})
            para = para["segs"]
        segs = [(para, None)] if isinstance(para, str) else para
        segs = [(t, {**(base or {}), **(s or {})}) for t, s in segs]
        p = copy.deepcopy(proto)
        set_par(p, segs, **opts)
        proto.addprevious(p)
    tx.remove(proto)


def place(slide, names, top: float, height: float) -> None:
    for n in names:
        sh = by_name(slide, n)
        sh.top, sh.height = Inches(top), Inches(height)


def clone(slide, src, name: str, left: float, top: float, width: float, height: float):
    tree = slide.shapes._spTree
    nid = max(int(e.get("id")) for e in tree.iter(qn("p:cNvPr"))) + 1
    el = copy.deepcopy(src._element)
    c = el.find(".//" + qn("p:cNvPr"))
    c.set("id", str(nid))
    c.set("name", name)
    tree.append(el)
    sh = by_name(slide, name)
    sh.left, sh.top, sh.width, sh.height = Inches(left), Inches(top), Inches(width), Inches(height)
    return sh


def remove(slide, name: str) -> None:
    el = by_name(slide, name)._element
    el.getparent().remove(el)


# ---------------------------------------------------------------- drawing helpers (new shapes)

def rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_)


def box(slide, name: str, left: float, top: float, width: float, height: float, fill_hex: str | None = None,
        line_hex: str | None = None, kind=MSO_SHAPE.ROUNDED_RECTANGLE, radius: float = 0.12):
    sh = slide.shapes.add_shape(kind, Inches(left), Inches(top), Inches(width), Inches(height))
    sh.name = name
    sh.shadow.inherit = False
    if fill_hex:
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill_hex)
    else:
        sh.fill.background()
    if line_hex:
        sh.line.color.rgb = rgb(line_hex)
        sh.line.width = Pt(1)
    else:
        sh.line.fill.background()
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = radius
    return sh


def put_text(sh, lines, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, margin: float = 0.06):
    """One paragraph per item. An item is a run (text, size, color, bold[, font]) or a list of runs."""
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = anchor
    for i, item in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        for text, size, color, bold, *font in (item if isinstance(item, list) else [item]):
            r = p.add_run()
            r.text = text
            r.font.size, r.font.bold = Pt(size), bold
            r.font.name = font[0] if font else FONT
            r.font.color.rgb = rgb(color)
    return sh


def textbox(slide, name: str, left: float, top: float, width: float, height: float, lines,
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tb.name = name
    return put_text(tb, lines, align, anchor, margin=0)


def stroke(slide, name: str, x1: float, y1: float, x2: float, y2: float, color: str, width: float = 1.5,
           arrow: bool = False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.name = name
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if arrow:
        tail = OxmlElement("a:tailEnd")
        tail.set("type", "triangle")
        c.get_or_add_ln().append(tail)
    return c


def dots(frac: str, on: str = BLUE, size: float = 12) -> list:
    """'1/3' becomes runs that draw one filled and two hollow circles."""
    solved, k = (int(x) for x in frac.split("/"))
    runs = []
    if solved:
        runs.append(("●" * solved, size, on, False, SYMBOL))
    if k - solved:
        runs.append(("○" * (k - solved), size, GREY, False, SYMBOL))
    return runs


def cell_text(cell, text, size: float = 12, color: str = INK, bold: bool = False, align=PP_ALIGN.LEFT,
              fill_hex: str = CARD, font: str = FONT) -> None:
    """`text` is a string, or a list of runs (text, size, color, bold[, font])."""
    cell.fill.solid()
    cell.fill.fore_color.rgb = rgb(fill_hex)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = cell.margin_right = Inches(0.08)
    cell.margin_top = cell.margin_bottom = Inches(0.03)
    p = cell.text_frame.paragraphs[0]
    p.alignment = align
    runs = [(text, size, color, bold, font)] if isinstance(text, str) else text
    for t, sz, col, b, *f in runs:
        r = p.add_run()
        r.text = t
        r.font.size, r.font.bold, r.font.name = Pt(sz), b, (f[0] if f else font)
        r.font.color.rgb = rgb(col)


def draw_flow(slide, top: float) -> None:
    """The pipeline as five boxes and two feedback loops: repair (grey) and deepen (blue, the part that is ours)."""
    x0, w, gap, h = 1.11, 1.86, 0.4525, 0.84
    y = top + 0.46
    steps = [("1  Plan", ["variation matrix:", "area × kind of change"], False),
             ("2  Write", ["Opus 5.5: feature request,", "reference solution, tests"], False),
             ("3  Check", ["8 local checks on the", "base and the reference"], False),
             ("4  Measure", ["solver jury: Haiku 5.5", "attempts it 3 times"], True),
             ("5  Package", ["Harbor task,", "ready to grade"], False)]
    centers = []
    for i, (name, sub, ours) in enumerate(steps):
        x = x0 + i * (w + gap)
        centers.append(x + w / 2)
        b = box(slide, f"flow-step{i + 1}", x, y, w, h, BLUE if ours else WHITE, None if ours else EDGE)
        put_text(b, [(name, 13, WHITE if ours else DARK, True)]
                 + [(s, 10, WHITE if ours else INK, False) for s in sub])
        if i < len(steps) - 1:
            box(slide, f"flow-arrow{i + 1}", x + w + 0.09, y + h / 2 - 0.1, gap - 0.18, 0.2, GREY,
                kind=MSO_SHAPE.RIGHT_ARROW)
    yb = y + h
    # repair: Check -> Write
    xs, xe, yh = centers[2], centers[1] + 0.3, yb + 0.22
    stroke(slide, "repair-1", xs, yb, xs, yh, "8A8F98")
    stroke(slide, "repair-2", xs, yh, xe, yh, "8A8F98")
    stroke(slide, "repair-3", xe, yh, xe, yb + 0.02, "8A8F98", arrow=True)
    textbox(slide, "repair-label", xe + 0.1, yh + 0.02, 3.6, 0.2,
            [("a check failed: repair with the exact report", 10, "6B7280", False)])
    # deepen: Measure -> Write
    xs, xe, yh = centers[3], centers[1] - 0.3, yb + 0.58
    stroke(slide, "deepen-1", xs, yb, xs, yh, BLUE, 2)
    stroke(slide, "deepen-2", xs, yh, xe, yh, BLUE, 2)
    stroke(slide, "deepen-3", xe, yh, xe, yb + 0.02, BLUE, 2, arrow=True)
    textbox(slide, "deepen-label", xe + 0.1, yh + 0.02, 6.2, 0.2,
            [("solved more than once: deepen the task, then check and measure it again", 10, BLUE, True)])


# ---------------------------------------------------------------- the five slides

def build(template: Path, out: Path, sub: Path, team: str, members: str) -> dict:
    ev = evidence(sub)
    rows = ev["tasks"]
    hard = min(rows, key=lambda t: t["solved"] / t["graded"])                 # the task the juror failed most
    per_task = ", ".join(t["final"] for t in rows)
    miss = next(a for a in hard["attempts"] if not a["container_resolved"])
    f_ok, f_all = miss["f2p"].split("/")
    n_miss = sum(1 for a in hard["attempts"] if not a["container_resolved"])
    cut = sum(1 for t in rows if "truncated" in t["note"])
    easy = sum(1 for t in rows if t["solved"] == t["graded"])
    files = sorted({l[6:] for l in (sub / "generated_tasks" / hard["iid"] / "solution" / "patch.diff")
                    .read_text(encoding="utf-8").splitlines() if l.startswith("+++ b/")})

    prs = Presentation(str(template))
    s1, s2, s3, s4, s5 = prs.slides
    card_src, card_text_src = by_name(s2, "card1"), by_name(s2, "card1-text")      # prototypes for new cards

    # 1 ---- title
    fill(by_name(s1, "title"), ["Solver-in-the-Loop Task Factory"])
    fill(by_name(s1, "team"), [f"{team} · Coding Task Factory"])
    fill(by_name(s1, "members"), [members])
    by_name(s1, "pitch").height = Inches(1.15)
    fill(by_name(s1, "pitch"), [
        "Our pipeline generates feature-development tasks on pallets/click to test whether a model can add a "
        "precisely specified behavior to a real codebase without breaking what already works. Before a task "
        "ships, the pipeline measures how often the benchmark model solves it."], line=130000)
    by_name(s1, "example").top = Inches(6.15)
    fill(by_name(s1, "example"), [
        f"Example: adding lifecycle hooks around each subcommand of a click command group. The change spans "
        f"{len(files)} files; {hard['f2p']} new tests must pass and {hard['p2p']} existing tests must stay green."],
        line=130000)

    # 2 ---- how we generate a task: input, the pipeline as a diagram, what changes
    flow_top = 3.18
    place(s2, ("card1", "card1-text"), 2.06, 1.0)
    place(s2, ("card2",), flow_top, 2.2)
    place(s2, ("card2-text",), flow_top + 0.12, 0.3)
    place(s2, ("card3", "card3-text"), 5.50, 1.12)
    fill(by_name(s2, "card1-text"), [
        f"pallets/click at tag 8.5.1 (commit {ev['base'][:8]}) and a seed. The seed picks {len(rows)} cells "
        f"of a variation matrix: six areas of click by five kinds of behavior."], base={"size": 14})
    fill(by_name(s2, "card2-text"), [])                                      # keep the label; the diagram follows
    draw_flow(s2, flow_top)
    fill(by_name(s2, "card3-text"), [
        f"Task 0101 (lifecycle hooks): call order, exceptions and re-entrancy across {len(files)} files. "
        f"Task 0103 (shell completion): splitting a half-typed command line. Measured overlap between any two "
        f"tasks: at most {ev['max_sim']:.2f} on a scale from 0 to 1."], base={"size": 14})

    # 3 ---- live demo
    place(s3, ("demo-card", "demo-bar", "demo-text"), 2.08, 2.42)
    code = {"font": MONO, "size": 13.5}
    dim = {"font": MONO, "size": 13.5, "color": GREY}
    fill(by_name(s3, "demo-text"), [
        {"segs": [("Entry point: one script. The model's recorded answers are replayed, so the demo spends "
                   "nothing; every check runs live.", {"size": 16})], "line": 125000},
        {"segs": [("PS> ", dim), (".\\demo", {"font": MONO, "size": 17, "b": True, "color": "7CC4FF"})],
         "line": 130000, "before": 900},
        {"segs": [("1  generate   ", dim),
                  ("python -m taskfactory generate --n 5 --seed 1 --out ../demo/generated_tasks", code)],
         "line": 125000, "before": 700},
        {"segs": [("2  show       ", dim),
                  ("the feature request, the checks on base and reference, the existing tests", code)],
         "line": 125000, "before": 0},
        {"segs": [("3  measure    ", dim), ("how often Haiku solved each submitted task", code),
                  ("   (--regrade: in the containers)", dim)], "line": 125000, "before": 0},
    ])
    watch = [
        ("THE FEATURE REQUEST",
         "instruction.md of a package made in front of you: what to build, written as observable behavior."),
        ("CHECKS ON BASE AND REFERENCE",
         "The new tests fail on click 8.5.1 and pass with the reference solution. In Harbor: nop 0.0, oracle 1.0."),
        ("WHICH EXISTING TESTS RAN",
         "Up to 80 of click's own tests, chosen by coverage of the lines the change touches. All must stay green."),
    ]
    w, gap, top, h = (11.78 - 2 * 0.2) / 3, 0.2, 4.72, 1.62
    for i, (label, text) in enumerate(watch):
        left = 0.78 + i * (w + gap)
        clone(s3, card_src, f"watch{i + 1}", left, top, w, h)
        tb = clone(s3, card_text_src, f"watch{i + 1}-text", left + 0.25, top, w - 0.5, h)
        fill(tb, [text], base={"size": 13.5}, label=label)

    # 4 ---- what the runs showed: four numbers, the required sentences, one row per task
    remove(s4, "card-visual")
    remove(s4, "card-visual-text")
    tw, tg, t_y, t_h = (11.78 - 3 * 0.16) / 4, 0.16, 2.06, 0.94
    tiles = [
        ([(f"{ev['valid']} / {ev['requested']}", 26, BLUE, True)], "unedited packages passed every check"),
        ([("1.0 / 0.0", 26, BLUE, True)], "Harbor oracle / nop on all five"),
        ([(f"{ev['solved']} / {ev['graded']}", 26, BLUE, True)], "Haiku one-shot attempts solved"),
        (dots(hard["first"], PALE, 22) + [("  →  ", 20, GREY, False)] + dots(hard["final"], BLUE, 22),
         f"task {hard['number']:04d} after one deepening round"),
    ]
    for i, (big, caption) in enumerate(tiles):
        tile = box(s4, f"tile{i + 1}", 0.78 + i * (tw + tg), t_y, tw, t_h, CARD, radius=0.1)
        put_text(tile, [big, (caption, 10.5, INK, False)])
    card_top, card_h = t_y + t_h + 0.14, 1.62
    place(s4, ("card-coding", "card-coding-text"), card_top, card_h)
    rule = ("a hook registered while a chain of subcommands runs must apply to the later ones"
            if any("registered_during_invocation" in k for k in hard["failed_f2p"]) else
            "see the jury log of that task")
    fill(by_name(s4, "card-coding-text"), [
        [("Model: anthropic/claude-haiku-5.5. ", {"b": True}),
         (f"{ev['solved']} successful / {ev['graded']} completed graded attempts, one-shot inside the pipeline "
          f"(k = 3) and re-graded in the task containers. The official terminus-2 resolve@k has not been run "
          f"yet.", None)],
        [("One failure: ", {"b": True}),
         (f"on task {hard['number']:04d}, {n_miss} of {hard['graded']} attempts failed the same test ({f_ok} of "
          f"{f_all} feature tests passed, no existing test broke): {rule}.", None)],
    ], base={"size": 13}, line=120000)

    cols = [("TASK", 0.6), ("FEATURE ADDED TO CLICK", 3.4), ("AREA × KIND OF CHANGE", 1.85),
            ("NEW / EXISTING TESTS", 1.82), ("BUGS CAUGHT", 1.2), ("ORACLE / NOP", 1.15),
            ("HAIKU SOLVED (OF 3)", 1.76)]
    row_h, t_top = 0.28, card_top + card_h + 0.14
    gf = s4.shapes.add_table(len(rows) + 1, len(cols), Inches(0.78), Inches(t_top), Inches(11.78),
                             Inches(row_h * (len(rows) + 1)))
    gf.name = "results-table"
    tbl = gf.table
    tbl.horz_banding = False
    for i, (head, width) in enumerate(cols):
        tbl.columns[i].width = Inches(width)
        cell_text(tbl.cell(0, i), head, 9.5, BLUE, True, PP_ALIGN.LEFT if i < 3 else PP_ALIGN.CENTER, WHITE)
    for r_i, t in enumerate(rows, start=1):
        tbl.rows[r_i].height = Inches(row_h)
        shade = CARD if r_i % 2 else WHITE
        changed = t["first"] != t["final"]
        vals = [f"{t['number']:04d}", FEATURE.get(t["number"], t["iid"].split("-", 2)[-1]), t["cell"],
                f"{t['f2p']} / {t['p2p']}", f"{t['killed']} / {t['mutants']}", f"{t['oracle']:.1f} / {t['nop']:.1f}"]
        for c_i, v in enumerate(vals):
            cell_text(tbl.cell(r_i, c_i), v, 12, INK, bold=c_i == 0,
                      align=PP_ALIGN.LEFT if c_i < 3 else PP_ALIGN.CENTER, fill_hex=shade)
        runs = (dots(t["first"], PALE, 13) + [(" → ", 11, GREY, False)] if changed else []) \
            + dots(t["final"], BLUE, 13) + [(f"  {t['final']}", 12, BLUE if changed else INK, changed)]
        cell_text(tbl.cell(r_i, len(vals)), runs, align=PP_ALIGN.CENTER, fill_hex=shade)
    tbl.rows[0].height = Inches(row_h)

    # 5 ---- what we learned (the finding gets its before / after picture)
    for i, top in enumerate((2.06, 3.62, 5.18), start=1):       # a little taller than the template: room for 3 lines
        place(s5, (f"card{i}", f"card{i}-text"), top, 1.36)
    by_name(s5, "card1-text").width = Inches(8.3)
    fill(by_name(s5, "card1-text"), [
        f"A task can pass every validity check and still be trivial. Before deepening, all {len(rows)} tasks were "
        f"valid and Haiku solved each {rows[0]['first'].replace('/', ' times out of ')}. With the solver in the "
        f"loop, one deepening round took task {hard['number']:04d} from {hard['first']} to {hard['final']}, still "
        f"passing every check."], base={"size": 14})
    k, was, now = hard["graded"], int(hard["first"].split("/")[0]), hard["solved"]
    gx, gy, d, step = 9.95, 2.06 + 0.62, 0.26, 0.32
    textbox(s5, "dots-title", 9.7, 2.06 + 0.24, 2.7, 0.2,
            [(f"TASK {hard['number']:04d} · HAIKU ATTEMPTS", 9, BLUE, True)], align=PP_ALIGN.CENTER)
    for j in range(k):
        box(s5, f"dot-before{j + 1}", gx + j * step, gy, d, d, PALE if j < was else None,
            None if j < was else GREY, kind=MSO_SHAPE.OVAL)
    ax = gx + k * step + 0.06
    box(s5, "dots-arrow", ax, gy + 0.04, 0.3, 0.18, GREY, kind=MSO_SHAPE.RIGHT_ARROW)
    bx = ax + 0.3 + 0.12
    for j in range(k):
        box(s5, f"dot-after{j + 1}", bx + j * step, gy, d, d, BLUE if j < now else None,
            None if j < now else GREY, kind=MSO_SHAPE.OVAL)
    textbox(s5, "dots-before", gx - 0.2, gy + d + 0.05, k * step + 0.34, 0.2, [("before", 9, "6B7280", False)],
            align=PP_ALIGN.CENTER)
    textbox(s5, "dots-after", bx - 0.2, gy + d + 0.05, k * step + 0.34, 0.2, [("after deepening", 9, "6B7280", False)],
            align=PP_ALIGN.CENTER)
    fill(by_name(s5, "card2-text"), [
        f"{easy} of {len(rows)} tasks are still easy by our measure: {cut} deepening answers were cut off at the "
        f"output-token limit, and one deepened task stayed 3/3. Our measure is a one-shot proxy, so the official "
        f"terminus-2 benchmark is still to run."], base={"size": 14})
    fill(by_name(s5, "card3-text"), [
        "Run terminus-2 with Haiku 5.5 on the same five tasks and compare with the jury's numbers: does a cheap "
        "one-shot proxy predict the benchmark? Then repeat deepening with a larger output limit."],
        base={"size": 14})

    # speaker notes (replace the template's instructions)
    notes = [
        "30 s. We built a task factory for pallets/click. Other pipelines claim their tasks are hard and fair; "
        "ours measures it. The benchmark model sits inside the generation loop, so every task ships with a "
        "measured solve rate, a measured overlap with its sibling tasks, and proof that its tests are sound.",
        "30 s. Input: click 8.5.1 and a seed. Read the diagram left to right. Plan: a variation matrix gives "
        "each task a different area of click and a different kind of behavior. Write: Opus writes the feature "
        "request, the reference solution and the tests. Check: eight local checks reject broken, unfair or "
        "already-upstream tasks (new tests fail on the base and pass on the reference, existing tests stay "
        "green, injected bugs are caught, tests assert only what the request states, the feature is not already "
        "in upstream click). The grey loop is repair: a failed check goes back to the model with the exact "
        "report. The blue loop is ours: Haiku attempts the task three times, and if it solves it more than once "
        "the task is deepened, then checked and measured again.",
        "2.5 min. In PowerShell, from the repo folder, run .\\demo (about 90 seconds). It replays the model "
        "answers recorded in the response cache, so it spends nothing, and runs every check for real. While it "
        "runs, read the log: design, then each check. Offline, 4 of 5 packages complete; the fifth (0103) has a "
        "first draft that fails its own tests, and the repair step is a live model call, so the replay stops "
        "there (in the recorded run it passed on attempt 2). Then the script prints (1) the feature request of "
        "one fresh package, (2) each check with PASS or FAIL: new tests fail on the base and pass with the "
        "reference, (3) the existing tests that ran, and (4) how often Haiku solved each submitted task, plus "
        "the recorded Harbor oracle / nop result. Two things to say out loud: the cost column in the table is "
        "what the replayed answers cost when they were first generated, and offline the solver jury is skipped "
        "(`no graded attempt`), which is why its recorded result is shown in the last step. A truly fresh "
        "generation needs the model key and about 15 minutes.",
        f"1 min. The four numbers first. {ev['valid']} of {ev['requested']} packages valid and unedited, in two "
        f"separate runs; every check means 8 local gates, the kit's structure check, and Harbor oracle 1.0 / nop "
        f"0.0 in images built from each package's own Dockerfile. Haiku one-shot solved {ev['solved']} of "
        f"{ev['graded']} graded attempts; these are solver-jury attempts re-graded in the task containers, not "
        f"terminus-2 rollouts, and the official resolve@k is not run. The table has one row per task: new tests "
        f"/ existing tests, injected bugs caught by the tests, and the Haiku result as dots. Task "
        f"{hard['number']:04d} went from {hard['first']} to {hard['final']} after one deepening round.",
        "30 s. Finding: validity does not imply difficulty, and measuring inside the loop is what exposed it. "
        "Limitation: four tasks are still easy; three deepening answers were cut off at the output limit. Next: "
        "run the official benchmark and check whether the cheap proxy predicts it.",
    ]
    for slide, text in zip(prs.slides, notes):
        slide.notes_slide.notes_text_frame.text = text

    prs.save(str(out))
    left = []
    for i, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            if sh.has_text_frame and re.search(r"\[[^\]]+\]", sh.text_frame.text):
                left.append(f"slide {i}: {sh.text_frame.text.strip()[:70]}")
    return {"out": str(out), "evidence_run": ev["run"], "brackets_left": left,
            "numbers": {"valid": f"{ev['valid']}/{ev['requested']}", "haiku": f"{ev['solved']}/{ev['graded']}",
                        "per_task": per_task, "max_similarity": ev["max_sim"], "deepen_cut_off": cut}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", type=Path, default=ROOT / "Presentation Template.pptx")
    ap.add_argument("--submission", type=Path, default=ROOT / "submission_v2")
    ap.add_argument("--out", type=Path, default=ROOT / "Track1_Slides.pptx")
    ap.add_argument("--team", default="[Team name]")
    ap.add_argument("--members", default="[Member names]")
    a = ap.parse_args()
    print(json.dumps(build(a.template, a.out, a.submission, a.team, a.members), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

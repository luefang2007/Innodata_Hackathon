"""The variation matrix: which part of click a task extends x what shape of behaviour it adds.

A batch is a set of cells with no repeated module and no repeated shape, so two tasks never exercise
the same capability in the same way. The assignment is a seeded search over permutations, restricted
to (module, shape) pairs that make sense for click, so a given seed always yields the same plan.
"""
from __future__ import annotations

import itertools
import random

MODULES = {
    "parser": {
        "area": "command-line tokenisation and option/argument parsing (how argv is split into options, "
                "values and arguments; prefixes, clustering, `--`, nargs)",
        "files": ["src/click/parser.py", "src/click/core.py"],
    },
    "types": {
        "area": "parameter types and value conversion / validation (ParamType subclasses, conversion "
                "errors, shell_complete of types, to_info_dict)",
        "files": ["src/click/types.py"],
    },
    "core": {
        "area": "Context / Command / Group runtime: invocation order, callbacks, chained groups, "
                "context settings and parent/child propagation, parameter sources",
        "files": ["src/click/core.py"],
    },
    "help": {
        "area": "help page generation and formatting (usage line, option and command records, sections, "
                "wrapping, HelpFormatter)",
        "files": ["src/click/formatting.py", "src/click/core.py"],
    },
    "termui": {
        "area": "interactive terminal features (prompt, confirm, choice prompts, progress) and the CliRunner "
                "test harness",
        "files": ["src/click/termui.py", "src/click/testing.py"],
    },
    "completion": {
        "area": "shell completion: CompletionItem, completion sources, per-shell output formats and the "
                "completion environment protocol",
        "files": ["src/click/shell_completion.py"],
    },
}

SHAPES = {
    "grammar": "a new input grammar: parse and validate a structured textual value or token sequence, with "
               "precise acceptance/rejection rules, normalisation and exact error messages",
    "interaction": "a rule system that interacts with existing click features (value sources: command line, "
                   "environment variables, default_map, defaults and prompts; multiple/nargs; required) with "
                   "a precisely defined precedence and conflict policy",
    "lifecycle": "state and lifecycle behaviour: ordering of callbacks/hooks, context propagation between parent "
                 "and child commands, chained/group invocation, cleanup",
    "output": "an exact output format: deterministic rendering (help text, listings, reports, completion "
              "payloads) with precise layout, ordering, wrapping and escaping rules",
    "api": "a new public extension API: a class, decorator or hook third-party code can use, with documented "
           "contracts, validation of misuse and introspection (e.g. to_info_dict)",
}

AFFINITY = {
    "parser": {"grammar", "interaction", "api"},
    "types": {"grammar", "interaction", "api", "output"},
    "core": {"interaction", "lifecycle", "api"},
    "help": {"output", "api", "interaction"},
    "termui": {"grammar", "output", "lifecycle", "interaction"},
    "completion": {"output", "api", "grammar"},
}


def plan(n: int, seed: int) -> list[dict]:
    """Return n cells; the first min(n, 5) have distinct modules and distinct shapes."""
    rng = random.Random(seed)
    modules = list(MODULES)
    rng.shuffle(modules)
    shapes = list(SHAPES)
    rng.shuffle(shapes)
    k = min(n, len(shapes), len(modules))
    chosen = None
    for mods in itertools.permutations(modules, k):
        for shp in itertools.permutations(shapes, k):
            if all(s in AFFINITY[m] for m, s in zip(mods, shp)):
                chosen = list(zip(mods, shp))
                break
        if chosen:
            break
    cells = chosen or list(zip(modules, shapes))[:k]
    # beyond five tasks, keep cycling through valid pairs that are not used yet
    pool = [(m, s) for m in modules for s in shapes if s in AFFINITY[m] and (m, s) not in cells]
    rng.shuffle(pool)
    cells += pool[: max(0, n - len(cells))]
    return [{"module": m, "shape": s, "area": MODULES[m]["area"], "files": MODULES[m]["files"],
             "shape_desc": SHAPES[s]} for m, s in cells[:n]]

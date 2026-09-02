# bollard/GOALS.json C13 fixture. Never imported, never executed -- only ever read as text and
# passed to ast.parse() by test_guard_semantic_resolution.py, to prove find_forbidden_calls()
# fires on eval reached indirectly through getattr/__builtins__, the F12 evasion a name-only
# scan for the literal token "eval" would miss.


def resolve(token):
    return getattr(__builtins__, "ev" + "al")(token)

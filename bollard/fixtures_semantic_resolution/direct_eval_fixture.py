# bollard/GOALS.json C13 fixture. Never imported, never executed -- only ever read as text and
# passed to ast.parse() by test_guard_semantic_resolution.py, to prove find_forbidden_calls()
# fires on a direct, name-matched eval() call.


def resolve(token):
    return eval(token)

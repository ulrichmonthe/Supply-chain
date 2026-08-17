"""Guard the supported Python floor.

The application must run on the `python3` that ships with macOS, which is 3.9. That
is not a stylistic preference: a tool sold on surviving without its authors should
not open by telling a ministry analyst to install a new interpreter.

Nothing here needs a 3.9 interpreter to run. These tests compile every module against
3.9's grammar and scan for the constructs that pass a syntax check but fail at import
under an evaluated annotation, so the floor is enforced on whatever version CI uses.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted(BACKEND.rglob("app/**/*.py")) + sorted(BACKEND.rglob("tests/*.py"))

MIN_VERSION = (3, 9)


def _relative(path: pathlib.Path) -> str:
    return str(path.relative_to(BACKEND))


@pytest.mark.parametrize("path", SOURCES, ids=_relative)
def test_module_parses_under_the_oldest_supported_python(path: pathlib.Path):
    """Catches match statements, except*, and other post-3.9 grammar."""
    try:
        ast.parse(path.read_text(), filename=str(path), feature_version=MIN_VERSION[1])
    except SyntaxError as exc:  # pragma: no cover - only fires on a regression
        pytest.fail(f"{_relative(path)}:{exc.lineno} is not valid Python 3.9: {exc.msg}")


@pytest.mark.parametrize("path", SOURCES, ids=_relative)
def test_no_pep604_unions(path: pathlib.Path):
    """`X | None` parses on 3.9 but raises when anything evaluates the annotation.

    SQLAlchemy resolves `Mapped[...]`, Pydantic resolves model fields and FastAPI
    resolves route signatures, so a single `str | None` in the wrong place takes the
    whole application down on 3.9 with a TypeError far from its cause. Banning the
    syntax outright is simpler than reasoning about which positions are evaluated.
    """
    tree = ast.parse(path.read_text())
    offences = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.BitOr)
        and _looks_like_a_type(node)
    ]
    assert not offences, (
        f"{_relative(path)} uses `X | Y` type syntax at line(s) {offences}. "
        f"Use Optional[X] / Union[X, Y] so the module imports on Python 3.9."
    )


def _looks_like_a_type(node: ast.BinOp) -> bool:
    """True for `A | B` where both sides look like type expressions, not values."""
    return _is_type_expr(node.left) and _is_type_expr(node.right)


def _is_type_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.Name):
        # A bare capitalised name, or a builtin container used as a generic base.
        return node.id[:1].isupper() or node.id in {"str", "int", "float", "bool", "bytes", "list", "dict", "tuple", "set"}
    if isinstance(node, ast.Attribute):
        return node.attr[:1].isupper()
    if isinstance(node, ast.Subscript):
        return _is_type_expr(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _looks_like_a_type(node)
    return False


@pytest.mark.parametrize("path", SOURCES, ids=_relative)
def test_no_dataclass_slots(path: pathlib.Path):
    """`@dataclass(slots=True)` is 3.10+, and fails at import rather than at parse."""
    tree = ast.parse(path.read_text())
    offences = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dataclass"
        and any(keyword.arg == "slots" for keyword in node.keywords)
    ]
    assert not offences, (
        f"{_relative(path)} passes slots= to @dataclass at line(s) {offences}, "
        f"which needs Python 3.10."
    )

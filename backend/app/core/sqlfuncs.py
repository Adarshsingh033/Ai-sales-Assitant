"""
Small SQL functions that differ between PostgreSQL (production) and SQLite (the test database).
"""
from sqlalchemy import DateTime, String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement


class greatest(FunctionElement):
    """Largest of several non-NULL timestamps: GREATEST(...) on PostgreSQL, scalar MAX(...) on SQLite."""

    type = DateTime(timezone=True)
    name = "greatest"
    inherit_cache = True


@compiles(greatest)
def _greatest_default(element, compiler, **kw):
    return f"GREATEST({compiler.process(element.clauses, **kw)})"


@compiles(greatest, "sqlite")
def _greatest_sqlite(element, compiler, **kw):
    return f"MAX({compiler.process(element.clauses, **kw)})"


class digits_only(FunctionElement):
    """A phone number reduced to its digits, so '+91 98765-43210' and '919876543210' compare equal."""

    type = String()
    name = "digits_only"
    inherit_cache = True


@compiles(digits_only)
def _digits_default(element, compiler, **kw):
    return f"regexp_replace({compiler.process(element.clauses, **kw)}, '[^0-9]', '', 'g')"


@compiles(digits_only, "sqlite")
def _digits_sqlite(element, compiler, **kw):
    expr = compiler.process(element.clauses, **kw)
    for ch in (" ", "-", "(", ")", "+", "."):  # the separators phone numbers are normally written with
        expr = f"replace({expr}, '{ch}', '')"
    return expr

"""Namespace isolation guard.

License Circuit Breaker shares one DataHub instance with four other hackathon
submissions. Every seed, reset, mutation, writeback, and enforcement target must
be proven to belong to this project's allocation before it is acted on.

The guard fails closed: anything that cannot be positively confirmed as in-namespace
raises :class:`NamespaceViolation`. There is no warn-and-continue path, and no
"apply to everything" mode -- a reset that cannot enumerate its targets is a reset
that does not run.

See ``../COORDINATOR_PLAN.md`` for the portfolio-wide isolation registry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


class NamespaceViolation(Exception):
    """Raised when an operation targets something outside this project's allocation.

    This is a blocking error, never a warning. Callers must not catch it to
    continue; catching it is only appropriate to report the violation and abort.
    """


# Matches the tuple form used by dataset/mlModel/mlFeatureTable style URNs:
#   urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)
_TUPLE_URN = re.compile(
    r"^urn:li:(?P<entity_type>[a-zA-Z]+):\("
    r"urn:li:dataPlatform:(?P<platform>[^,)]+),"
    r"(?P<name>[^,)]+),"
    r"(?P<env>[^,)]+)\)$"
)

# Matches the flat form used by tags, domains, glossary terms:
#   urn:li:tag:project-license-circuit-breaker
_FLAT_URN = re.compile(r"^urn:li:(?P<entity_type>[a-zA-Z]+):(?P<name>[^(),]+)$")

# Tokens that indicate a caller is trying to operate on everything. A global
# delete against the shared instance would destroy four other submissions'
# demo state, so these are rejected outright rather than pattern-matched.
_GLOBAL_TOKENS = frozenset({"*", "**", "%", "all", "ALL", "", "urn:li:*"})


@dataclass(frozen=True)
class ParsedUrn:
    """A DataHub URN decomposed into the parts the guard needs."""

    entity_type: str
    name: str
    platform: str | None = None
    env: str | None = None


@dataclass(frozen=True)
class Namespace:
    """This project's coordinator-assigned allocation.

    Values come from the shared environment contract and must match the registry
    in ``../COORDINATOR_PLAN.md``. Changing them requires a coordinator proposal.
    """

    project_slug: str
    urn_prefix: str
    project_tag: str
    domain: str

    def __post_init__(self) -> None:
        if not self.urn_prefix:
            raise ValueError("urn_prefix must not be empty; an empty prefix matches everything")
        if not self.project_tag:
            raise ValueError("project_tag must not be empty")


def parse_urn(urn: str) -> ParsedUrn:
    """Decompose a DataHub URN.

    Raises:
        NamespaceViolation: if the URN is malformed. An unparseable URN cannot be
            proven in-namespace, so it is treated as a violation rather than
            passed through.
    """
    if not isinstance(urn, str) or not urn.strip():
        raise NamespaceViolation("Empty or non-string URN cannot be namespace-checked")

    urn = urn.strip()

    match = _TUPLE_URN.match(urn)
    if match:
        return ParsedUrn(
            entity_type=match.group("entity_type"),
            name=match.group("name"),
            platform=match.group("platform"),
            env=match.group("env"),
        )

    match = _FLAT_URN.match(urn)
    if match:
        return ParsedUrn(entity_type=match.group("entity_type"), name=match.group("name"))

    raise NamespaceViolation(f"Unparseable URN, cannot prove namespace membership: {urn!r}")


def is_in_namespace(urn: str, namespace: Namespace) -> bool:
    """Return whether ``urn`` belongs to this project.

    Tag and domain URNs are matched exactly against the allocation. Everything
    else must carry the entity-name prefix. Returns False rather than raising for
    malformed input, so this is safe for filtering; use :func:`require_in_namespace`
    when a violation should abort.
    """
    try:
        parsed = parse_urn(urn)
    except NamespaceViolation:
        return False

    if parsed.entity_type == "tag":
        return parsed.name == namespace.project_tag
    if parsed.entity_type == "domain":
        return parsed.name in (namespace.domain, namespace.project_slug)

    return parsed.name.startswith(namespace.urn_prefix)


def require_in_namespace(urn: str, namespace: Namespace, operation: str) -> str:
    """Assert that ``urn`` is in-namespace, returning it unchanged.

    Raises:
        NamespaceViolation: if the URN is outside this project's allocation.
    """
    if urn in _GLOBAL_TOKENS:
        raise NamespaceViolation(
            f"{operation!r} refused: {urn!r} is a global selector. "
            "Operations must enumerate explicit in-namespace targets."
        )
    if not is_in_namespace(urn, namespace):
        raise NamespaceViolation(
            f"{operation!r} refused: {urn!r} is outside the {namespace.urn_prefix!r} "
            f"namespace owned by {namespace.project_slug!r}."
        )
    return urn


def require_all_in_namespace(
    urns: Iterable[str], namespace: Namespace, operation: str
) -> list[str]:
    """Assert that every URN is in-namespace, returning them as a list.

    Reports every violation at once rather than only the first, so an operator
    fixing a bad target list sees the full picture.

    Raises:
        NamespaceViolation: if any URN is outside the allocation.
    """
    targets = list(urns)
    violations = [u for u in targets if u in _GLOBAL_TOKENS or not is_in_namespace(u, namespace)]
    if violations:
        rendered = ", ".join(repr(v) for v in violations)
        raise NamespaceViolation(
            f"{operation!r} refused: {len(violations)} of {len(targets)} targets fall "
            f"outside the {namespace.urn_prefix!r} namespace: {rendered}"
        )
    return targets


def assert_scoped_reset(urns: Sequence[str], namespace: Namespace) -> list[str]:
    """Validate the target list for a destructive reset.

    A reset must enumerate exactly what it will remove. An empty target list is
    rejected because callers have historically treated "no targets" as "everything",
    and this instance is shared with four other submissions.

    Raises:
        NamespaceViolation: if the list is empty or contains out-of-namespace targets.
    """
    if not urns:
        raise NamespaceViolation(
            "Reset refused: no explicit targets supplied. A reset must enumerate the "
            "entities it will remove; it must never fall back to a global delete."
        )
    return require_all_in_namespace(urns, namespace, operation="reset")


def require_path_within(path: Path | str, root: Path | str, operation: str) -> Path:
    """Assert that a filesystem enforcement target lives under ``root``.

    Containment adapters quarantine exports, purge indexes, and rewrite manifests.
    Those act on real paths, so they get the same fail-closed treatment as URNs:
    a target outside the project's fixture or state root is refused. Symlinks and
    ``..`` traversal are defeated by resolving both sides before comparing.

    Raises:
        NamespaceViolation: if ``path`` does not resolve to a location under ``root``.
    """
    resolved_root = Path(root).resolve()
    resolved_path = Path(path).resolve()

    if resolved_path == resolved_root:
        raise NamespaceViolation(
            f"{operation!r} refused: target is the root {resolved_root} itself, "
            "not an artifact within it."
        )
    if not resolved_path.is_relative_to(resolved_root):
        raise NamespaceViolation(
            f"{operation!r} refused: {resolved_path} is outside the project root "
            f"{resolved_root}."
        )
    return resolved_path

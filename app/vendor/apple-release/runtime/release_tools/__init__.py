"""Reusable, credential-free release workflow primitives.

The package owns its versioned machine contracts.  Harness integrations may
import and invoke this package, but they are not a second source of workflow or
credential policy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .iterative_release import (
    CandidateIdentity,
    ReleaseStateError,
    WorkflowError,
    append_transition,
    candidate_id,
    freeze_candidate,
    hash_file,
    has_transition,
    read_candidate_ledger,
    read_ledger,
    run_candidate,
    run_gate,
    transition_once,
)


_CONTRACT_ROOT = Path(__file__).resolve().parent
_SAFE_PRODUCT_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_SECRET_NAME = re.compile(
    r"(?:secret|token|password|credential|private[_-]?key|api[_-]?key)",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """A deterministic, credential-free repository contract rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _load_contract(name: str) -> dict[str, Any]:
    try:
        value = json.loads((_CONTRACT_ROOT / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("repository-contract-unreadable") from exc
    if not isinstance(value, dict):
        raise ContractError("repository-contract-invalid")
    return value


def load_workflow_spec() -> dict[str, Any]:
    """Load the repository-owned workflow specification."""

    return _load_contract("workflow_spec.json")


def load_adapter_schema() -> dict[str, Any]:
    """Load the strict adapter JSON Schema."""

    return _load_contract("adapter.schema.json")


def _git_marker(root: Path) -> Path | None:
    current = root
    while True:
        marker = current / ".git"
        if marker.exists():
            return marker
        if current.parent == current:
            return None
        current = current.parent


def git_common_directory(repository_root: Path | str) -> Path | None:
    """Resolve Git's common directory without running Git or another process."""

    root = Path(repository_root).resolve()
    marker = _git_marker(root)
    if marker is None:
        return None
    if marker.is_dir():
        git_directory = marker.resolve()
    elif marker.is_file():
        try:
            marker_text = marker.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ContractError("git-common-directory-unreadable") from exc
        prefix = "gitdir:"
        if not marker_text.lower().startswith(prefix):
            raise ContractError("git-common-directory-invalid")
        declared = marker_text[len(prefix) :].strip()
        if not declared or "\n" in declared or "\r" in declared:
            raise ContractError("git-common-directory-invalid")
        git_path = Path(declared)
        git_directory = (
            git_path if git_path.is_absolute() else marker.parent / git_path
        ).resolve()
    else:
        raise ContractError("git-common-directory-invalid")

    common_marker = git_directory / "commondir"
    if not common_marker.exists():
        return git_directory
    try:
        declared_common = common_marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ContractError("git-common-directory-unreadable") from exc
    if not declared_common or "\n" in declared_common or "\r" in declared_common:
        raise ContractError("git-common-directory-invalid")
    common_path = Path(declared_common)
    return (
        common_path if common_path.is_absolute() else git_directory / common_path
    ).resolve()


def canonical_product_state_home(
    repository_root: Path | str,
    product_key: str,
    *,
    non_git_home: Path | str | None = None,
) -> Path:
    """Return the one product state home shared by all local worktrees.

    Git repositories use ``<absolute-common-dir>/release-state/<product-key>``.
    A non-Git project must explicitly declare a project-contained equivalent.
    This resolver is read-only and does not create the returned directory.
    """

    if not isinstance(product_key, str) or not _SAFE_PRODUCT_KEY.fullmatch(product_key):
        raise ContractError("product-key-invalid")
    root = Path(repository_root).resolve()
    common = git_common_directory(root)
    if common is not None:
        return common / "release-state" / product_key
    if non_git_home is None:
        raise ContractError("canonical-state-home-required")
    declared = Path(non_git_home)
    resolved = (declared if declared.is_absolute() else root / declared).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("canonical-state-home-outside-project") from exc
    return resolved / product_key


def _relative_contract_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def adapter_rejection_codes(document: Any) -> tuple[str, ...]:
    """Return stable security-boundary rejections for an adapter document.

    This is the dependency-free contract preflight.  The phase-2 loader may add
    richer structural diagnostics, but these codes and their precedence are the
    versioned compatibility boundary.
    """

    codes: list[str] = []

    def reject(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if not isinstance(document, Mapping):
        return ("adapter-document-invalid",)

    top_fields = {
        "schemaVersion",
        "workflow",
        "product",
        "state",
        "environmentInputs",
        "registeredConsumers",
        "operations",
        "evidencePaths",
        "localSmoke",
    }
    if set(document) - top_fields:
        reject("adapter-unknown-field")
    if document.get("schemaVersion") != "1.0.0":
        reject("adapter-schema-version-unsupported")

    spec = load_workflow_spec()
    supported_classes = spec.get("core", {}).get("operationClasses", {})
    proof_schemas = spec.get("core", {}).get("proofSchemas", {})
    operations = document.get("operations")
    if not isinstance(operations, list):
        reject("adapter-required-field-missing")
        operations = []

    registered = document.get("registeredConsumers")
    registered_names = {
        item.get("name")
        for item in registered
        if isinstance(registered, list) and isinstance(item, Mapping)
    } if isinstance(registered, list) else set()

    common_fields = {
        "id",
        "class",
        "mode",
        "proofSchema",
        "dependencies",
        "timeoutSeconds",
        "statusEvents",
    }
    credential_fields = common_fields | {"consumer", "arguments"}
    noncredential_fields = common_fields | {
        "argv",
        "workingDirectory",
        "environment",
    }
    framework_fields = common_fields

    for operation in operations:
        if not isinstance(operation, Mapping):
            reject("adapter-operation-invalid")
            continue
        mode = operation.get("mode")
        operation_class = operation.get("class")
        if operation_class not in supported_classes:
            reject("adapter-operation-class-unknown")
        proof_schema = operation.get("proofSchema")
        if not isinstance(proof_schema, str) or not proof_schema:
            reject("adapter-proof-schema-required")
        elif proof_schema not in proof_schemas:
            reject("adapter-proof-schema-unknown")
        elif isinstance(operation_class, str):
            expected = supported_classes.get(operation_class, {}).get("proofSchema")
            if expected and proof_schema != expected:
                reject("adapter-proof-schema-mismatch")

        if "command" in operation or isinstance(operation.get("argv"), str):
            reject("adapter-shell-string-forbidden")
        if "inheritEnvironment" in operation:
            reject("adapter-environment-inheritance-forbidden")
        if "secretNames" in operation:
            reject("adapter-secret-selection-forbidden")

        if mode == "credential":
            if "executable" in operation or "argv" in operation or "command" in operation:
                reject("adapter-credential-executable-forbidden")
            if set(operation) - credential_fields - {
                "command",
                "executable",
                "inheritEnvironment",
                "secretNames",
            }:
                reject("adapter-unknown-field")
            if operation.get("consumer") not in registered_names:
                reject("adapter-registered-consumer-required")
        elif mode == "nonCredential":
            if set(operation) - noncredential_fields - {
                "command",
                "inheritEnvironment",
                "secretNames",
            }:
                reject("adapter-unknown-field")
            environment = operation.get("environment")
            if not isinstance(environment, Mapping) or environment.get("inherit") is not False:
                reject("adapter-environment-inheritance-forbidden")
            argv = operation.get("argv")
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item and "\x00" not in item and "\n" not in item
                for item in argv
            ):
                reject("adapter-argv-invalid")
        elif mode == "framework":
            if set(operation) - framework_fields:
                reject("adapter-unknown-field")
        else:
            reject("adapter-operation-mode-unknown")

        for path_field in ("workingDirectory",):
            if path_field in operation and not _relative_contract_path(operation[path_field]):
                reject("adapter-path-traversal")

    evidence_paths = document.get("evidencePaths", [])
    if isinstance(evidence_paths, list):
        for evidence in evidence_paths:
            if isinstance(evidence, Mapping) and not _relative_contract_path(evidence.get("path")):
                reject("adapter-path-traversal")
    state = document.get("state")
    if isinstance(state, Mapping) and "nonGitCanonicalHome" in state:
        if not _relative_contract_path(state["nonGitCanonicalHome"]):
            reject("adapter-path-traversal")

    environment_inputs = document.get("environmentInputs", [])
    if isinstance(environment_inputs, list):
        for value in environment_inputs:
            if isinstance(value, Mapping):
                if "default" in value:
                    reject("adapter-environment-default-forbidden")
                if _SECRET_NAME.search(str(value.get("name", ""))):
                    reject("adapter-secret-selection-forbidden")

    if document.get("localSmoke") is True and not any(
        item.get("class") == "localSmoke"
        for item in operations
        if isinstance(item, Mapping)
    ):
        reject("adapter-local-smoke-operation-required")

    return tuple(codes)


_WRAPPER_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "harness-transition-policy-forbidden",
        re.compile(r"(?:transition[_-]?table|state[_-]?transitions|allowed[_-]?transitions)", re.I),
    ),
    (
        "harness-app-identity-forbidden",
        re.compile(r"(?:bundle[_-]?identifier|bundle[_-]?id|team[_-]?identifier|product[_-]?key\s*[:=])", re.I),
    ),
    (
        "harness-provider-route-forbidden",
        re.compile(r"(?:provider[_-]?route|model[_-]?route|route[_-]?provider|switchyard[_-]?route)", re.I),
    ),
    (
        "harness-credential-policy-forbidden",
        re.compile(r"(?:bws-secret-exec|secret[_-]?mapping|credential[_-]?policy|consumer[_-]?mapping)", re.I),
    ),
)


def harness_wrapper_rejection_codes(source: str) -> tuple[str, ...]:
    """Reject repository policy duplicated into a harness wrapper."""

    if not isinstance(source, str):
        return ("harness-wrapper-invalid",)
    return tuple(code for code, pattern in _WRAPPER_RULES if pattern.search(source))

__all__ = [
    "ContractError",
    "CandidateIdentity",
    "ReleaseStateError",
    "WorkflowError",
    "adapter_rejection_codes",
    "append_transition",
    "candidate_id",
    "canonical_product_state_home",
    "freeze_candidate",
    "hash_file",
    "harness_wrapper_rejection_codes",
    "has_transition",
    "read_candidate_ledger",
    "read_ledger",
    "git_common_directory",
    "load_adapter_schema",
    "load_workflow_spec",
    "run_candidate",
    "run_gate",
    "transition_once",
]

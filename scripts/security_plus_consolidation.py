#!/usr/bin/env python3
"""Stage the local Security+ final-review consolidation.

This command deliberately stops at the discovery-excluded staging boundary.
It snapshots the active source packs, records content hashes, selects a
deterministic 136-question legacy subset, adds 24 original remediation cards,
and writes a complete selection ledger.  It never moves or edits an installed
pack and it never invokes an external reviewer.

Usage::

    python3 scripts/security_plus_consolidation.py stage
    python3 scripts/security_plus_consolidation.py validate
    python3 scripts/security_plus_consolidation.py paths

The generated directory is ignored by the repository's question-pack rules::

    question-packs/_staging/security-plus-final-review-2026-08-04/

The staged candidate is intentionally uncertified.  Layer-C certification and
the independent INV-8 review are separate, authorized steps.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"
ACTIVE_SECURITY_DIR = PACKS_DIR / "sy0-701"
ACTIVE_ITN_DIR = PACKS_DIR / "itn260"
STAGING_ROOT = PACKS_DIR / "_staging" / "security-plus-final-review-2026-08-04"
CANDIDATE_PATH = STAGING_ROOT / "sy0-701-final-review.json"
LEDGER_PATH = STAGING_ROOT / "selection-ledger.json"
INVENTORY_PATH = STAGING_ROOT / "source-inventory.json"
PATHS_PATH = STAGING_ROOT / "cutover-paths.json"
BUILD_NOTES_PATH = STAGING_ROOT / "BUILD_NOTES.md"
SNAPSHOT_ROOT = STAGING_ROOT / "source-snapshot"

ARCHIVE_SECURITY_REL = "question-packs/_archive/sy0-701-objective-packs-2026-08-04"
ARCHIVE_ITN_REL = "question-packs/_archive/itn260-final-review-2026-08-04"
DATE_STAMP = "2026-08-04"

OBJECTIVE_RE = re.compile(r"^ch(?P<chapter>\d{2})-obj(?P<objective>[0-9.]+)-.*\.json$")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")

LEGACY_TARGET = 136
REMEDIATION_TARGET = 24
FINAL_TARGET = LEGACY_TARGET + REMEDIATION_TARGET
OBJECTIVE_FLOOR = 4
DOMAIN_EXTRA_QUOTA = {"1": 3, "2": 6, "3": 4, "4": 7, "5": 4}
TYPE_TARGETS = {
    "multiple_choice": 96,
    "scenario_multiple_choice": 24,
    "matching": 19,
    "true_false": 13,
    "multiple_select": 8,
}

# These are study-log themes, not copied question text.  They make the legacy
# selection deterministic and bias the 24 discretionary slots toward the
# concepts David explicitly logged as missed locally.
WEAK_POINT_TERMS = {
    "allow list", "allow-list", "acl", "application allow", "vishing",
    "typosquat", "spoof", "business email", "whaling", "credential stuffing",
    "session hijack", "session token", "host firewall", "container", "microservice",
    "supply chain", "msp", "password rotation", "ultrasonic", "active defense",
    "generator", "web filtering", "blocklist", "certificate", "pcap", "packet capture",
    "devsecops", "automation", "orchestration", "internal audit", "stored data",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(_canonical_json(value) + b"\n")
    temp.replace(path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object at {path}")
    return value


def relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def discover_security_sources() -> list[tuple[Path, str]]:
    sources: list[tuple[Path, str]] = []
    for path in sorted(ACTIVE_SECURITY_DIR.glob("ch*.json")):
        match = OBJECTIVE_RE.match(path.name)
        if not match:
            raise ValueError(f"unexpected Security+ pack filename: {path.name}")
        sources.append((path, match.group("objective")))
    if len(sources) != 28:
        raise ValueError(f"expected 28 active Security+ objective packs, found {len(sources)}")
    objectives = [objective for _, objective in sources]
    if objectives != sorted(objectives, key=lambda value: tuple(int(part) for part in value.split("."))):
        raise ValueError("Security+ source packs are not in objective order")
    if len(set(objectives)) != 28:
        raise ValueError("Security+ objective roster contains duplicates")
    return sources


def discover_itn_sources() -> list[Path]:
    paths = sorted(ACTIVE_ITN_DIR.glob("*.json"))
    paths = [path for path in paths if path.name != "_course.json"]
    if len(paths) != 1:
        raise ValueError(f"expected one active ITN260 pack, found {len(paths)}")
    return paths


def _question_count(path: Path) -> int:
    data = read_json(path)
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"{path} has no question list")
    return len(questions)


def _inventory_file(path: Path, *, course: str, objective: str | None = None) -> dict:
    data = {
        "path": relative(path),
        "course": course,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if objective is not None:
        data["objective"] = objective
        data["question_count"] = _question_count(path)
    return data


def build_inventory() -> dict:
    security_sources = discover_security_sources()
    itn_sources = discover_itn_sources()
    files: list[dict] = []
    for path, objective in security_sources:
        files.append(_inventory_file(path, course="sy0-701", objective=objective))
    for path in itn_sources:
        files.append(_inventory_file(path, course="itn260"))
    # Metadata is part of the recoverable source inventory too.  It is not a
    # question pack, so it remains outside the question-count reconciliation.
    for path, course in ((ACTIVE_SECURITY_DIR / "_course.json", "sy0-701"),
                         (ACTIVE_ITN_DIR / "_course.json", "itn260")):
        files.append(_inventory_file(path, course=course))
    for path in (ACTIVE_SECURITY_DIR / "BUILD_NOTES.md", ACTIVE_SECURITY_DIR / "SPOTCHECK_DIGEST.md"):
        if path.exists():
            files.append(_inventory_file(path, course="sy0-701"))
    files.sort(key=lambda entry: entry["path"])
    security_questions = sum(entry.get("question_count", 0) for entry in files if entry["course"] == "sy0-701")
    itn_questions = sum(_question_count(path) for path in itn_sources)
    content_hash = sha256_bytes(_canonical_json(files))
    return {
        "schema_version": 1,
        "generated_at": DATE_STAMP,
        "source_snapshot_hash": content_hash,
        "backup_verification": "pending-human-verification",
        "files": files,
        "course_counts": {
            "sy0-701": {"pack_count": 28, "question_count": security_questions},
            "itn260": {"pack_count": 1, "question_count": itn_questions},
        },
        "staging_root": relative(STAGING_ROOT),
        "snapshot_root": relative(SNAPSHOT_ROOT),
        "archive_destinations": {
            "sy0-701": ARCHIVE_SECURITY_REL,
            "itn260": ARCHIVE_ITN_REL,
        },
    }


def _copy_checked(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or sha256_file(source) != sha256_file(destination):
            raise RuntimeError(f"snapshot collision: {destination}")
        return
    shutil.copy2(source, destination)


def snapshot_sources(inventory: dict) -> None:
    for entry in inventory["files"]:
        source = PROJECT_ROOT / entry["path"]
        if source.is_relative_to(ACTIVE_SECURITY_DIR):
            destination = SNAPSHOT_ROOT / "sy0-701" / source.relative_to(ACTIVE_SECURITY_DIR)
        elif source.is_relative_to(ACTIVE_ITN_DIR):
            destination = SNAPSHOT_ROOT / "itn260" / source.relative_to(ACTIVE_ITN_DIR)
        else:
            raise ValueError(f"source is outside active courses: {source}")
        _copy_checked(source, destination)


def _tokens(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if len(token) > 2}


def stem_similarity(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def topic_conflict(left: str | None, right: str | None) -> bool:
    """Mirror the L23 near-duplicate topic-slug smell for curation."""
    if not left or not right or left == right:
        return False
    left_tokens = set(str(left).split("-"))
    right_tokens = set(str(right).split("-"))
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    left_slug = str(left)
    right_slug = str(right)
    if left_slug.startswith(right_slug + "-") or right_slug.startswith(left_slug + "-"):
        return True
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.60


def _question_text(question: dict) -> str:
    pieces = [str(question.get("prompt", "")), str(question.get("explanation", ""))]
    for key in ("options", "leftItems", "rightItems", "tags", "topic"):
        value = question.get(key)
        if isinstance(value, list):
            pieces.extend(str(item) for item in value)
        elif value:
            pieces.append(str(value))
    return " ".join(pieces).lower()


def legacy_score(question: dict) -> int:
    text = _question_text(question)
    score = {
        "scenario_multiple_choice": 12,
        "multiple_select": 9,
        "matching": 6,
        "true_false": 4,
        "multiple_choice": 5,
    }.get(question.get("type"), 0)
    score += {"hard": 5, "medium": 3, "easy": 1}.get(question.get("difficulty"), 0)
    if len(str(question.get("prompt", "")).split()) >= 24:
        score += 3
    for term in WEAK_POINT_TERMS:
        if term in text:
            score += 5
    return score


def _candidate_sort_key(entry: tuple[dict, Path, str]) -> tuple[int, str, str]:
    question, path, source_id = entry
    return (-legacy_score(question), relative(path), source_id)


def _safe_against_selected(question: dict, selected: list[dict]) -> bool:
    prompt = str(question.get("prompt", ""))
    for other in selected:
        if topic_conflict(question.get("topic"), other.get("topic")):
            return False
        if stem_similarity(prompt, str(other.get("prompt", ""))) >= 0.70:
            return False
    return True


def _choose_from_pool(pool: list[tuple[dict, Path, str]], selected: list[dict], count: int,
                     preferred_types: Iterable[str] = ()) -> list[tuple[dict, Path, str]]:
    chosen: list[tuple[dict, Path, str]] = []
    used_types: set[str] = set()
    ordered = sorted(pool, key=_candidate_sort_key)
    for preferred in preferred_types:
        for entry in ordered:
            question = entry[0]
            if entry in chosen or question.get("type") != preferred:
                continue
            if not _safe_against_selected(question, selected + [item[0] for item in chosen]):
                continue
            chosen.append(entry)
            used_types.add(preferred)
            break
        if len(chosen) >= count:
            return chosen[:count]
    for entry in ordered:
        if entry in chosen:
            continue
        if not _safe_against_selected(entry[0], selected + [item[0] for item in chosen]):
            continue
        chosen.append(entry)
        if len(chosen) >= count:
            break
    if len(chosen) < count:
        # A source objective can contain legitimate related stems.  Do not
        # silently reduce the objective floor; the Layer-A merge check will
        # surface any remaining issue for a human curation decision.
        for entry in ordered:
            if entry not in chosen:
                chosen.append(entry)
                if len(chosen) >= count:
                    break
    return chosen[:count]


def load_legacy_questions() -> tuple[list[tuple[dict, Path, str]], list[dict]]:
    all_entries: list[tuple[dict, Path, str]] = []
    roster: list[dict] = []
    for path, objective in discover_security_sources():
        data = read_json(path)
        questions = data.get("questions")
        if not isinstance(questions, list):
            raise ValueError(f"{path} questions is not a list")
        roster.append({
            "objective": objective,
            "source_pack": relative(path),
            "source_question_count": len(questions),
            "floor": OBJECTIVE_FLOOR,
        })
        for question in questions:
            if not isinstance(question, dict) or not isinstance(question.get("id"), str):
                raise ValueError(f"invalid question in {path}")
            all_entries.append((copy.deepcopy(question), path, question["id"]))
    if len(all_entries) != 454:
        raise ValueError(f"expected 454 active Security+ questions, found {len(all_entries)}")
    return all_entries, roster


def _mc(prompt: str, options: list[str], answer: int, explanation: str, *,
        topic: str, difficulty: str = "medium", scenario: bool = False,
        tags: list[str] | None = None) -> dict:
    return {
        "id": "",
        "type": "scenario_multiple_choice" if scenario else "multiple_choice",
        "topic": topic,
        "difficulty": difficulty,
        "prompt": prompt,
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "tags": ["local-remediation", *(tags or [])],
    }


def remediation_cards() -> list[dict]:
    """Return original cards paraphrased from the local missed-question logs."""
    cards = [
        _mc(
            "A web site's identity warning appears immediately after renewal. The host name is correct, but the validation path still fails. What should be checked first?",
            [
                "Check the client trust store and the server's intermediate certificate chain",
                "Switch the site to unencrypted HTTP until the certificate warning disappears",
                "Turn off certificate validation in every client configuration and suppress warnings",
                "Generate a longer key without examining the certificate chain or trust settings",
            ], 0,
            "The trusted CA and intermediate chain determine whether clients can build a valid path. Plain HTTP removes protection rather than repairing trust. Disabling validation hides the defect and permits impersonation. Key length may affect strength but does not repair a missing or untrusted chain.",
            topic="certificate-troubleshooting", difficulty="hard", scenario=True, tags=["study-log-secondary-01"],
        ),
        _mc(
            "A review finds sensitive values in a readable local file. Which remediation most directly reduces the exposure?",
            [
                "Use a protected secret store and remove plaintext storage",
                "Increase the display resolution on the workstation used by the employee",
                "Move the readable file to another user directory without changing permissions",
                "Permit reuse of one credential across additional services to simplify support",
            ], 0,
            "A protected credential store and removal of plaintext storage address the exposure directly. Monitor resolution has no credential-protection effect. Moving a readable file leaves the secret exposed. Reusing a password increases the consequences of disclosure.",
            topic="credential-remediation", difficulty="medium", tags=["study-log-secondary-02"],
        ),
        _mc(
            "A team wants independent deployment and horizontal scaling for functions that currently share one codebase. Which architectural change best fits?",
            [
                "Split the monolith into independently deployable services with separate scaling boundaries",
                "Place the unchanged monolith on a larger virtual machine with no service or scaling boundaries",
                "Add a perimeter firewall while leaving deployment and scaling design unchanged",
                "Store each function in one shared spreadsheet without separate deployment or scaling",
            ], 0,
            "Independent microservices separate business functions so they can be deployed and scaled independently. A larger virtual machine preserves the monolith. A perimeter firewall controls traffic rather than application decomposition. A shared spreadsheet does not provide service boundaries or scalable deployment.",
            topic="microservices", difficulty="medium", tags=["study-log-secondary-03"],
        ),
        _mc(
            "During an incident, investigators need the contents and sequence of communications, not only host summaries. Which source is most useful?",
            [
                "A packet capture of the exchanged traffic during the event",
                "A host log summarizing network connections and process events",
                "An approved-software list for the affected host",
                "A baseline recorded before the incident began",
            ], 0,
            "A packet capture preserves observed packets and can show communication contents and sequence. The host log summarizing network connections and process events is a summary, not the exchanged contents. The approved-software list describes authorization, not traffic. The pre-incident baseline supports comparison but does not contain the communications themselves.",
            topic="packet-capture", difficulty="medium", scenario=True, tags=["study-log-secondary-04"],
        ),
        _mc(
            "A company laptop contains sensitive files and may be stolen while traveling. Which control best protects the stored data if the device is lost?",
            [
                "Full-disk encryption enforced with a protected recovery process",
                "Transport encryption used only while a web session is active",
                "A hash of each file without access-control enforcement",
                "A faster wireless access point in the office",
            ], 0,
            "Full-disk encryption protects data at rest when the storage device is lost. Transport encryption protects data in transit during a session. Hashing detects changes but does not make file contents confidential. A faster access point does not protect a stolen laptop.",
            topic="data-at-rest", difficulty="medium", tags=["study-log-secondary-05"],
        ),
        _mc(
            "Which practice places automated tests in a continuous-integration pipeline so defects can be found before deployment?",
            [
                "Run static application security testing as part of the CI checks",
                "Wait for a yearly audit after all releases are complete",
                "Disable testing so the pipeline remains fast",
                "Rely only on a backup after a vulnerable release is deployed",
            ], 0,
            "Static application security testing in CI examines code before deployment and gives developers an early feedback loop. A yearly audit is later and less continuous. The option to disable testing so the pipeline remains fast leaves defects undetected. A backup helps recovery but does not find the defect before release.",
            topic="devsecops-ci", difficulty="medium", tags=["study-log-secondary-06"],
        ),
        _mc(
            "An attacker uses a username and password from one breach against many unrelated accounts. Which attack is being attempted?",
            [
                "Credential stuffing",
                "Password spraying",
                "A single-account brute-force attack",
                "A voice-phishing call",
            ], 0,
            "Credential stuffing reuses previously exposed username-and-password pairs across services. Password spraying tries one or a few common passwords against many accounts. A single-account brute-force attack repeatedly guesses one account's password. A voice-phishing call is a social-engineering channel, not this automated reuse pattern.",
            topic="credential-stuffing", difficulty="medium", scenario=True, tags=["study-log-secondary-07"],
        ),
        _mc(
            "An organization's managed service provider is compromised, and the attacker uses its remote-management channel to reach customers. Which risk is most directly illustrated?",
            [
                "Third-party supply-chain compromise",
                "A natural disaster affecting a data center",
                "A local password typo by one customer",
                "A capacity-planning calculation error",
            ], 0,
            "A compromised provider used as a path into customers is a third-party and supply-chain compromise. A natural disaster is an environmental event, not a provider-mediated intrusion. A password typo is an individual user error. Capacity planning concerns resources and performance rather than this trust relationship.",
            topic="msp", difficulty="hard", scenario=True, tags=["study-log-secondary-08"],
        ),
        _mc(
            "An attacker steals a valid browser token and uses it to access an account without knowing the password. What attack is this?",
            [
                "Session hijacking",
                "Password spraying",
                "Directory traversal",
                "A denial-of-service attack",
            ], 0,
            "Using a stolen valid session token is session hijacking. Password spraying guesses one common password across accounts. Directory traversal manipulates file paths to reach unauthorized resources. A denial-of-service attack exhausts availability rather than taking over an authenticated session.",
            topic="session-hijacking", difficulty="medium", scenario=True, tags=["study-log-secondary-09"],
        ),
        _mc(
            "Which control filters traffic entering or leaving an individual workstation, even when that workstation is outside the organization's network perimeter?",
            [
                "A host-based firewall",
                "A perimeter network firewall only",
                "A web application firewall protecting a server",
                "A passive network intrusion detection sensor",
            ], 0,
            "A host-based firewall enforces traffic rules on the individual workstation. A perimeter firewall only protects traffic that crosses that perimeter. A web application firewall focuses on application-layer web traffic to a server. A passive intrusion detection sensor observes and alerts rather than enforcing the host's traffic policy.",
            topic="host-firewall", difficulty="medium", tags=["study-log-secondary-10"],
        ),
        _mc(
            "Which document states how organizational systems may be used and what happens after a violation?",
            [
                "Acceptable use policy",
                "Business continuity plan",
                "Incident response plan",
                "Data-retention schedule",
            ], 0,
            "An acceptable use policy defines allowed and prohibited system use and the consequences for violations. A business continuity plan addresses continued operations during disruption. An incident response plan addresses handling security events. A data-retention schedule defines how long records are kept.",
            topic="acceptable-use-policy", difficulty="easy", tags=["study-log-secondary-11"],
        ),
        _mc(
            "Which statement accurately distinguishes containers from traditional virtual machines?",
            [
                "Containers share the host kernel; virtual machines normally include separate guest operating systems",
                "Containers require a full guest operating system for each application, while virtual machines do not",
                "Virtual machines provide no isolation, while containers provide complete hardware separation",
                "Containers are physical servers, while virtual machines are only network cables",
            ], 0,
            "Containers generally share the host kernel, while virtual machines use a hypervisor and normally include separate guest operating systems. Containers therefore do not always require a full guest OS. Virtual machines are designed to provide isolation. Containers are software-isolated workloads, not physical servers.",
            topic="containers-vs-virtualization", difficulty="medium", tags=["study-log-secondary-12"],
        ),
        _mc(
            "When a response playbook runs on a noisy alert source, what is a significant security risk?",
            [
                "A false positive can trigger an overly broad action and disrupt legitimate activity",
                "Every alert is guaranteed to be a true positive and needs no review",
                "A playbook removes testing and change control from the deployment process",
                "Automation makes recovery impossible because it prevents all log retention",
            ], 0,
            "A false positive can cause a playbook to isolate healthy systems or block legitimate activity, so testing and bounded actions matter. Automation does not guarantee alert accuracy. Playbooks still need testing and change control. Automation does not inherently prevent log retention or recovery.",
            topic="automation-risk", difficulty="hard", scenario=True, tags=["study-log-secondary-13"],
        ),
        _mc(
            "An administrator finds a switch port in a public area that is not needed for current operations. Which action most directly reduces unauthorized connectivity?",
            [
                "Disable the switch port until an approved device needs it",
                "Keep the switch port active and disable DNS for internal hosts",
                "Keep the switch port active and replace the certificate authority",
                "Keep the switch port active and increase the Ethernet frame size",
            ], 0,
            "Disabling the unused switch port removes the available connection point. Disabling DNS does not close the physical port. Replacing a certificate authority addresses trust infrastructure rather than switch access. Increasing frame size does not prevent an unauthorized device from connecting.",
            topic="physical-network-port", difficulty="easy", scenario=True, tags=["study-log-secondary-14"],
        ),
        _mc(
            "A policy team proposes a 30-day credential-change interval despite no evidence of an incident. Which recommendation is strongest?",
            [
                "Use long unique credentials, MFA, and changes only after a suspected breach",
                "Rotate to short predictable credentials every week to satisfy the interval",
                "Reuse one long credential across services to avoid frequent changes",
                "Remove MFA so credential changes are the only authentication control",
            ], 0,
            "Strong unique passwords, MFA, and event-driven changes address practical risk better than arbitrary frequent rotation. Short predictable rotations weaken passwords. Reusing one password creates cross-service exposure. The option to remove MFA so credential changes are the only authentication control removes a useful independent factor.",
            topic="password-rotation", difficulty="medium", scenario=True, tags=["study-log-secondary-15"],
        ),
        _mc(
            "Which physical security sensor detects motion by transmitting and receiving high-frequency sound waves?",
            [
                "An ultrasonic sensor",
                "A magnetic door contact",
                "An infrared beam sensor",
                "A badge reader",
            ], 0,
            "An ultrasonic sensor uses high-frequency sound waves to detect motion or distance. A magnetic door contact detects the state of a door. An infrared beam sensor uses infrared light. A badge reader authenticates a credential rather than measuring motion with sound.",
            topic="ultrasonic-sensors", difficulty="easy", tags=["study-log-secondary-16"],
        ),
        _mc(
            "Indicators show that an attacker is using one workstation. Which activity changes the defensive posture?",
            [
                "Isolate the device and block malicious infrastructure while preserving evidence",
                "Only record the event and take no response action during investigation",
                "Publish the asset inventory so outside parties can identify the system",
                "Disable all backups before investigating the alert",
            ], 0,
            "Isolating the endpoint and blocking malicious infrastructure changes the defensive posture in response to the attack, which is active defense. Only recording an event is passive observation. Publishing an asset inventory increases exposure. Disabling backups destroys a recovery control and is not a defensive response.",
            topic="active-defense", difficulty="hard", scenario=True, tags=["study-log-secondary-17"],
        ),
        _mc(
            "Which reviewer provides assurance from outside the organization and is independent of the team that operates the audited system?",
            [
                "An external auditor",
                "The system owner",
                "The system administrator",
                "The internal service desk",
            ], 0,
            "An external auditor provides assurance from outside the organization and is independent of the operating team. The system owner is accountable for the asset. The system administrator operates the system. The internal service desk supports users; none of those roles is the external independent reviewer described.",
            topic="external-assurance", difficulty="easy", tags=["study-log-secondary-18"],
        ),
        _mc(
            "A facility must keep critical systems powered during a utility outage that may last several days. Which control is most appropriate?",
            [
                "An onsite generator with tested fuel and maintenance procedures for sustained operation",
                "A small UPS intended only to bridge a short transfer between power sources",
                "A redundant disk array that protects against a failed drive in the server",
                "A password vault replicated to another region for recovery after an outage",
            ], 0,
            "An onsite generator with fuel and maintenance planning can provide sustained facility power. A small UPS normally bridges a short interruption rather than several days. A disk array protects data availability from disk failure, not utility loss. A replicated password vault does not supply electrical power.",
            topic="onsite-generators", difficulty="medium", scenario=True, tags=["study-log-secondary-19"],
        ),
        _mc(
            "Which control prevents users from visiting domains that an organization has identified as malicious or prohibited?",
            [
                "A web filter enforcing a domain blocklist",
                "Full-disk encryption on a workstation",
                "A network access control rule checking device posture only",
                "A data-loss-prevention rule scanning outgoing documents only",
            ], 0,
            "A web filter enforcing a domain blocklist controls access to identified malicious or prohibited destinations. Full-disk encryption protects stored data. Network access control checks whether a device may join a network. Data-loss prevention scanning outgoing documents addresses data movement, not domain visits.",
            topic="web-filtering", difficulty="easy", tags=["study-log-secondary-20"],
        ),
        _mc(
            "An attacker registers a domain that differs from a trusted domain by a common spelling error and uses it to imitate the real site. What technique is this?",
            [
                "Typosquatting",
                "Email spoofing",
                "Credential stuffing",
                "Tailgating",
            ], 0,
            "Typosquatting registers a look-alike domain that exploits a typing error. Email spoofing forges message sender information but does not require registering the look-alike domain described. Credential stuffing reuses exposed credentials. Tailgating is physical entry by following an authorized person.",
            topic="typosquatting", difficulty="medium", scenario=True, tags=["study-log-amplifire-01"],
        ),
        _mc(
            "A caller impersonates a bank employee and pressures a victim to disclose a one-time code over the phone. What social-engineering technique is this?",
            [
                "Vishing",
                "Pharming",
                "Smishing",
                "Dumpster diving",
            ], 0,
            "Vishing is voice-based phishing over a phone call. Pharming redirects a victim through manipulated name resolution or routing. Smishing uses text messages. Dumpster diving searches discarded materials for information.",
            topic="vishing", difficulty="easy", scenario=True, tags=["study-log-amplifire-03"],
        ),
        _mc(
            "An attacker compromises a finance executive's mailbox and sends an urgent payment request to an employee. Which label best describes the fraud campaign?",
            [
                "Business email compromise",
                "Voice phishing by a telephone caller",
                "Pharming through manipulated name resolution",
                "Tailgating through unauthorized physical entry",
            ], 0,
            "Business email compromise uses a trusted business mailbox or identity to deceive staff into transferring money or data. Voice phishing uses a phone call. Pharming redirects traffic through manipulated resolution. Tailgating is unauthorized physical entry behind an authorized person; none describes this mailbox-based fraud.",
            topic="business-email-compromise", difficulty="hard", scenario=True, tags=["study-log-amplifire-05"],
        ),
        _mc(
            "An endpoint should execute only software that the organization has explicitly approved. Which control is designed for that requirement?",
            [
                "An application allow list",
                "A network access-control list that filters packet flows",
                "A full backup schedule",
                "A vulnerability scanner that reports missing patches",
            ], 0,
            "An application allow list permits execution only for approved software. A network access-control list filters traffic rather than deciding which local applications may run. A backup schedule supports recovery. A vulnerability scanner identifies weaknesses but does not enforce an execution allow list.",
            topic="application-allow-listing", difficulty="medium", scenario=True, tags=["study-log-amplifire-06"],
        ),
    ]
    if len(cards) != REMEDIATION_TARGET:
        raise AssertionError(f"authoring error: expected {REMEDIATION_TARGET} remediation cards, found {len(cards)}")
    return cards


def _rebalance_mc_answer(question: dict, target_index: int) -> dict:
    """Move the keyed option to a deterministic slot without changing content."""
    if question.get("type") not in {"multiple_choice", "scenario_multiple_choice"}:
        return question
    options = question.get("options")
    answer = question.get("answer")
    if not isinstance(options, list) or not isinstance(answer, int) or not (0 <= answer < len(options)):
        return question
    target_index %= len(options)
    if answer == target_index:
        return question
    keyed = options[answer]
    distractors = [option for index, option in enumerate(options) if index != answer]
    question["options"] = distractors[:target_index] + [keyed] + distractors[target_index:]
    question["answer"] = target_index
    return question


def select_legacy_entries(entries: list[tuple[dict, Path, str]], roster: list[dict]) -> list[tuple[dict, Path, str]]:
    by_objective: dict[str, list[tuple[dict, Path, str]]] = defaultdict(list)
    for entry in entries:
        path = entry[1]
        match = OBJECTIVE_RE.match(path.name)
        assert match is not None
        by_objective[match.group("objective")].append(entry)

    selected_entries: list[tuple[dict, Path, str]] = []
    preferred_types = ("scenario_multiple_choice", "multiple_select", "matching", "true_false", "multiple_choice")
    for row in roster:
        objective = row["objective"]
        chosen = _choose_from_pool(by_objective[objective], [item[0] for item in selected_entries], OBJECTIVE_FLOOR,
                                   preferred_types)
        if len(chosen) != OBJECTIVE_FLOOR:
            raise ValueError(f"objective {objective} has fewer than {OBJECTIVE_FLOOR} selectable questions")
        selected_entries.extend(chosen)

    remaining_by_domain: dict[str, list[tuple[dict, Path, str]]] = defaultdict(list)
    selected_ids = {source_id for _, _, source_id in selected_entries}
    for question, path, source_id in entries:
        if source_id in selected_ids:
            continue
        objective = OBJECTIVE_RE.match(path.name).group("objective")  # type: ignore[union-attr]
        remaining_by_domain[objective.split(".", 1)[0]].append((question, path, source_id))

    current_types = Counter(question.get("type") for question, _, _ in selected_entries)
    false_tf_extras = 0
    false_tf_extra_target = 5
    for domain, quota in DOMAIN_EXTRA_QUOTA.items():
        pool = remaining_by_domain[domain]
        for _ in range(quota):
            if not pool:
                raise ValueError(f"domain {domain} has no remaining questions for its extra quota")
            balance_candidates = [
                entry for entry in pool
                if entry[0].get("type") == "true_false" and entry[0].get("answer") is False
            ]
            if false_tf_extras < false_tf_extra_target and balance_candidates:
                ordered = sorted(balance_candidates, key=_candidate_sort_key)
            else:
                target_types = [kind for kind, target in TYPE_TARGETS.items() if current_types[kind] < target]
                ordered = sorted(pool, key=lambda entry: (
                    0 if entry[0].get("type") in target_types else 1,
                    _candidate_sort_key(entry),
                ))
            chosen = next((entry for entry in ordered if _safe_against_selected(entry[0], [item[0] for item in selected_entries])), None)
            if chosen is None:
                chosen = ordered[0]
            pool.remove(chosen)
            selected_entries.append(chosen)
            current_types[chosen[0].get("type")] += 1
            if chosen[0].get("type") == "true_false" and chosen[0].get("answer") is False:
                false_tf_extras += 1

    if len(selected_entries) != LEGACY_TARGET:
        raise AssertionError(f"legacy selection produced {len(selected_entries)} questions")
    return selected_entries


def _rebalance_answer_positions(questions: list[dict]) -> list[dict]:
    """Spread keyed MC positions within each option-count group."""
    group_counts: Counter[int] = Counter()
    for question in questions:
        if question.get("type") not in {"multiple_choice", "scenario_multiple_choice"}:
            continue
        options = question.get("options") or []
        if not options:
            continue
        target = group_counts[len(options)] % len(options)
        _rebalance_mc_answer(question, target)
        group_counts[len(options)] += 1
    return questions


def select_legacy_questions(entries: list[tuple[dict, Path, str]], roster: list[dict]) -> list[dict]:
    selected_entries = select_legacy_entries(entries, roster)

    selected: list[dict] = []
    for index, (question, path, source_id) in enumerate(selected_entries, start=1):
        question = copy.deepcopy(question)
        question["id"] = f"f1q{index:03d}"
        tags = list(question.get("tags") or [])
        if "consolidated-legacy" not in tags:
            tags.append("consolidated-legacy")
        question["tags"] = tags
        selected.append(question)
    return _rebalance_answer_positions(selected)


def make_candidate(legacy_questions: list[dict]) -> tuple[dict, list[dict]]:
    cards = remediation_cards()
    questions = list(legacy_questions)
    for index, card in enumerate(cards, start=1):
        card = copy.deepcopy(card)
        card["id"] = f"f1q{LEGACY_TARGET + index:03d}"
        questions.append(card)
    if len(questions) != FINAL_TARGET:
        raise AssertionError(f"candidate produced {len(questions)} questions")
    _rebalance_answer_positions(questions)
    blueprint = [
        {"topic": topic, "min": 1}
        for topic in sorted({str(question.get("topic", "")).strip() for question in questions if question.get("topic")})
    ]
    candidate = {
        "pack_id": "sy0-701-final-review",
        "subject": "CompTIA Security+ SY0-701",
        "title": "Security+ SY0-701 Final Review",
        "version": 1,
        "generated_at": DATE_STAMP,
        "generation_mode": "local-deterministic-consolidation",
        "notes": "160-question focused final review; 136 curated legacy + 24 local remediation cards.",
        "source_directive": (
            "Grade against the CompTIA Security+ SY0-701 objectives and standard security practice. "
            "Check the keyed answer, explanation, distractor distinctions, and objective alignment. "
            "Questions are original or locally selected study material; do not infer correctness "
            "from option position or from a source title alone."
        ),
        "coverage_blueprint": blueprint,
        "questions": questions,
    }
    return candidate, cards


def build_ledger(inventory: dict, candidate: dict, legacy_entries: list[tuple[dict, Path, str]],
                 roster: list[dict], selected_legacy: list[dict], remediation: list[dict]) -> dict:
    # Reuse the exact selector used to build the candidate.  Keeping one
    # selector prevents the ledger from silently drifting from final IDs.
    selected_entries = select_legacy_entries(legacy_entries, roster)
    if len(selected_entries) != len(selected_legacy):
        raise AssertionError("ledger selector diverged from candidate selector")

    selected_source_ids = {source_id for _, _, source_id in selected_entries}
    selected_by_source = {source_id: (index, question, path) for index, (question, path, source_id) in enumerate(selected_entries, start=1)}
    entries: list[dict] = []
    for question, path, source_id in legacy_entries:
        objective = OBJECTIVE_RE.match(path.name).group("objective")  # type: ignore[union-attr]
        selected = source_id in selected_source_ids
        index, _, _ = selected_by_source[source_id] if selected else (None, None, None)
        entries.append({
            "origin": "legacy",
            "status": "selected" if selected else "not_selected",
            "source_path": relative(path),
            "source_sha256": sha256_file(path),
            "source_id": source_id,
            "final_id": f"f1q{index:03d}" if selected else None,
            "objective": objective,
            "topic": question.get("topic"),
            "question_type": question.get("type"),
            "rationale": (
                "objective-floor survivor" if selected and index <= 112 else
                "discretionary high-yield survivor" if selected else
                "not selected after objective floor, type diversity, weak-point weighting, and merge deduplication"
            ),
            "review_disposition": "pending-content-qa" if selected else "not_reviewed",
        })
    remediation_start = LEGACY_TARGET + 1
    for index, card in enumerate(remediation, start=remediation_start):
        tags = card.get("tags") or []
        source_tag = next((tag for tag in tags if str(tag).startswith("study-log-")), "local-study-log")
        entries.append({
            "origin": "remediation",
            "status": "selected",
            "source_path": "local Security+ study logs (see card tag)",
            "source_sha256": None,
            "source_id": f"remediation-{index - LEGACY_TARGET:02d}",
            "final_id": f"f1q{index:03d}",
            "objective": next((row["objective"] for row in REMEDIATION_MAP if row["card"] == index - LEGACY_TARGET), None),
            "topic": card.get("topic"),
            "question_type": card.get("type"),
            "rationale": "original remediation card mapped from a locally logged missed concept",
            "study_log_ref": source_tag,
            "review_disposition": "pending-content-qa",
        })
    selected_entries_for_counts = [entry for entry in entries if entry["status"] == "selected"]
    objective_counts = Counter(entry.get("objective") for entry in selected_entries_for_counts)
    return {
        "schema_version": 1,
        "generated_at": DATE_STAMP,
        "candidate_path": relative(CANDIDATE_PATH),
        "source_snapshot_hash": inventory["source_snapshot_hash"],
        "legacy_source_count": len(legacy_entries),
        "legacy_selected_count": sum(1 for entry in entries if entry["origin"] == "legacy" and entry["status"] == "selected"),
        "remediation_count": sum(1 for entry in entries if entry["origin"] == "remediation"),
        "final_question_count": len(candidate.get("questions", [])),
        "objective_floor": OBJECTIVE_FLOOR,
        "objective_roster": roster,
        "objective_counts": dict(sorted(objective_counts.items(), key=lambda item: tuple(int(part) for part in item[0].split(".")))),
        "entries": entries,
        "selection_policy": {
            "legacy_target": LEGACY_TARGET,
            "remediation_target": REMEDIATION_TARGET,
            "extra_domain_quota": DOMAIN_EXTRA_QUOTA,
            "type_targets": TYPE_TARGETS,
            "merge_stem_similarity_ceiling": 0.70,
            "review_disposition_before_qa": "pending-content-qa",
        },
    }


# Kept separate from the card text so the ledger can audit the objective map
# without carrying any study-log prose into an installed pack.
REMEDIATION_MAP = [
    {"card": 1, "objective": "1.4"}, {"card": 2, "objective": "1.1"},
    {"card": 3, "objective": "3.1"}, {"card": 4, "objective": "4.9"},
    {"card": 5, "objective": "3.3"}, {"card": 6, "objective": "4.7"},
    {"card": 7, "objective": "2.2"}, {"card": 8, "objective": "5.3"},
    {"card": 9, "objective": "2.3"}, {"card": 10, "objective": "4.1"},
    {"card": 11, "objective": "5.1"}, {"card": 12, "objective": "3.1"},
    {"card": 13, "objective": "4.7"}, {"card": 14, "objective": "4.1"},
    {"card": 15, "objective": "4.6"}, {"card": 16, "objective": "4.1"},
    {"card": 17, "objective": "4.5"}, {"card": 18, "objective": "5.5"},
    {"card": 19, "objective": "3.4"}, {"card": 20, "objective": "1.1"},
    {"card": 21, "objective": "2.2"}, {"card": 22, "objective": "2.2"},
    {"card": 23, "objective": "2.2"}, {"card": 24, "objective": "2.5"},
]


def build_paths_record() -> dict:
    return {
        "schema_version": 1,
        "status": "planned-not-executed",
        "staging_candidate": relative(CANDIDATE_PATH),
        "source_snapshot": relative(SNAPSHOT_ROOT),
        "archive_destinations": {
            "sy0-701": ARCHIVE_SECURITY_REL,
            "itn260": ARCHIVE_ITN_REL,
        },
        "cutover_rule": "move both complete source inventories and the certified candidate in one uninterrupted local operation; rebuild manifest only after all moves finish",
        "rollback_rule": "restore both archived source inventories, remove the candidate, rebuild manifest only after restoration is complete, then rerun the topology test",
    }


def build_notes(inventory: dict, ledger: dict) -> str:
    return f"""# Security+ Final Review — Staged Build Notes

Status: **STAGED / NOT CERTIFIED** (2026-08-04)

This candidate is discovery-excluded under `question-packs/_staging/`. Active
packs and `question-packs/manifest.json` have not been changed by staging.

## Local build

- Candidate: `{relative(CANDIDATE_PATH)}`
- Source snapshot: `{relative(SNAPSHOT_ROOT)}`
- Source inventory hash: `{inventory['source_snapshot_hash']}`
- Active source baseline: 28 Security+ packs / {inventory['course_counts']['sy0-701']['question_count']} questions; 1 ITN260 pack / {inventory['course_counts']['itn260']['question_count']} questions.
- Selection ledger: `{relative(LEDGER_PATH)}`
- Candidate total: {ledger['final_question_count']} questions ({ledger['legacy_selected_count']} legacy + {ledger['remediation_count']} remediation).
- Progress decision: fresh pack-scoped mastery/history/SRS start; no migration.

## Required gates before cutover

1. Resolve all Layer-A criticals and warnings on the staged candidate.
2. Run standard Layer-C certification, then the strict factual pass.
3. Complete the independently authorized INV-8 content/objective review.
4. Obtain David's human spot-check and record all dispositions here.
5. Only then perform the named reversible cutover and rebuild the manifest.

No external content review has run from this staging command. The candidate is
not certified and must not be installed or used to build the manifest.

## Cutover paths

- Security+ archive: `{ARCHIVE_SECURITY_REL}/`
- ITN260 archive: `{ARCHIVE_ITN_REL}/`
- Full path and rollback contract: `{relative(PATHS_PATH)}`

## Review record

Pending. Do not replace this status with a certification claim until the
standard, strict, independent, and human gates have all completed.
"""


def stage() -> None:
    inventory = build_inventory()
    legacy_entries, roster = load_legacy_questions()
    selected_legacy = select_legacy_questions(legacy_entries, roster)
    candidate, remediation = make_candidate(selected_legacy)
    ledger = build_ledger(inventory, candidate, legacy_entries, roster, selected_legacy, remediation)
    validate_artifacts(candidate, ledger, inventory, check_snapshot=False)
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    snapshot_sources(inventory)
    write_json_atomic(INVENTORY_PATH, inventory)
    write_json_atomic(PATHS_PATH, build_paths_record())
    write_json_atomic(CANDIDATE_PATH, candidate)
    write_json_atomic(LEDGER_PATH, ledger)
    BUILD_NOTES_PATH.write_text(build_notes(inventory, ledger), encoding="utf-8")
    print(f"staged candidate: {relative(CANDIDATE_PATH)}")
    print(f"source inventory: {relative(INVENTORY_PATH)} ({inventory['source_snapshot_hash']})")
    print(f"selection ledger: {relative(LEDGER_PATH)}")
    print("status: local staging only; active packs and manifest unchanged")


def _validate_inventory_against_active(inventory: dict) -> None:
    current = build_inventory()
    if current["source_snapshot_hash"] != inventory.get("source_snapshot_hash"):
        raise AssertionError(
            "active source inventory changed after staging: "
            f"expected {inventory.get('source_snapshot_hash')}, got {current['source_snapshot_hash']}"
        )


def _validate_snapshot(inventory: dict) -> None:
    for entry in inventory.get("files", []):
        source = PROJECT_ROOT / entry["path"]
        if source.is_relative_to(ACTIVE_SECURITY_DIR):
            snapshot = SNAPSHOT_ROOT / "sy0-701" / source.relative_to(ACTIVE_SECURITY_DIR)
        else:
            snapshot = SNAPSHOT_ROOT / "itn260" / source.relative_to(ACTIVE_ITN_DIR)
        if not snapshot.exists():
            raise AssertionError(f"missing snapshot file: {snapshot}")
        actual = sha256_file(snapshot)
        if actual != entry["sha256"]:
            raise AssertionError(f"snapshot hash mismatch for {snapshot}: {actual} != {entry['sha256']}")


def validate_artifacts(candidate: dict, ledger: dict, inventory: dict, *, check_snapshot: bool = True) -> None:
    questions = candidate.get("questions")
    if not isinstance(questions, list) or len(questions) != FINAL_TARGET:
        raise AssertionError(f"candidate must have exactly {FINAL_TARGET} questions")
    if len({question.get("id") for question in questions}) != FINAL_TARGET:
        raise AssertionError("candidate question IDs are not unique")
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise AssertionError("selection ledger entries are missing")
    legacy_selected = [entry for entry in entries if entry.get("origin") == "legacy" and entry.get("status") == "selected"]
    remediation = [entry for entry in entries if entry.get("origin") == "remediation"]
    if len(entries) != 454 + REMEDIATION_TARGET:
        raise AssertionError(f"ledger must contain 478 entries, found {len(entries)}")
    if len(legacy_selected) != LEGACY_TARGET or len(remediation) != REMEDIATION_TARGET:
        raise AssertionError("ledger selection counts do not reconcile")
    final_ids = {entry.get("final_id") for entry in legacy_selected + remediation}
    if final_ids != {question.get("id") for question in questions}:
        raise AssertionError("ledger final IDs do not match candidate question IDs")
    objective_counts = Counter(entry.get("objective") for entry in legacy_selected + remediation)
    expected_roster = {row["objective"] for row in ledger.get("objective_roster", [])}
    if len(expected_roster) != 28:
        raise AssertionError("ledger objective roster must contain exactly 28 objectives")
    missing = sorted(objective for objective in expected_roster if objective_counts[objective] < OBJECTIVE_FLOOR)
    if missing:
        raise AssertionError(f"objective floor undercovered: {missing}")
    if ledger.get("source_snapshot_hash") != inventory.get("source_snapshot_hash"):
        raise AssertionError("ledger and inventory source snapshot hashes differ")
    if check_snapshot:
        _validate_inventory_against_active(inventory)
        _validate_snapshot(inventory)


def validate() -> None:
    for path in (CANDIDATE_PATH, LEDGER_PATH, INVENTORY_PATH, PATHS_PATH):
        if not path.exists():
            raise FileNotFoundError(f"staging artifact missing: {path}")
    candidate = read_json(CANDIDATE_PATH)
    ledger = read_json(LEDGER_PATH)
    inventory = read_json(INVENTORY_PATH)
    validate_artifacts(candidate, ledger, inventory)
    print(f"valid staged candidate: {relative(CANDIDATE_PATH)}")
    print(f"valid snapshot: {relative(SNAPSHOT_ROOT)}")
    print("active source hashes match the staged inventory")


def paths() -> None:
    print(json.dumps(build_paths_record(), indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("stage", "validate", "paths"))
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            stage()
        elif args.command == "validate":
            validate()
        else:
            paths()
    except (AssertionError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

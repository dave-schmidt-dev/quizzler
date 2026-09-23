#!/usr/bin/env python3
"""Build and install the Quizzler Mac app locally and clean registrations.

Mac Catalyst build of the `Quizzler` scheme in `app/Quizzler.xcodeproj`
(product `QuizzleriOS.app`, bundle id `com.zerodelta.quizzler`).
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, TextIO

BUNDLE_ID = "com.zerodelta.quizzler"
ICLOUD_CONTAINER = "iCloud.com.zerodelta.quizzler.dev"
LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
)
ROOT = Path(__file__).resolve().parents[2]

# Every external command is bounded; a hung tool fails the step instead of the session.
BUILD_TIMEOUT = 1800.0
DUMP_TIMEOUT = 120.0
COMMAND_TIMEOUT = 60.0
HEARTBEAT_INTERVAL = 30.0


class CommandRunner:
    """Wrapper around subprocess for injectable test execution."""

    def run(
        self,
        argv: list[str],
        *,
        check: bool = False,
        capture_output: bool = True,
        text: bool = True,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            check=check,
            capture_output=capture_output,
            text=text,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )

    def run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        heartbeat: Callable[[float], None] | None = None,
        interval: float = HEARTBEAT_INTERVAL,
    ) -> subprocess.CompletedProcess[str]:
        """Run argv with stdout and stderr streamed into log_path.

        Calls heartbeat(elapsed_seconds) every interval seconds while the command
        runs. Raises subprocess.TimeoutExpired after stopping the command when it
        outlives timeout.
        """
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while True:
                    wait_for = interval
                    if timeout is not None:
                        remaining = timeout - (time.monotonic() - start)
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, timeout)
                        wait_for = min(interval, remaining)
                    try:
                        returncode = proc.wait(timeout=wait_for)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - start
                        if timeout is not None and elapsed >= timeout:
                            raise subprocess.TimeoutExpired(argv, timeout) from None
                        if heartbeat is not None:
                            heartbeat(elapsed)
            except BaseException:
                # xcodebuild starts compilers of its own; stop the whole group.
                _signal_group(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _signal_group(proc, signal.SIGKILL)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                raise
        return subprocess.CompletedProcess(argv, returncode)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal proc's process group (it leads one via start_new_session)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _run_cmd(
    runner: Any,
    argv: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    if hasattr(runner, "run") and callable(runner.run):
        return runner.run(
            argv,
            check=check,
            capture_output=capture_output,
            text=text,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
    return runner(
        argv,
        check=check,
        capture_output=capture_output,
        text=text,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


def _run_build(
    runner: Any,
    argv: list[str],
    *,
    cwd: Path,
    log_path: Path,
    heartbeat: Callable[[float], None],
) -> tuple[int, str]:
    """Run the build with its output in log_path; return (returncode, log text)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    streaming = getattr(runner, "run_streaming", None)
    if callable(streaming):
        res = streaming(
            argv,
            log_path=log_path,
            cwd=cwd,
            timeout=BUILD_TIMEOUT,
            heartbeat=heartbeat,
        )
        log_content = log_path.read_text(encoding="utf-8", errors="replace")
    else:
        res = _run_cmd(runner, argv, cwd=cwd, timeout=BUILD_TIMEOUT)
        log_content = (res.stdout or "") + (res.stderr or "")
        log_path.write_text(log_content, encoding="utf-8")
    return res.returncode, log_content


def bundle_identifier(app: Path | str) -> str | None:
    """Return CFBundleIdentifier of an app bundle, or None if missing/unreadable."""
    plist_path = Path(app) / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        with open(plist_path, "rb") as fp:
            data = plistlib.load(fp)
        if isinstance(data, dict):
            val = data.get("CFBundleIdentifier")
            return str(val) if val is not None else None
    except Exception:
        return None
    return None


def wrapped_bundle_identifiers(app: Path | str) -> list[str]:
    """Return identifiers of iOS apps wrapped for Apple silicon Macs (`<app>/Wrapper/*.app`).

    TestFlight and App Store installs of the iOS app live in a wrapper with no
    `Contents/Info.plist`, yet LaunchServices registers them under the same id.
    """
    wrapper = Path(app) / "Wrapper"
    identifiers: list[str] = []
    try:
        inner_apps = sorted(wrapper.glob("*.app"))
    except OSError:
        return identifiers
    for inner in inner_apps:
        try:
            with open(inner / "Info.plist", "rb") as fp:
                data = plistlib.load(fp)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("CFBundleIdentifier") is not None:
            identifiers.append(str(data["CFBundleIdentifier"]))
    return identifiers


def conflicting_bundles(
    directory: Path | str,
    identifier: str,
    keep: Path | str | None = None,
    skip_prefix: str | None = None,
) -> list[Path]:
    """Find *.app directories up to 3 levels below directory claiming identifier, excluding keep.

    Top-level entries whose names start with skip_prefix are ignored; install
    passes its own staging/backup sibling prefix, which it cleans up itself.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    keep_resolved = Path(keep).resolve() if keep is not None else None
    results: list[Path] = []

    def scan_dir(curr: Path, depth: int) -> None:
        if depth > 3:
            return
        try:
            entries = sorted(curr.iterdir())
        except OSError:
            return
        for entry in entries:
            if depth == 1 and skip_prefix and entry.name.startswith(skip_prefix):
                continue
            if not entry.is_dir():
                continue
            if entry.name.endswith(".app"):
                if keep_resolved is None or entry.resolve() != keep_resolved:
                    if (
                        bundle_identifier(entry) == identifier
                        or identifier in wrapped_bundle_identifiers(entry)
                    ):
                        results.append(entry)
                # Do not descend into a found .app
            elif depth < 3:
                scan_dir(entry, depth + 1)

    scan_dir(dir_path, 1)
    return sorted(results)


def parse_lsregister_dump(text: str, identifier: str) -> list[str]:
    """Parse lsregister dump for first identifier and first path per record."""
    records: list[dict[str, str]] = []
    curr_id: str | None = None
    curr_path: str | None = None

    def flush_record() -> None:
        nonlocal curr_id, curr_path
        if curr_id is not None and curr_path is not None:
            records.append({"id": curr_id, "path": curr_path})
        curr_id = None
        curr_path = None

    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^-{20,}$", stripped):
            flush_record()
            continue
        if curr_id is None:
            m_id = re.match(r"^[ \t]*identifier:[ \t]+(.*)$", line)
            if m_id:
                curr_id = m_id.group(1).strip()
                continue
        if curr_path is None:
            m_path = re.match(r"^[ \t]*path:[ \t]+(.*)$", line)
            if m_path:
                raw_path = m_path.group(1).strip()
                raw_path = re.sub(r"\s+\(0x[0-9a-fA-F]+\)$", "", raw_path)
                curr_path = raw_path
                continue

    flush_record()

    matching = {
        r["path"] for r in records if r["id"] == identifier and r["path"]
    }
    return sorted(matching)


def prune_refusal_reason(repo_root: Path | str) -> str | None:
    """Return why app/build must not be pruned, or None when pruning is safe."""
    if (Path(repo_root) / "app" / "build").is_symlink():
        return "build-root-is-symlink"
    return None


def prune_build_products(
    repo_root: Path | str,
    identifier: str,
    keep: Path | str | None = None,
) -> list[Path]:
    """Remove every *.app matching identifier under repo_root/app/build except keep.

    Archives (`*.xcarchive`, e.g. `app/build/testflight/`) are release evidence and
    are never pruned. Symlinks are never followed: a symlinked build root prunes
    nothing, and symlinked entries below it are skipped.
    """
    if prune_refusal_reason(repo_root) is not None:
        return []
    build_root = (Path(repo_root) / "app" / "build").resolve()
    if not build_root.is_dir():
        return []

    keep_resolved = Path(keep).resolve() if keep is not None else None
    removed: list[Path] = []

    def find_apps(curr: Path) -> list[Path]:
        found: list[Path] = []
        try:
            entries = sorted(curr.iterdir())
        except OSError:
            return found
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name.endswith(".xcarchive"):
                continue
            if entry.name.endswith(".app"):
                found.append(entry)
            else:
                found.extend(find_apps(entry))
        return found

    candidate_apps = find_apps(build_root)
    for app in candidate_apps:
        if app.is_symlink():
            continue
        app_resolved = app.resolve()
        try:
            relative = app_resolved.relative_to(build_root)
        except ValueError:
            continue
        if any(part.endswith(".xcarchive") for part in relative.parts):
            continue
        if keep_resolved is not None and app_resolved == keep_resolved:
            continue
        if bundle_identifier(app) == identifier:
            shutil.rmtree(app)
            removed.append(app)

    return sorted(removed)


def running_bundle_pids(identifier: str, ps_output: str) -> list[int]:
    """Parse ps -axo pid=,comm= output for processes whose executable is in a matching bundle."""
    pids: list[int] = []
    marker = ".app/Contents/MacOS/"
    for line in ps_output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, comm = parts
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if marker not in comm:
            continue
        app_path_str = comm.split(marker, 1)[0] + ".app"
        app_path = Path(app_path_str)
        if bundle_identifier(app_path) == identifier:
            pids.append(pid)
    return sorted(list(set(pids)))


def sweep_registrations(
    runner: Any,
    identifier: str,
    keep: Path | str,
    lsregister_path: Path | str = LSREGISTER,
) -> None:
    """lsregister -u every registered path that is not keep, then lsregister -f keep."""
    res = _run_cmd(
        runner,
        [str(lsregister_path), "-dump"],
        capture_output=True,
        text=True,
        check=False,
        timeout=DUMP_TIMEOUT,
    )
    registered_paths = parse_lsregister_dump(res.stdout or "", identifier)
    keep_path = Path(keep)
    keep_str = str(keep_path)
    keep_resolved_str = str(keep_path.resolve())

    for path in registered_paths:
        if path != keep_str and path != keep_resolved_str:
            _run_cmd(
                runner,
                [str(lsregister_path), "-u", path],
                capture_output=True,
                text=True,
                check=False,
            )

    _run_cmd(
        runner,
        [str(lsregister_path), "-f", keep_str],
        capture_output=True,
        text=True,
        check=False,
    )


def _get_running_pids(runner: Any, identifier: str) -> list[int]:
    res = _run_cmd(
        runner,
        ["ps", "-axo", "pid=,comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    return running_bundle_pids(identifier, res.stdout or "")


def _wait_for_bundle_exit(
    runner: Any,
    identifier: str,
    seconds: float,
    sleep_fn: Callable[[float], None],
) -> bool:
    polls = int(seconds * 2)
    if polls <= 0:
        polls = 1
    for _ in range(polls):
        if not _get_running_pids(runner, identifier):
            return True
        sleep_fn(0.5)
    return not bool(_get_running_pids(runner, identifier))


def _remove_path(path: Path) -> None:
    """Delete path without following it when it is a symlink; absent paths are fine."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _replace_target(
    runner: Any,
    product: Path,
    target: Path,
    status: Callable[[str], None],
) -> bool:
    """Stage, verify, and swap product into target, restoring the old target on failure.

    The copy lands in `.<target>.installing-<pid>/<target>` beside target, so the
    signature check sees a normally named bundle and the final move is a rename
    on the same filesystem. The previous target is renamed to
    `.<target>.previous-<pid>` and deleted only after the new one is in place.
    A subprocess.TimeoutExpired is re-raised after the rollback.
    """
    pid = os.getpid()
    staging_dir = target.parent / f".{target.name}.installing-{pid}"
    staged = staging_dir / target.name
    backup = target.parent / f".{target.name}.previous-{pid}"
    moved_to_backup = False
    committed = False
    try:
        _remove_path(staging_dir)

        ditto_res = _run_cmd(runner, ["ditto", str(product), str(staged)], capture_output=True, text=True, check=False)
        if ditto_res.returncode != 0:
            status(f"install.error failed to copy {product} to {target}")
            return False

        verify_staged = _run_cmd(
            runner,
            ["codesign", "--verify", "--strict", str(staged)],
            capture_output=True,
            text=True,
            check=False,
        )
        if verify_staged.returncode != 0:
            status("install.error installed app failed signature verification")
            return False

        if target.exists() or target.is_symlink():
            # A same-pid backup can only be a leftover superseded by the live target.
            _remove_path(backup)
            os.replace(target, backup)
            moved_to_backup = True
        os.replace(staged, target)
        committed = True
    except OSError as exc:
        status(f"install.error could not put {target} in place: {exc}")
        return False
    finally:
        if not committed:
            if moved_to_backup and not (target.exists() or target.is_symlink()):
                try:
                    os.replace(backup, target)
                except OSError as exc:
                    status(f"install.error rollback incomplete; previous app kept at {backup}: {exc}")
            try:
                _remove_path(staging_dir)
            except OSError as exc:
                status(f"install.error could not remove staging copy {staging_dir}: {exc}")

    # The new target is in place; now drop this run's backup and any staging or
    # backup siblings a crashed earlier run left behind.
    leftovers = sorted(
        set(target.parent.glob(f".{target.name}.installing-*"))
        | set(target.parent.glob(f".{target.name}.previous-*"))
    )
    for leftover in leftovers:
        try:
            _remove_path(leftover)
        except OSError as exc:
            status(f"install.warning could not remove {leftover}: {exc}")
    return True


class _Progress:
    """Names the install step in progress, for timeout reporting."""

    def __init__(self) -> None:
        self.step = "setup"


def install(
    destination: Path | str = Path("/Applications"),
    repo_root: Path | str = ROOT,
    runner: Any | None = None,
    skip_build: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    kill_fn: Callable[[int, int], None] = os.kill,
    stderr: TextIO = sys.stderr,
) -> int:
    """Perform Mac app installation."""
    if runner is None:
        runner = CommandRunner()

    def status(msg: str) -> None:
        print(msg, file=stderr, flush=True)

    progress = _Progress()
    try:
        return _install_steps(
            destination, repo_root, runner, skip_build, sleep_fn, kill_fn, status, progress
        )
    except subprocess.TimeoutExpired as exc:
        cmd = exc.cmd
        tool = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
        status(f"install.error {progress.step} timed out after {exc.timeout:g}s: {Path(str(tool)).name}")
        return 1


def _install_steps(
    destination: Path | str,
    repo_root: Path | str,
    runner: Any,
    skip_build: bool,
    sleep_fn: Callable[[float], None],
    kill_fn: Callable[[int, int], None],
    status: Callable[[str], None],
    progress: _Progress,
) -> int:
    dest = Path(destination).resolve()
    root = Path(repo_root).resolve()

    # a. Refuse unless destination is a writable directory
    if not dest.is_dir() or not os.access(dest, os.W_OK):
        status(f"install.error destination is not a writable directory: {destination}")
        return 1

    derived_data = root / "app" / "build" / "mac-install"
    product = derived_data / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app"

    # b. Build
    if not skip_build:
        progress.step = "build"
        if product.exists() or product.is_symlink():
            status(f"install.clean previous-product={product}")
            if product.is_dir() and not product.is_symlink():
                shutil.rmtree(product)
            else:
                product.unlink()

        status("install.build configuration=Debug")
        xcodebuild_cmd = [
            "xcodebuild",
            "-project",
            "app/Quizzler.xcodeproj",
            "-scheme",
            "Quizzler",
            "-configuration",
            "Debug",
            "-destination",
            "platform=macOS,variant=Mac Catalyst",
            "-derivedDataPath",
            "app/build/mac-install",
            "-allowProvisioningUpdates",
            "-quiet",
            "build",
        ]
        log_path = root / "app" / "build" / "mac-install.log"
        build_returncode, log_content = _run_build(
            runner,
            xcodebuild_cmd,
            cwd=root,
            log_path=log_path,
            heartbeat=lambda elapsed: status(f"install.build running elapsed={int(elapsed)}s"),
        )
        if build_returncode != 0:
            status("install.build failed")
            lines = log_content.splitlines()[-40:]
            for line in lines:
                status(line)
            return 1

    # c. Product verification
    progress.step = "verify"
    if not product.is_dir():
        status(f"install.error no app product was produced at {product}")
        return 1

    built_id = bundle_identifier(product)
    if built_id != BUNDLE_ID:
        status(f"install.error built bundle identifier is '{built_id}', expected '{BUNDLE_ID}'")
        return 1

    verify_res = _run_cmd(
        runner,
        ["codesign", "--verify", "--deep", "--strict", str(product)],
        capture_output=True,
        text=True,
        check=False,
    )
    if verify_res.returncode != 0:
        status("install.error built app failed signature verification")
        return 1

    ent_res = _run_cmd(
        runner,
        ["codesign", "-d", "--entitlements", "-", "--xml", str(product)],
        capture_output=True,
        text=True,
        check=False,
    )
    if ent_res.returncode != 0:
        status("install.error failed to read entitlements from built app")
        return 1

    ent_raw = ent_res.stdout if (ent_res.stdout and ent_res.stdout.strip()) else ent_res.stderr
    try:
        ent_data = plistlib.loads(ent_raw.encode("utf-8") if isinstance(ent_raw, str) else ent_raw)
    except Exception:
        status("install.error built app entitlements invalid XML")
        return 1

    containers = ent_data.get("com.apple.developer.icloud-container-identifiers") if isinstance(ent_data, dict) else None
    valid_container = False
    if isinstance(containers, list) and ICLOUD_CONTAINER in containers:
        valid_container = True
    elif isinstance(containers, str) and containers == ICLOUD_CONTAINER:
        valid_container = True

    if not valid_container:
        status(f"install.error built app missing iCloud container entitlement: {ICLOUD_CONTAINER}")
        return 1

    # d. Target checks
    progress.step = "conflicts"
    target = Path(destination) / "Quizzler.app"
    if target.exists():
        existing_id = bundle_identifier(target)
        if existing_id != BUNDLE_ID:
            status(f"install.error {target} exists and is not {BUNDLE_ID}; refusing to replace it")
            return 1

    conflicts = conflicting_bundles(
        destination, BUNDLE_ID, keep=target, skip_prefix=f".{target.name}."
    )
    if conflicts:
        status(f"install.error another bundle in {destination} already claims {BUNDLE_ID}:")
        for c in conflicts:
            status(f"install.error   {c}")
        status("install.error move it to the Trash and run this again; two bundles for one identifier")
        status("install.error make which binary launches unpredictable")
        return 1

    # e. Quit running copies
    progress.step = "quit"
    running_pids = _get_running_pids(runner, BUNDLE_ID)
    for pid in running_pids:
        status(f"install.quit pid={pid}")

    # Only address the app when a copy is running: with duplicate registrations,
    # an Apple event by bundle id could otherwise start a stale bundle.
    if running_pids:
        _run_cmd(
            runner,
            ["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'],
            capture_output=True,
            text=True,
            check=False,
        )

    if running_pids and not _wait_for_bundle_exit(runner, BUNDLE_ID, 5.0, sleep_fn):
        surviving = _get_running_pids(runner, BUNDLE_ID)
        for pid in surviving:
            try:
                kill_fn(pid, signal.SIGTERM)
            except OSError:
                pass
        if not _wait_for_bundle_exit(runner, BUNDLE_ID, 5.0, sleep_fn):
            surviving = _get_running_pids(runner, BUNDLE_ID)
            for pid in surviving:
                try:
                    kill_fn(pid, signal.SIGKILL)
                except OSError:
                    pass
            if not _wait_for_bundle_exit(runner, BUNDLE_ID, 3.0, sleep_fn):
                surviving = _get_running_pids(runner, BUNDLE_ID)
                pids_str = " ".join(str(p) for p in surviving)
                status(f"install.error a running copy would not exit: {pids_str}")
                return 1

    # f. Replace the target (staged copy, verified, then swapped; rollback on failure)
    progress.step = "replace"
    if not _replace_target(runner, product, target, status):
        return 1

    # g. Prune build products
    progress.step = "prune"
    refusal = prune_refusal_reason(root)
    if refusal is not None:
        status(f"install.prune skipped reason={refusal} path={root / 'app' / 'build'}")
    else:
        pruned = prune_build_products(root, BUNDLE_ID, keep=product)
        for p in pruned:
            status(f"install.prune removed={p}")

    # h. Sweep registrations and verify resolution
    progress.step = "sweep"
    status("install.register sweep")
    sweep_registrations(runner, BUNDLE_ID, keep=target)

    progress.step = "resolve"
    dump_res = _run_cmd(
        runner,
        [str(LSREGISTER), "-dump"],
        capture_output=True,
        text=True,
        check=False,
        timeout=DUMP_TIMEOUT,
    )
    registered = parse_lsregister_dump(dump_res.stdout or "", BUNDLE_ID)
    target_str = str(target)
    target_res_str = str(target.resolve())

    if len(registered) != 1 or (registered[0] != target_str and registered[0] != target_res_str):
        status(f"install.error {BUNDLE_ID} does not resolve to {target} after the sweep; registered paths:")
        if not registered:
            status("install.error   (none)")
        else:
            for p in registered:
                status(f"install.error   {p}")
        return 1

    status(f"install.register resolves={target}")

    # i. Finish
    progress.step = "finish"
    version = "unknown"
    build = "unknown"
    target_info = target / "Contents" / "Info.plist"
    if target_info.is_file():
        try:
            with open(target_info, "rb") as fp:
                info_dict = plistlib.load(fp)
            if isinstance(info_dict, dict):
                version = str(info_dict.get("CFBundleShortVersionString", "unknown"))
                build = str(info_dict.get("CFBundleVersion", "unknown"))
        except Exception:
            pass

    git_res = _run_cmd(
        runner,
        ["git", "-C", str(root), "describe", "--always", "--dirty"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = git_res.stdout.strip() if (git_res.returncode == 0 and git_res.stdout.strip()) else "unknown"

    status(f"install.complete path={target} version={version} build={build} commit={commit}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for install_mac_app CLI."""
    parser = argparse.ArgumentParser(description="Install Quizzler Mac app.")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("/Applications"),
        help="Destination directory (default: /Applications)",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Install existing product without building",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root (default: repo root)",
    )
    args = parser.parse_args(argv)
    return install(
        destination=args.destination,
        repo_root=args.repo_root,
        skip_build=args.skip_build,
    )


if __name__ == "__main__":
    raise SystemExit(main())

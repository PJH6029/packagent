from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import sys
from typing import Sequence

from packagent.activation import linux_namespace_support_error
from packagent.app import PackagentManager
from packagent.errors import UserFacingError
from packagent.models import ActivationResult, DoctorReport, StatusReport
from packagent.shell import (
    SUPPORTED_SHELLS,
    default_rc_path,
    detect_shell,
    install_shell_init,
    namespace_shell_error_message,
    render_activate_commands,
    render_deactivate_commands,
    render_shell_init,
    shell_hook_error_message,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="packagent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    shell_parser = subparsers.add_parser("shell", help="shell integration helpers")
    shell_subparsers = shell_parser.add_subparsers(dest="shell_command", required=True)
    shell_init = shell_subparsers.add_parser("init", help="print a shell hook")
    shell_init.add_argument("shell", choices=SUPPORTED_SHELLS)
    shell_supports = shell_subparsers.add_parser("supports-namespace", help=argparse.SUPPRESS)
    shell_supports.add_argument("--quiet", action="store_true")
    shell_enter = shell_subparsers.add_parser("enter", help=argparse.SUPPRESS)
    shell_enter.add_argument("shell", choices=SUPPORTED_SHELLS)
    shell_attach = shell_subparsers.add_parser("attach", help=argparse.SUPPRESS)
    shell_attach.add_argument("shell", choices=SUPPORTED_SHELLS)

    init_parser = subparsers.add_parser("init", help="install shell startup integration")
    init_parser.add_argument("--shell", choices=SUPPORTED_SHELLS)
    init_parser.add_argument("--rc-file")

    create_parser = subparsers.add_parser("create", help="create an environment")
    create_parser.add_argument("-n", "--name", required=True)
    create_parser.add_argument("--clone")

    activate_parser = subparsers.add_parser("activate", help="activate an environment")
    activate_parser.add_argument("name")

    subparsers.add_parser("deactivate", help="return to the base environment")
    subparsers.add_parser("list", help="list environments")
    subparsers.add_parser("status", help="show current status")

    remove_parser = subparsers.add_parser("remove", help="remove an environment")
    remove_parser.add_argument("name")

    doctor_parser = subparsers.add_parser("doctor", help="inspect the managed home")
    doctor_parser.add_argument("--fix", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = PackagentManager()
    try:
        if args.command == "shell":
            if args.shell_command == "init":
                return _handle_shell_init(manager, args.shell)
            if args.shell_command == "supports-namespace":
                return _handle_shell_supports_namespace(quiet=args.quiet)
            if args.shell_command == "enter":
                return _handle_shell_enter(manager, args.shell)
            if args.shell_command == "attach":
                return _handle_shell_attach(manager, args.shell)
        if args.command == "init":
            return _handle_init(manager, args.shell, args.rc_file)
        if args.command == "create":
            metadata = manager.create_env(args.name, clone_from=args.clone)
            print(f"created\t{metadata.name}\t{manager.paths.env_dir(metadata.name)}")
            return 0
        if args.command == "activate":
            return _handle_activate(manager, args.name)
        if args.command == "deactivate":
            return _handle_deactivate(manager)
        if args.command == "list":
            rows = manager.list_envs()
            for row in rows:
                print(f"{row['active']}\t{row['name']}\t{row['path']}")
            return 0
        if args.command == "status":
            _print_status(manager.status())
            return 0
        if args.command == "remove":
            manager.remove_env(args.name)
            print(f"removed\t{args.name}")
            return 0
        if args.command == "doctor":
            report = manager.doctor(fix=args.fix)
            _print_doctor(report)
            return 0 if not report.issues else 1
    except UserFacingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _handle_shell_init(manager: PackagentManager, shell_name: str) -> int:
    status = manager.status()
    env_name = status.current_env or status.default_env
    backing_home = status.home_target or status.default_target
    initial_result = ActivationResult(
        env_name=env_name,
        managed_home_path=status.managed_home_path,
        backing_home_path=backing_home,
    )
    print(
        render_shell_init(
            shell_name,
            initial_result,
            use_linux_namespace=manager.backend.shell_scoped,
        ),
    )
    return 0


def _handle_shell_supports_namespace(*, quiet: bool = False) -> int:
    error = linux_namespace_support_error()
    if error is None:
        return 0
    if not quiet:
        print(error, file=sys.stderr)
    return 1


def _handle_shell_enter(manager: PackagentManager, shell_name: str) -> int:
    if not manager.backend.shell_scoped:
        raise UserFacingError("shell namespace entry is only supported for Linux per-shell activation")
    error = linux_namespace_support_error()
    if error is not None:
        raise UserFacingError(error)
    command = [
        "unshare",
        "--user",
        "--mount",
        "--map-root-user",
        "--propagation",
        "private",
        sys.executable,
        "-m",
        "packagent",
        "shell",
        "attach",
        shell_name,
    ]
    try:
        os.execvpe(command[0], command, os.environ.copy())
    except OSError as exc:
        raise UserFacingError(f"failed to enter the Linux shell namespace: {exc}") from exc


def _handle_shell_attach(manager: PackagentManager, shell_name: str) -> int:
    if not manager.backend.shell_scoped:
        raise UserFacingError("shell namespace attach is only supported for Linux per-shell activation")
    default_env = manager.load_default_env()
    result = manager.activate_env(default_env)
    shell_path = _resolve_shell_path(shell_name)
    env = os.environ.copy()
    env["PACKAGENT_NAMESPACE_ACTIVE"] = "1"
    env["PACKAGENT_SHELL_HOOK"] = "1"
    env["PACKAGENT_SHELL"] = shell_name
    env["PACKAGENT_ACTIVE_ENV"] = result.env_name
    env["PACKAGENT_ACTIVE_HOST"] = "codex"
    env["PACKAGENT_BACKING_HOME"] = result.backing_home_path
    env["CODEX_HOME"] = result.managed_home_path
    env["SHELL"] = shell_path
    try:
        os.execvpe(shell_path, [shell_path, "-i"], env)
    except OSError as exc:
        raise UserFacingError(f"failed to start the packagent-managed {shell_name} shell: {exc}") from exc


def _resolve_shell_path(shell_name: str) -> str:
    shell_path = shutil.which(shell_name)
    if shell_path:
        return shell_path
    raise UserFacingError(f"unable to find '{shell_name}' on PATH")


def _handle_activate(manager: PackagentManager, env_name: str) -> int:
    if not _shell_hook_active():
        print(shell_hook_error_message(detect_shell()), file=sys.stderr)
        return 2
    if manager.backend.shell_scoped and not _namespace_shell_active():
        print(namespace_shell_error_message(), file=sys.stderr)
        return 2
    shell_name = _current_shell()
    result = manager.activate_env(env_name)
    print(render_activate_commands(shell_name, result))
    return 0


def _handle_deactivate(manager: PackagentManager) -> int:
    if not _shell_hook_active():
        print(shell_hook_error_message(detect_shell()), file=sys.stderr)
        return 2
    if manager.backend.shell_scoped and not _namespace_shell_active():
        print(namespace_shell_error_message(), file=sys.stderr)
        return 2
    shell_name = _current_shell()
    result = manager.deactivate_env()
    print(render_deactivate_commands(shell_name, result))
    return 0


def _handle_init(manager: PackagentManager, requested_shell: str | None, rc_file: str | None) -> int:
    shell_name = requested_shell or detect_shell()
    status = manager.status()
    target = Path(rc_file).expanduser() if rc_file else default_rc_path(shell_name, manager.paths.home)
    result = install_shell_init(shell_name, target)
    print(
        f"initialized\t{result.shell_name}\t{result.rc_path}\t"
        f"{'updated' if result.changed else 'unchanged'}",
    )
    print(f"default_env\t{status.default_env}")
    print(f"run_now\teval \"$(packagent shell init {shell_name})\"")
    print(f"reload\tsource {shlex.quote(result.rc_path)}")
    return 0


def _shell_hook_active() -> bool:
    return bool(os.environ.get("PACKAGENT_SHELL_HOOK"))


def _namespace_shell_active() -> bool:
    return bool(os.environ.get("PACKAGENT_NAMESPACE_ACTIVE"))


def _current_shell() -> str:
    shell_name = os.environ.get("PACKAGENT_SHELL", "")
    if shell_name in SUPPORTED_SHELLS:
        return shell_name
    return detect_shell()


def _print_status(status: StatusReport) -> None:
    print(f"current_env={status.current_env or ''}")
    print(f"default_env={status.default_env}")
    print(f"managed={str(status.managed).lower()}")
    print(f"managed_home={status.managed_home_path}")
    print(f"home_kind={status.home_kind}")
    print(f"home_target={status.home_target or ''}")
    print(f"default_target={status.default_target}")


def _print_doctor(report: DoctorReport) -> None:
    _print_status(report.status)
    for issue in report.issues:
        print(f"issue={issue}")
    for repaired in report.repaired:
        print(f"repaired={repaired}")

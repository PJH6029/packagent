from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import sys
from typing import Sequence

from packagent.app import BASE_MODE_FRESH, BASE_MODE_IMPORT, BASE_MODES, PackagentManager
from packagent.errors import UserFacingError
from packagent.models import ActivationResult, DoctorReport, StatusReport
from packagent.shell import (
    SUPPORTED_SHELLS,
    default_rc_path,
    detect_shell,
    install_shell_init,
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

    init_parser = subparsers.add_parser("init", help="install shell startup integration")
    init_parser.add_argument("--shell", choices=SUPPORTED_SHELLS)
    init_parser.add_argument("--rc-file")
    init_parser.add_argument("--base-mode", choices=BASE_MODES)

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
            status = manager.status()
            initial_result = ActivationResult(
                env_name=status.active_env,
                managed_home_path=status.managed_home_path,
                codex_home=status.expected_target,
            )
            print(render_shell_init(args.shell, initial_result))
            return 0
        if args.command == "init":
            return _handle_init(manager, args.shell, args.rc_file, args.base_mode)
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


def _handle_activate(manager: PackagentManager, env_name: str) -> int:
    if not _shell_hook_active():
        print(shell_hook_error_message(detect_shell()), file=sys.stderr)
        return 2
    shell_name = _current_shell()
    result = manager.activate_env(env_name)
    print(render_activate_commands(shell_name, result))
    return 0


def _handle_deactivate(manager: PackagentManager) -> int:
    if not _shell_hook_active():
        print(shell_hook_error_message(detect_shell()), file=sys.stderr)
        return 2
    shell_name = _current_shell()
    result = manager.deactivate_env()
    print(render_deactivate_commands(shell_name, result))
    return 0


def _handle_init(
    manager: PackagentManager,
    requested_shell: str | None,
    rc_file: str | None,
    requested_base_mode: str | None,
) -> int:
    shell_name = requested_shell or detect_shell()
    base_mode = _resolve_base_mode(manager, requested_base_mode)
    activation = manager.initialize_base(base_mode)
    target = Path(rc_file).expanduser() if rc_file else default_rc_path(shell_name, manager.paths.home)
    result = install_shell_init(shell_name, target)
    print(
        f"initialized\t{result.shell_name}\t{result.rc_path}\t"
        f"{'updated' if result.changed else 'unchanged'}",
    )
    print(f"base_mode\t{base_mode}")
    print(f"active_env\t{activation.env_name}")
    print(f"run_now\teval \"$(packagent shell init {shell_name})\"")
    print(f"reload\tsource {shlex.quote(result.rc_path)}")
    return 0


def _resolve_base_mode(manager: PackagentManager, requested_base_mode: str | None) -> str:
    if requested_base_mode:
        return requested_base_mode
    if not sys.stdin.isatty() or not manager.base_init_prompt_needed():
        return BASE_MODE_IMPORT
    print(
        "packagent found existing unmanaged Codex, agents, or Claude homes.",
        file=sys.stderr,
    )
    print("Choose how to create the base environment:", file=sys.stderr)
    print("  import: back up existing homes and import them into base", file=sys.stderr)
    print("  fresh:  back up existing homes and start base empty", file=sys.stderr)
    while True:
        print("Base mode [import/fresh, default import]: ", end="", file=sys.stderr)
        answer = input().strip().lower()
        if answer in {"", "i", "import", "y", "yes"}:
            return BASE_MODE_IMPORT
        if answer in {"f", "fresh", "n", "no", "new"}:
            return BASE_MODE_FRESH
        print("Please enter 'import' or 'fresh'.", file=sys.stderr)


def _shell_hook_active() -> bool:
    return bool(os.environ.get("PACKAGENT_SHELL_HOOK"))


def _current_shell() -> str:
    shell_name = os.environ.get("PACKAGENT_SHELL", "")
    if shell_name in SUPPORTED_SHELLS:
        return shell_name
    return detect_shell()


def _print_status(status: StatusReport) -> None:
    print(f"active_env={status.active_env}")
    print(f"managed={str(status.managed).lower()}")
    print(f"managed_home={status.managed_home_path}")
    print(f"home_kind={status.home_kind}")
    print(f"home_target={status.home_target or ''}")
    print(f"expected_target={status.expected_target}")
    for target_status in status.target_statuses:
        print(
            "\t".join(
                [
                    "target",
                    target_status.key,
                    str(target_status.managed).lower(),
                    target_status.managed_home_path,
                    target_status.home_kind,
                    target_status.home_target or "",
                    target_status.expected_target,
                ],
            ),
        )


def _print_doctor(report: DoctorReport) -> None:
    _print_status(report.status)
    for issue in report.issues:
        print(f"issue={issue}")
    for repaired in report.repaired:
        print(f"repaired={repaired}")

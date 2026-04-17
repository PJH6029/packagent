from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import sys
from typing import Sequence

from packagent.app import (
    BASE_MODE_FRESH,
    BASE_MODE_IMPORT,
    BASE_MODES,
    RESTORE_SOURCE_BACKUP,
    RESTORE_SOURCE_BASE,
    RESTORE_SOURCES,
    PackagentManager,
)
from packagent.errors import UserFacingError
from packagent.models import ActivationResult, DoctorReport, StatusReport, UninstallResult
from packagent.shell import (
    ShellInitRemoveResult,
    SUPPORTED_SHELLS,
    default_rc_path,
    detect_shell,
    install_shell_init,
    remove_shell_init,
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
    shell_subparsers.add_parser("active-env", help=argparse.SUPPRESS)

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

    uninstall_parser = subparsers.add_parser("uninstall", help="restore managed homes and remove shell integration")
    uninstall_parser.add_argument("--restore-source", choices=RESTORE_SOURCES)
    uninstall_parser.add_argument("--shell", choices=SUPPORTED_SHELLS)
    uninstall_parser.add_argument("--rc-file")

    doctor_parser = subparsers.add_parser("doctor", help="inspect the managed home")
    doctor_parser.add_argument("--fix", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = PackagentManager()
    try:
        if args.command == "shell":
            if args.shell_command == "active-env":
                active_env = manager.shell_active_env()
                if active_env:
                    print(active_env)
                return 0
            if args.shell_command == "init":
                initial_result = None
                active_env = manager.shell_active_env()
                if active_env:
                    status = manager.status()
                    initial_result = ActivationResult(
                        env_name=active_env,
                        managed_home_path=status.managed_home_path,
                        codex_home=status.expected_target,
                    )
                print(render_shell_init(args.shell, initial_result))
                return 0
            return 0
        if args.command == "init":
            return _handle_init(manager, args.shell, args.rc_file, args.base_mode)
        if args.command == "create":
            metadata = manager.create_env(args.name, clone_from=args.clone)
            print(f"created\t{metadata.name}\t{manager.paths.env_dir(metadata.name)}")
            if metadata.cloned_from:
                print(f"You've created an env based on {metadata.cloned_from}.")
            else:
                print(
                    "You've created an env with bare agent homes except shared auth. "
                    "Use packagent create -n <env-name> --clone <src-env-name> to seed a source env."
                )
            return 0
        if args.command == "activate":
            return _handle_activate(manager, args.name)
        if args.command == "deactivate":
            return _handle_deactivate(manager)
        if args.command == "list":
            _print_env_list(manager.list_envs())
            return 0
        if args.command == "status":
            _print_status(manager.status())
            return 0
        if args.command == "remove":
            manager.remove_env(args.name)
            print(f"removed\t{args.name}")
            return 0
        if args.command == "uninstall":
            return _handle_uninstall(manager, args.restore_source, args.shell, args.rc_file)
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
    rc_status = "updated" if result.changed else "unchanged"
    print("==== Initializing packagent ====")
    print(f"shell: {result.shell_name}")
    print(f"rc_file: {result.rc_path} ({rc_status})")
    print(f"base_mode: {base_mode}")
    print(f"active_env: {activation.env_name}")
    print("==== Ready ====")
    print("Run this in your current shell:")
    print(f"  source {shlex.quote(result.rc_path)}")
    print("Or bootstrap just this shell:")
    print(f"  eval \"$(packagent shell init {shell_name})\"")
    return 0


def _resolve_base_mode(manager: PackagentManager, requested_base_mode: str | None) -> str:
    if requested_base_mode:
        return requested_base_mode
    if not sys.stdin.isatty() or not manager.base_init_prompt_needed():
        return BASE_MODE_IMPORT
    print(
        "packagent found existing unmanaged agent homes.",
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


def _handle_uninstall(
    manager: PackagentManager,
    requested_restore_source: str | None,
    requested_shell: str | None,
    rc_file: str | None,
) -> int:
    restore_source = _resolve_uninstall_restore_source(manager, requested_restore_source)
    result = manager.uninstall(restore_source)
    shell_results = _remove_shell_init_blocks(manager, requested_shell, rc_file)
    _print_uninstall_result(result)
    for shell_result in shell_results:
        rc_status = "updated" if shell_result.changed else "unchanged"
        print(f"rc_file: {shell_result.rc_path} ({rc_status})")
    print("packagent data remains at:")
    print(f"  {manager.paths.root}")
    print("Remove the executable with:")
    print("  uv tool uninstall packagent")
    print("If the current shell still shows a packagent prompt prefix, restart the shell.")
    return 0


def _resolve_uninstall_restore_source(
    manager: PackagentManager,
    requested_restore_source: str | None,
) -> str:
    base_mode = manager.uninstall_base_mode()
    if base_mode == BASE_MODE_FRESH:
        if requested_restore_source == RESTORE_SOURCE_BASE:
            raise UserFacingError("fresh-mode init can only uninstall from backup")
        return RESTORE_SOURCE_BACKUP
    if requested_restore_source:
        return requested_restore_source
    if not sys.stdin.isatty():
        raise UserFacingError(
            "import-mode uninstall requires --restore-source base or --restore-source backup",
        )
    print("packagent was initialized in import mode.", file=sys.stderr)
    print("Choose which data to restore to the default target paths:", file=sys.stderr)
    print("  base:   copy the current base environment", file=sys.stderr)
    print("  backup: restore the current init backup snapshots", file=sys.stderr)
    while True:
        print("Restore source [base/backup]: ", end="", file=sys.stderr)
        answer = input().strip().lower()
        if answer == "base":
            return RESTORE_SOURCE_BASE
        if answer == "backup":
            return RESTORE_SOURCE_BACKUP
        print("Please enter 'base' or 'backup'.", file=sys.stderr)


def _remove_shell_init_blocks(
    manager: PackagentManager,
    requested_shell: str | None,
    rc_file: str | None,
) -> list[ShellInitRemoveResult]:
    if rc_file:
        shell_name = requested_shell or detect_shell()
        return [remove_shell_init(shell_name, Path(rc_file).expanduser())]
    if requested_shell:
        return [
            remove_shell_init(
                requested_shell,
                default_rc_path(requested_shell, manager.paths.home),
            ),
        ]
    return [
        remove_shell_init(shell_name, default_rc_path(shell_name, manager.paths.home))
        for shell_name in SUPPORTED_SHELLS
    ]


def _shell_hook_active() -> bool:
    return bool(os.environ.get("PACKAGENT_SHELL_HOOK"))


def _current_shell() -> str:
    shell_name = os.environ.get("PACKAGENT_SHELL", "")
    if shell_name in SUPPORTED_SHELLS:
        return shell_name
    return detect_shell()


def _print_env_list(rows: list[dict[str, str]]) -> None:
    print("active\tname\tpath")
    for row in rows:
        print(f"{row['active']}\t{row['name']}\t{row['path']}")


def _print_status(status: StatusReport) -> None:
    print(f"active_env={status.active_env}")
    print(f"managed={str(status.managed).lower()}")
    print(f"managed_home={status.managed_home_path}")
    print(f"home_kind={status.home_kind}")
    print()
    print("target\tmanaged\tmanaged_home\thome_kind\thome_target\texpected_target")
    for target_status in status.target_statuses:
        print(
            "\t".join(
                [
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


def _print_uninstall_result(result: UninstallResult) -> None:
    print("==== Uninstalling packagent ====")
    print(f"restore_source: {result.restore_source}")
    print("target\taction\tmanaged_home\tsource")
    for target_result in result.target_results:
        source = target_result.source_path or ""
        print(
            "\t".join(
                [
                    target_result.key,
                    target_result.action,
                    target_result.managed_home_path,
                    source,
                ],
            ),
        )
    print("==== Restored ====")

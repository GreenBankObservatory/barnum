#! /usr/bin/env python


import argparse
import concurrent.futures
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from configparser import RawConfigParser
from pathlib import Path

import yaml

ENDPOINT_REGEX = re.compile(r"(\w+://)\d+\.\d+\.\d+\.\d+(:\d+)")

SCRIPT_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)


def check_all_output(cmd, dry_run=False, **kwargs):
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", True)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    cmd = [str(c) for c in cmd]
    if dry_run:
        logger.info(f"DRY RUN $ {shlex.join(cmd)}")
        return None

    try:
        logger.info(f"Executing $ {shlex.join(cmd)}")
        result = subprocess.run(cmd, **kwargs)
    except subprocess.CalledProcessError as error:
        logger.exception("Error in proc")
        raise

    return result


def get_users(path: Path):
    with open(path, encoding="utf-8") as yaml_file:
        users = yaml.load(yaml_file, Loader=yaml.Loader)

    if not users:
        raise ValueError(f"No users defined in {path}")
    return users


def _bailey(
    user_and_host,
    bailey_args=None,
    dry_run=False,
    bailey_path="bailey",
    circusctl_path="circusctl",
):
    cmd = [
        "ssh",
        # No X forwarding (probably doesn't matter)
        "-x",
        # Turn off all output (e.g. headers) except for errors
        "-o",
        "LogLevel=error",
        "-o",
        # Prevent SSH from prompting for passwords, passphrases, etc.
        "BatchMode=yes",
        user_and_host,
        bailey_path,
        "--circusctl-path",
        circusctl_path,
    ]
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        cmd.insert(7, f"PATH={venv}/bin:$PATH")

    if bailey_args is not None:
        cmd.extend(bailey_args)

    return check_all_output(cmd, check=False, dry_run=dry_run)


def barnum_multi_thread(
    user_and_host_to_config,
    bailey_args=None,
    dry_run=False,
    bailey_path="bailey",
    circusctl_path="circusctl",
):
    max_workers = (
        num_jobs
        if (num_jobs := len(user_and_host_to_config))
        < (cpu_count := os.cpu_count() * 2)
        else cpu_count
    )
    logger.debug(f"Setting max_workers to {max_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        if bailey_args is None:
            bailey_args = []

        # Start threads; create dict of future: host
        results = {
            executor.submit(
                _bailey,
                user_and_host=user_and_host,
                bailey_args=bailey_args,
                dry_run=dry_run,
                bailey_path=bailey_path,
                circusctl_path=circusctl_path,
            ): user_and_host
            for user_and_host in user_and_host_to_config
        }
        for future in concurrent.futures.as_completed(results):
            host = results[future]
            try:
                bailey_cmd = future.result()
            except Exception as exc:
                print(f"ERROR on {host}: {exc}")
                logger.exception(exc)
            else:
                if bailey_cmd:
                    print(bailey_cmd.stdout.strip())
                    if bailey_cmd.stderr:
                        print(f"{bailey_cmd.stderr}")


def barnum_single_thread(
    user_and_host_to_config,
    bailey_args=None,
    dry_run=False,
    bailey_path="bailey",
    circusctl_path="circusctl",
):
    if bailey_args is None:
        bailey_args = []
    for user_and_host in user_and_host_to_config:
        bailey_cmd = _bailey(
            user_and_host=user_and_host,
            bailey_args=bailey_args,
            dry_run=dry_run,
            bailey_path=bailey_path,
            circusctl_path=circusctl_path,
        )
        print(bailey_cmd.stdout)
        print(bailey_cmd.stderr)


def derive_config():
    config_dir = (
        Path(
            os.environ.get("APPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.environ["HOME"], ".config"),
        )
        / "barnum"
    )
    config_dir.mkdir(exist_ok=True, parents=True)
    barnum_config_path = config_dir / "barnum_config.yaml"
    if not barnum_config_path.exists():
        with open(barnum_config_path, "w") as file:
            file.write("# Add usernames here:\n# - <username1>\n# - <username1>")

        print(f"Wrote config file template to {barnum_config_path}")
    logger.info(f"Using barnum config file {barnum_config_path}")
    return barnum_config_path


def main():
    args = parse_args()
    if args.verbosity == 3:
        init_logging(logging.DEBUG)
    elif args.verbosity == 2:
        init_logging(logging.INFO)
    elif args.verbosity == 1:
        init_logging(logging.WARNING)
    elif args.verbosity == 0:
        init_logging(logging.ERROR)
    logger.debug(f"args: {args}")

    if args.config_path:
        barnum_config_path = args.config_path
    else:
        barnum_config_path = derive_config()

    if getattr(args, "user_and_host", None):
        circus_config_paths = [
            config_path
            for user_and_host in args.user_and_host
            for config_path in get_user_circus_config_paths(
                user_and_host, barnum_config_path
            )
        ]
    else:
        circus_config_paths = get_user_circus_config_paths(
            user_and_host=None, barnum_config_path=barnum_config_path
        )
    logger.info(
        f"Processing {len(circus_config_paths)} Circus config paths: {[str(p) for p in circus_config_paths]}"
    )

    user_and_host_to_config = {}
    unique_users = set()
    unique_hosts = set()
    for config_path in circus_config_paths:
        user = config_path.parent.parent.parent.name
        host = config_path.parent.name
        user_and_host_to_config[f"{user}@{host}"] = config_path
        unique_users.add(user)
        unique_hosts.add(host)

    if not user_and_host_to_config:
        raise ValueError("There are no Circus config files found on your system")

    logger.info(
        f"Processing {len(circus_config_paths)} Circus config files across {len(unique_users)} "
        f"users and {len(unique_hosts)} hosts"
    )
    if args.subcommand == "list":
        return handle_list(circus_config_paths, verbose=args.verbosity > 1)

    if args.no_threads:
        barnum_single_thread(
            user_and_host_to_config.keys(),
            bailey_args=args.bailey_args,
            dry_run=args.dry_run,
            bailey_path=args.bailey_path,
            circusctl_path=args.circusctl_path,
        )
    else:
        barnum_multi_thread(
            user_and_host_to_config.keys(),
            bailey_args=args.bailey_args,
            dry_run=args.dry_run,
            bailey_path=args.bailey_path,
            circusctl_path=args.circusctl_path,
        )


def get_user_circus_config_paths(user_and_host, barnum_config_path):
    if user_and_host in [None, "", "*"]:
        user = "*"
        host = "*"
    else:
        try:
            user, host = user_and_host.split("@")
        except ValueError as error:
            raise ValueError(
                "user/host arg must be in format user@host, '*@host', 'host@*', or '*'"
            ) from error

    if user == "*":
        users = get_users(barnum_config_path)
    else:
        users = [user]

    paths = []
    for user in users:
        base = Path("/", "users", user, "circus").glob(f"{host}/circus.ini")
        paths.extend(base)

    return paths


def parse_circus_config(path):
    cp = RawConfigParser()
    cp.read(path)
    return cp


def handle_list(circus_config_paths, verbose=False):
    for config_path in circus_config_paths:
        user = config_path.parent.parent.parent.name
        host = config_path.parent.name
        config = parse_circus_config(config_path)
        watchers = sorted([section for section in config if "watcher" in section])
        for watcher in watchers:
            line = f"{user}@{host} [{watcher}]"
            if verbose:
                line = f"{line} ({config_path})"
            print(line)


def init_logging(level):
    """Initialize logging."""
    logging.getLogger().setLevel(level)
    _logger = logging.getLogger(__name__)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[barnum] %(message)s"))
    _logger.addHandler(console_handler)
    _logger.setLevel(level)


class WideHelpFormatter(argparse.HelpFormatter):
    def __init__(self, *args, **kwargs):
        # If we can't determine terminal size, just let argparse derive it itself
        # in the super class
        width, __ = shutil.get_terminal_size(fallback=(None, None))
        if width:
            kwargs["width"] = width
        super().__init__(*args, **kwargs)

    def _format_usage(self, usage, actions, groups, prefix):
        usage = super()._format_usage(usage, actions, groups, prefix)
        usage = f"{usage.strip()} [-- BAILEY_ARG [BAILEY_ARG ...] [-- CIRCUS_ARG [CIRCUS_ARG ...]]]"
        return usage


def parse_args():
    parser = argparse.ArgumentParser(formatter_class=WideHelpFormatter)
    subparsers = parser.add_subparsers(help="Indicate status or control")
    parser.set_defaults(subcommand=None)
    user_and_host_arg = {
        "dest": "user_and_host",
        "help": "Can be EITHER user@host OR just host. In the former case, operations will "
        "affect only the circus instance for user@host. In the latter case, "
        "operations will affect ALL circus instances on host",
        "nargs": "+",
    }

    parser_status = subparsers.add_parser("status", aliases=["s"])
    parser_status.add_argument(**user_and_host_arg)
    parser_status.add_argument("--allow-missing-systemd-unit", action="store_true")
    parser_status.add_argument("-S", "--get-systemd-status", action="store_true")
    parser_status.add_argument("--short", action="store_true")
    parser_status.set_defaults(subcommand="status")

    parser_control = subparsers.add_parser("control", aliases=["c"])
    parser_control.add_argument(**user_and_host_arg)
    parser_control.add_argument(
        "circus_command",
        default="status",
        help="Circus command to pass through to each selected Circus instance. Default is 'status'",
    )
    parser_control.add_argument("--allow-missing-systemd-unit", action="store_true")
    parser_control.add_argument("-S", "--get-systemd-status", action="store_true")
    parser_control.add_argument("--short", action="store_true")
    parser_control.set_defaults(subcommand="control")

    parser_list = subparsers.add_parser("list", aliases=["l"])
    parser_list.add_argument(
        **{
            "dest": "user_and_host",
            "help": "Can be EITHER user@host OR just host. In the former case, operations will "
            "affect only the circus instance for user@host. In the latter case, "
            "operations will affect ALL circus instances on host",
            "nargs": "*",
        }
    )
    parser_list.set_defaults(subcommand="list")

    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--bailey-path", default="bailey")
    parser.add_argument("--circusctl-path", default="circusctl")
    parser.add_argument(
        "-v",
        "--verbosity",
        type=int,
        choices=[0, 1, 2, 3],
        help="Set verbosity of output. 1 (default) will show standard output. 0 does nothing. "
        "2 shows info-level logging; 3 shows debug-level logging",
        default=1,
    )
    parser.add_argument(
        "-D", "--dry-run", action="store_true", help="Don't make any changes"
    )

    parser.add_argument("--no-colors", action="store_true", help="No colors")
    parser.add_argument(
        "--no-threads", action="store_true", help="Don't use threads for SSH'ing"
    )

    # argparse doesn't seem to be able to handle this natively, so we manually
    # alter sys.argv before argparse sees it in order to pull out all of the
    # circus arguments
    try:
        index = sys.argv.index("--")
        sys.argv, bailey_args = sys.argv[:index], sys.argv[index + 1 :]
    except ValueError:
        bailey_args = []

    parsed_args = parser.parse_args()
    if getattr(parsed_args, "circus_command", None):
        bailey_args = [parsed_args.circus_command, *bailey_args]

    if parsed_args.verbosity:
        bailey_args = ["--verbosity", parsed_args.verbosity, *bailey_args]

    if getattr(parsed_args, "allow_missing_systemd_unit", None):
        bailey_args = ["--allow-missing-systemd-unit", *bailey_args]

    if getattr(parsed_args, "get_systemd_status", None):
        bailey_args = ["--get-systemd-status", *bailey_args]

    if getattr(parsed_args, "short", None):
        bailey_args = ["--short", *bailey_args]

    if not parsed_args.no_colors:
        bailey_args = ["--force-colors", *bailey_args]

    parsed_args.bailey_args = bailey_args
    return parsed_args


if __name__ == "__main__":
    main()

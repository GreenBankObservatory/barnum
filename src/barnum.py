#! /usr/bin/env python


import argparse
import concurrent.futures
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import yaml

ENDPOINT_REGEX = re.compile(r"(\w+://)\d+\.\d+\.\d+\.\d+(:\d+)")

SCRIPT_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)


def check_output(*args, verbose=False, **kwargs):
    if "universal_newlines" not in kwargs:
        kwargs["universal_newlines"] = True
    if "check" not in kwargs:
        kwargs["check"] = True

    try:
        return subprocess.run(*args, **kwargs, stdout=subprocess.PIPE).stdout
    except subprocess.CalledProcessError as error:
        if verbose:
            raise
        else:
            logger.error(error)
            sys.exit(1)


def get_users(path: Path):
    with open(path) as yaml_file:
        users = yaml.load(yaml_file, Loader=yaml.Loader)

    if not users:
        users = []
    return users


def _barnum(host, user=None, bailey_args=None, dry_run=False, bailey_cmd="bailey"):
    if user:
        logger.debug(f"Processing {user}@{host}")
    else:
        logger.debug(f"Processing {host}")

    if host != socket.gethostname():
        cmd = [
            "ssh",
            "-x",
            "-o",
            "LogLevel=error",
            host,
            f"PATH={os.environ.get('VIRTUAL_ENV')}/bin:$PATH",
            bailey_cmd,
        ]
    else:
        cmd = [bailey_cmd]

    if user:
        cmd.append(user)

    if bailey_args is not None:
        cmd.extend(bailey_args)

    if dry_run:
        return f"DRY RUN; would execute: {' '.join(cmd)}"
    else:
        logger.debug(f"bailey cmd: {' '.join(cmd)}")
        return check_output(cmd, verbose="--verbose" in bailey_args)


def barnum_multi_thread(hosts, bailey_args=None, dry_run=False, bailey_cmd="bailey"):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        # Start threads; create dict of future: host
        if bailey_args is None:
            bailey_args = []
        results = {
            executor.submit(
                _barnum,
                host,
                bailey_args=bailey_args,
                dry_run=dry_run,
                bailey_cmd=bailey_cmd,
            ): host
            for host in hosts
        }
        for future in concurrent.futures.as_completed(results):
            host = results[future]
            try:
                data = future.result()
            except Exception as exc:
                print(f"ERROR on {host}: {exc}")
            else:
                print(data)


def barnum_single_thread(hosts, bailey_args=None, dry_run=False, bailey_cmd="bailey"):
    for host in hosts:
        print(
            _barnum(
                host,
                bailey_args=bailey_args,
                dry_run=dry_run,
                bailey_cmd=bailey_cmd,
            )
        )


def main():
    args = parse_args()
    if args.verbose:
        init_logging(logging.DEBUG)
    else:
        init_logging(logging.INFO)
    logger.debug(f"args: {args}")

    if args.config_path:
        config_path = args.config_path
    else:
        config_dir = (
            Path(
                os.environ.get("APPDATA")
                or os.environ.get("XDG_CONFIG_HOME")
                or os.path.join(os.environ["HOME"], ".config"),
            )
            / "barnum"
        )
        config_dir.mkdir(exist_ok=True, parents=True)
        config_path = config_dir / "barnum_config.yaml"
        if not config_path.exists():
            with open(config_path, "w") as file:
                file.write("# Add usernames here:\n# - <username1>\n# - <username1>")

            logger.debug(f"Wrote config file template to {config_path}")

    if args.user_and_host:
        try:
            user, host = args.user_and_host.split("@")
        except ValueError:
            user, host = None, args.user_and_host
        output = _barnum(
            host=host,
            user=user,
            bailey_args=args.bailey_args,
            dry_run=args.dry_run,
            bailey_cmd=args.bailey_cmd,
        )
        print("---")
        print(output)
    else:
        users = get_users(config_path)
        if not users:
            logger.error(f"You must provide at least one username in {config_path}")
            sys.exit(1)
        config_paths = get_user_circus_ini_paths(users)
        hosts = get_unique_systemd_hosts(config_paths)
        logger.debug(f"Circus is configured on the following hosts: {', '.join(hosts)}")
        if args.no_threads:
            barnum_single_thread(
                hosts,
                bailey_args=args.bailey_args,
                dry_run=args.dry_run,
                bailey_cmd=args.bailey_cmd,
            )
        else:
            barnum_multi_thread(
                hosts,
                bailey_args=args.bailey_args,
                dry_run=args.dry_run,
                bailey_cmd=args.bailey_cmd,
            )


def get_unique_systemd_hosts(config_file_paths):
    return set(path.parent.name for path in config_file_paths)


def get_user_circus_ini_paths(users):
    paths = []
    for user in users:
        base = Path("/", "users", user, "circus").glob("*/circus.ini")
        paths.extend(base)

    return paths


def init_logging(level):
    """Initialize logging."""
    logging.getLogger().setLevel(level)
    _logger = logging.getLogger(__name__)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("barnum: %(message)s"))
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
    parser.add_argument(
        "user_and_host",
        nargs="?",
        help="Can be EITHER user@host OR just host. In the former case, operations will "
        "affect only the circus instance for user@host. In the latter case, "
        "operations will affect ALL circus instances on host",
    )
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--bailey-cmd", default="bailey")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase verbosity"
    )
    parser.add_argument(
        "-D", "--dry-run", action="store_true", help="Don't make any changes"
    )
    parser.add_argument(
        "--no-threads", action="store_true", help="Don't use threads for SSH'ing"
    )
    parser.add_argument(
        "-C", "--circus-cmd", help="Specify the positional argument to send to circus"
    )
    parser.add_argument("--no-colors", action="store_true", help="No colors")

    # argparse doesn't seem to be able to handle this natively, so we manually
    # alter sys.argv before argparse sees it in order to pull out all of the
    # circus arguments
    try:
        index = sys.argv.index("--")
        sys.argv, bailey_args = sys.argv[:index], sys.argv[index + 1 :]
    except ValueError:
        bailey_args = []

    parsed_args = parser.parse_args()

    if parsed_args.verbose:
        bailey_args = ["--verbose", *bailey_args]

    if not parsed_args.no_colors:
        bailey_args = ["--force-colors", *bailey_args]

    if parsed_args.circus_cmd:
        bailey_args = ["--circus-cmd", parsed_args.circus_cmd, *bailey_args]

    parsed_args.bailey_args = bailey_args
    return parsed_args


if __name__ == "__main__":
    main()

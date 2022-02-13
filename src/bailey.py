#! /usr/bin/env python

"""Manage circus instances on a given host."""

import argparse
import configparser
import logging
import shlex
import shutil
import socket
import sys
import urllib.parse
from pathlib import Path
from typing import Tuple

from colorama import Fore, Style
from colorama import init as init_colorama

from barnum import check_output

logger = logging.getLogger(__name__)

HOST = socket.gethostname()


def _circus(circus_config_path, circus_args=None, dry_run=False):
    if not circus_args:
        circus_args = ["status"]

    circus_parser = configparser.ConfigParser(strict=False)
    circus_parser.read(circus_config_path)
    endpoint = circus_parser["circus"]["endpoint"]
    if "0.0.0.0" in endpoint:
        logger.debug("Converting 0.0.0.0 to explicit host reference")
        pr = urllib.parse.urlparse(endpoint)
        __, port = pr.netloc.split(":")
        new_endpoint = pr._replace(netloc=f"{HOST}:{port}").geturl()
        logger.debug(f"Converted {endpoint!r} to {new_endpoint!r}")
        endpoint = new_endpoint

    circus_cmd = ["circusctl", "--endpoint", endpoint, *circus_args]
    if dry_run:
        return f"DRY RUN; would execute: {' '.join(circus_cmd)}"
    else:
        logger.debug(f"circus cmd: {shlex.join(circus_cmd)}")
        return check_output(circus_cmd)


def handle_user(user, circus_args=None, dry_run=False):
    circus_config_path = Path("/", "users", user, "circus", HOST, "circus.ini")
    if not circus_config_path.exists():
        raise ValueError(f"Circus config path {circus_config_path} does not exist!")
    circus_result = _circus(
        circus_config_path, circus_args=circus_args, dry_run=dry_run
    )
    print(circus_result)


def handle_host(circus_args=None, dry_run=False):
    print(f"{Style.BRIGHT}{Fore.BLUE}--- {HOST.upper()} ---{Style.RESET_ALL}")
    # TODO: Remove circus-beta.service and circus-prod.service on gboweb
    circus_unit_files_str = check_output(["systemctl", "list-unit-files", "circus_*"])
    circus_unit_files: Tuple(str, str)
    circus_unit_files = [
        line.split()
        for line in circus_unit_files_str.split("\n")
        if line.startswith("circus_")
    ]
    circus_unit_file_paths = [
        (Path("/etc/systemd/system", name), enabled)
        for name, enabled in circus_unit_files
    ]
    table_data = []
    for unit_path, enabled in circus_unit_file_paths:
        status = check_output(["systemctl", "status", unit_path.name], check=False)
        status_lines = status.split("\n")
        for line in status_lines:
            if "Active" in line:
                status = " ".join(line.split(":")[1].strip().split()[:2])
                table_data.append((unit_path, enabled, status))

    for unit_path, enabled, status in table_data:
        assert unit_path.exists()
        print("-" * 80)
        systemctl_summary = "\t".join([unit_path.name, enabled, status])
        if status.startswith("active"):
            unit_parser = configparser.ConfigParser()
            unit_parser.read(unit_path)
            try:
                circus_user = unit_parser["Service"]["user"]
            except KeyError:
                logger.exception(f"Failed to parse user from {unit_path}")
                raise
            else:
                logger.debug(f"Derived circus user {circus_user} from {unit_path}")
                circus_config_path = Path(
                    "/", "users", circus_user, "circus", HOST, "circus.ini"
                )

                if not circus_config_path.exists():
                    raise ValueError(f"{circus_config_path} does not exist!")
                circus_result = _circus(
                    circus_config_path, circus_args=circus_args, dry_run=dry_run
                )
                print(f"{Fore.GREEN}{systemctl_summary}{Style.RESET_ALL}")
                print("  " + "\n  ".join(circus_result.splitlines()))
        else:
            print(f"{Fore.RED}{systemctl_summary}{Style.RESET_ALL}")
            print(f"{Fore.RED}  No circus expected{Style.RESET_ALL}")

    # print(tabulate(table_data))
    print("=" * 80)


def init_logging(level):
    """Initialize logging."""
    logging.getLogger().setLevel(level)
    _logger = logging.getLogger(__name__)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("bailey: %(message)s"))
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
        usage = f"{usage.strip()} [-- CIRCUS_ARG [CIRCUS_ARG ...]]"
        return usage


def parse_args():
    parser = argparse.ArgumentParser(formatter_class=WideHelpFormatter)
    parser.add_argument("user", nargs="?")
    parser.add_argument(
        "-D", "--dry-run", action="store_true", help="Don't make any changes"
    )
    parser.add_argument("--force-colors", action="store_true", help="No colors")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase verbosity"
    )
    parser.add_argument(
        "-C", "--circus-cmd", help="Specify the positional argument to send to circus"
    )
    # argparse doesn't seem to be able to handle this natively, so we manually
    # alter sys.argv before argparse sees it in order to pull out all of the
    # circus arguments
    try:
        index = sys.argv.index("--")
        sys.argv, circus_args = sys.argv[:index], sys.argv[index + 1 :]
    except ValueError:
        circus_args = None

    parsed_args = parser.parse_args()
    if circus_args:
        if parsed_args.circus_cmd:
            parser.error("Cannot give both --circus-cmd and direct circus args")
        parsed_args.circus_args = circus_args
    elif parsed_args.circus_cmd:
        parsed_args.circus_args = [parsed_args.circus_cmd]
    else:
        parsed_args.circus_args = None
    return parsed_args


def main():
    args = parse_args()
    if args.verbose:
        init_logging(logging.DEBUG)
    else:
        init_logging(logging.INFO)

    init_colorama(strip=not args.force_colors)

    if args.user:
        handle_user(user=args.user, circus_args=args.circus_args, dry_run=args.dry_run)
    else:
        handle_host(circus_args=args.circus_args, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

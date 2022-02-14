#! /usr/bin/env python

"""Manage circus instances on a given host."""

import argparse
import configparser
import logging
import math
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.parse
from collections import Counter
from getpass import getuser
from pathlib import Path

from colorama import Fore, Style
from colorama import init as init_colorama

from barnum import check_all_output

LOADED_REGEX = re.compile(r"Loaded:.*\((.*\.service)")

logger = logging.getLogger(__name__)

USER = getuser()
HOST = socket.gethostname()


def _circus(
    circus_config_path, circus_args=None, dry_run=False, circusctl_path="circusctl"
):
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

    circus_cmd = [circusctl_path, "--endpoint", endpoint, *circus_args]
    return check_all_output(circus_cmd, check=False, dry_run=dry_run)


def handle_status_verbose(
    circus_args=None,
    dry_run=False,
    allow_missing_systemd_unit=False,
    verbose=False,
    circusctl_path="circusctl",
):
    pass


def handle_systemd_status(verbose=False, dry_run=False, short=False):
    status_string_parts = []
    systemd_unit_name = f"circus_{USER}_{HOST}"
    systemd_unit_found = False
    try:
        systemctl_status_cmd = check_all_output(
            ["systemctl", "status", systemd_unit_name],
            text=True,
            check=False,
            dry_run=dry_run,
        )
    except subprocess.CalledProcessError as error:
        status_string_parts.append(
            f"{Fore.RED}  No Systemd Unit found with name {systemd_unit_name}{Style.RESET_ALL}"
        )
        status_string_parts.append(f"{error=}")
    else:
        if systemctl_status_cmd.stdout:
            systemd_unit_found = True
            systemd_unit_active = True
            unit_path = None
            unit_status = None
            for line in systemctl_status_cmd.stdout.splitlines():
                if "Loaded" in line:
                    unit_path = LOADED_REGEX.search(line).groups()[0]
                if "Active" in line:
                    if "inactive" in line:
                        systemd_unit_active = False
                    unit_status = line.split(": ")[1]
                    break

            if unit_path is None or unit_status is None:
                raise AssertionError("hmmm")

            systemd_status_color = Fore.GREEN if systemd_unit_active else Fore.RED
            if not short:
                status_string_parts.append(
                    f"{systemd_status_color}Systemd Status{Style.RESET_ALL}"
                )
                status_string_parts.append(f"  {unit_path}; {unit_status}")
            else:
                status_string_parts.append(
                    f"{systemd_status_color}Systemd Status: {' '.join(unit_status.split(' ')[:2])}{Style.RESET_ALL}"
                )
        else:
            # Probably no service file
            status_string_parts.append(f"{Fore.RED}Systemd Status{Style.RESET_ALL}")
            status_string_parts.append(
                f"  {Fore.RED}{systemctl_status_cmd.stderr.strip()}{Style.RESET_ALL}"
            )
    return status_string_parts, systemd_unit_found


def handle_circus_status(
    circusctl_path="circusctl",
    circus_args=None,
    verbose=False,
    dry_run=False,
    short=False,
):
    status_string_parts = []
    circus_config_path = Path("/", "users", USER, "circus", HOST, "circus.ini")
    circus_cmd = _circus(
        circus_config_path,
        circus_args=circus_args,
        dry_run=dry_run,
        circusctl_path=circusctl_path,
    )
    if circus_cmd.returncode == 0:
        if circus_cmd.stdout.strip():
            if not short:
                status_string_parts.append(
                    f"{Fore.GREEN}Circus Status{Style.RESET_ALL}"
                )
                status_string_parts.append(
                    "  " + "\n  ".join(circus_cmd.stdout.splitlines())
                )
            else:
                status_counts = Counter(
                    (line.split(": ")[1] for line in circus_cmd.stdout.splitlines())
                )
                if all(status == "active" for status in status_counts):
                    color = Fore.GREEN
                elif any(status == "error" for status in status_counts):
                    color = Fore.RED
                else:
                    color = Fore.YELLOW
                status_summary = []
                for status, count in status_counts.items():
                    if status == "active":
                        color = Fore.GREEN
                    elif status == "error":
                        color = Fore.RED
                    else:
                        color = Fore.YELLOW

                    status_summary.append(f"{color}{status}: {count}{Style.RESET_ALL}")
                status_string_parts.append(
                    f"{color}Circus Status{Style.RESET_ALL}: {'; '.join(status_summary)}"
                )

        else:
            status_string_parts.append(
                f"  {Fore.RED}No Circus Watchers found{Style.RESET_ALL}"
            )
    else:
        status_string_parts.append(f"{Fore.RED}Circus Watchers: ERROR{Style.RESET_ALL}")
        circus_pgrep_cmd = check_all_output(
            ["pgrep", "-alf", "-u", USER, "circusd"], text=True, check=False
        )
        if circus_pgrep_cmd.returncode == 0:
            status_string_parts.append(
                f"  {Fore.YELLOW}Circusd instances for {USER}@{HOST}{Style.RESET_ALL}"
            )
            for line in circus_pgrep_cmd.stderr.splitlines():
                status_string_parts.append(f"  {line}")
        else:
            status_string_parts.append(
                f"  {Fore.RED}No Circusd instances found for {USER}@{HOST}{Style.RESET_ALL}"
            )

        if circus_cmd.stderr:
            for line in circus_pgrep_cmd.stderr.splitlines():
                status_string_parts.append(f"  {line}")

    return status_string_parts


def handle_status(
    circus_args=None,
    dry_run=False,
    allow_missing_systemd_unit=False,
    verbose=False,
    circusctl_path="circusctl",
    get_systemd_status=False,
    short=False,
):
    status_string_parts = []
    if verbose:
        width, __ = shutil.get_terminal_size()
        section_name = f"Circus status for {USER}@{HOST}"
        buffer = "*" * (math.ceil((width - len(section_name)) / 2) - 2)
        header = f"{buffer} {Fore.BLUE}{section_name}{Style.RESET_ALL} {'' if len(section_name) % 2 == 0 else ' '}{buffer}"
        status_string_parts.append(header)

    systemd_status_parts, systemd_unit_found = handle_systemd_status(
        verbose=verbose, short=short
    )
    status_string_parts.extend(systemd_status_parts)
    if allow_missing_systemd_unit or systemd_unit_found:
        circus_status_parts = handle_circus_status(
            circusctl_path=circusctl_path,
            circus_args=circus_args,
            verbose=verbose,
            dry_run=dry_run,
            short=short,
        )
        status_string_parts.extend(circus_status_parts)
    if not short:
        return "\n".join(status_string_parts)
    else:
        if get_systemd_status:
            concise_status = "; ".join(status_string_parts)
        else:
            concise_status = "; ".join(circus_status_parts)
        return f"{USER}@{HOST}: {concise_status}"


def init_logging(level):
    """Initialize logging."""
    logging.getLogger().setLevel(level)
    _logger = logging.getLogger(__name__)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[bailey] %(message)s"))
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

    parser.add_argument("circus_cmd", nargs="?", default="status")
    parser.add_argument(
        "-D", "--dry-run", action="store_true", help="Don't make any changes"
    )
    parser.add_argument("--circusctl-path", default="circusctl")
    parser.add_argument("--force-colors", action="store_true", help="No colors")
    parser.add_argument("--short", action="store_true", help="Short format output")
    parser.add_argument(
        "-S",
        "--get-systemd-status",
        action="store_true",
        help="Get Systemd status, even in concise mode",
    )
    parser.add_argument(
        "-v",
        "--verbosity",
        type=int,
        choices=[0, 1, 2, 3],
        help="Set verbosity of output. 1 (default) will show standard output. 0 does nothing. "
        "2 shows info-level logging; 3 shows debug-level logging",
        default=1,
    )
    parser.add_argument("--allow-missing-systemd-unit", action="store_true")
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
        parsed_args.circus_args = circus_args
    else:
        parsed_args.circus_args = []

    #     if parsed_args.circus_cmd:
    #         parser.error("Cannot give both --circus-cmd and direct circus args")
    #     parsed_args.circus_args = circus_args
    # elif parsed_args.circus_cmd:
    #     parsed_args.circus_args = [parsed_args.circus_cmd]
    # else:
    #     parsed_args.circus_args = None
    return parsed_args


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

    init_colorama(strip=not args.force_colors)
    if args.circus_cmd == "status":
        print(
            handle_status(
                circus_args=args.circus_args,
                dry_run=args.dry_run,
                allow_missing_systemd_unit=args.allow_missing_systemd_unit,
                verbose=args.verbosity > 1,
                circusctl_path=args.circusctl_path,
                get_systemd_status=args.get_systemd_status,
                short=args.short,
            )
        )
    else:
        circus_config_path = Path("/", "users", USER, "circus", HOST, "circus.ini")
        circusctl_cmd = _circus(
            circus_config_path,
            circus_args=[args.circus_cmd, *args.circus_args],
            dry_run=args.dry_run,
            circusctl_path=args.circusctl_path,
        )
        if args.verbosity > 1:
            circus_cmd_str = f"$ {shlex.join(circusctl_cmd.args)}"
        else:
            circus_cmd_str = args.circus_cmd
        if circusctl_cmd.returncode == 0:

            print(
                f"{Fore.GREEN}{USER}@{HOST}: Circus command '{circus_cmd_str}': SUCCESS{Style.RESET_ALL}"
            )
            for line in circusctl_cmd.stdout.splitlines():
                print(f"  {line}")
        else:
            print(
                f"{Fore.RED}{USER}@{HOST}: Circus command '{circus_cmd_str}': FAILED{Style.RESET_ALL}"
            )
            print(circusctl_cmd.stderr, file=sys.stderr)


if __name__ == "__main__":
    main()

#! /usr/bin/env python
# -*- coding: utf-8 -*-


import argparse
import configparser
from pathlib import Path
import yaml
import shutil
import logging
import subprocess
from typing import Tuple
import re
import socket
import sys

import concurrent.futures

from pathlib import Path
from tabulate import tabulate

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
        return yaml.load(yaml_file, Loader=yaml.Loader)


def _barnum(host, user=None, bailey_args=None, verbose=False, dry_run=False):
    if user:
        logger.debug(f"Processing {user}@{host}")
    else:
        logger.debug(f"Processing {host}")

    if host != socket.gethostname():
        cmd = ["ssh", "-o", "LogLevel=error", host, str(SCRIPT_DIR / "bailey")]
    else:
        cmd = [str(SCRIPT_DIR / "bailey")]

    if user:
        cmd.append(user)

    if verbose:
        cmd.append("--verbose")

    if bailey_args:
        cmd.extend(bailey_args)

    if dry_run:
        return f"DRY RUN; would execute: {' '.join(cmd)}"
    else:
        logger.debug(f"bailey cmd: {' '.join(cmd)}")
        return check_output(cmd, verbose=verbose)


def barnum_multi_thread(hosts, bailey_args=None, verbose=None, dry_run=False):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        # Start threads; create dict of future: host
        if bailey_args is None:
            bailey_args = []
        results = {
            executor.submit(
                _barnum, host, bailey_args=bailey_args, verbose=verbose, dry_run=dry_run
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


def barnum_single_thread(hosts, bailey_args=None, verbose=None, dry_run=False):
    for host in hosts:
        print(_barnum(host, bailey_args=bailey_args, verbose=verbose, dry_run=dry_run))


def main():
    args = parse_args()
    if args.verbose:
        init_logging(logging.DEBUG)
    else:
        init_logging(logging.INFO)
    logger.debug(f"args: {args}")

    if args.user_and_host:
        try:
            user, host = args.user_and_host.split("@")
        except ValueError:
            user, host = None, args.user_and_host
        output = _barnum(
            host=host,
            user=user,
            bailey_args=args.bailey_args,
            verbose=args.verbose,
            dry_run=args.dry_run,
        )
        print("---")
        print(output)
    else:
        users = get_users(args.config_path)
        config_paths = get_config_paths(users)
        hosts = get_unique_systemd_hosts(config_paths)
        logger.debug(f"Circus is configured on the following hosts: {', '.join(hosts)}")
        if args.no_threads:
            barnum_single_thread(
                hosts,
                bailey_args=args.bailey_args,
                verbose=args.verbose,
                dry_run=args.dry_run,
            )
        else:
            barnum_multi_thread(
                hosts,
                bailey_args=args.bailey_args,
                verbose=args.verbose,
                dry_run=args.dry_run,
            )


def get_unique_systemd_hosts(config_file_paths):
    return set(path.parent.name for path in config_file_paths)


def get_config_paths(users):
    paths = []
    for user in users:
        base = Path("/", "users", user, "circus").glob("*/circus.ini")
        paths.extend(base)

    return paths


def init_logging(level):
    """Initialize logging"""
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


class ExtraArgumentParser(argparse.ArgumentParser):
    def format_usage(self):
        usage = super().format_usage()
        usage = f"{usage.strip()} [-- BAILEY_ARG [BAILEY_ARG ...] [-- CIRCUS_ARG [CIRCUS_ARG ...]]]"
        return usage


def parse_args():
    parser = ExtraArgumentParser(formatter_class=WideHelpFormatter)
    parser.add_argument(
        "user_and_host",
        nargs="?",
        help="Can be EITHER user@host OR just host. In the former case, operations will "
        "affect only the circus instance for user@host. In the latter case, "
        "operations will affect ALL circus instances on host",
    )
    parser.add_argument(
        "--config-path", type=Path, default=SCRIPT_DIR / "circus_users.yaml"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Increase verbosity"
    )
    parser.add_argument(
        "-D", "--dry-run", action="store_true", help="Don't make any changes"
    )
    parser.add_argument(
        "--no-threads", action="store_true", help="Don't use threads for SSH'ing"
    )

    # argparse doesn't seem to be able to handle this natively, so we manually
    # alter sys.argv before argparse sees it in order to pull out all of the
    # circus arguments
    try:
        index = sys.argv.index("--")
        sys.argv, bailey_args = sys.argv[:index], sys.argv[index + 1:]
    except ValueError:
        bailey_args = None

    parsed_args = parser.parse_known_intermixed_args()[0]
    if bailey_args:
        parsed_args.bailey_args = bailey_args
    else:
        parsed_args.bailey_args = None

    return parsed_args


if __name__ == "__main__":
    main()

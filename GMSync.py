import argparse
import logging
import os
import sys
from os.path import abspath, join

from dotenv import dotenv_values, find_dotenv
from dotenv.main import set_key

from helpers.ConfigHelper import Config
from helpers.DatabaseHelper import Database
from helpers.Exceptions import ConfigError
from helpers.GoogleHelper import Google
from helpers.MonicaHelper import Monica
from helpers.SyncHelper import Sync

VERSION = "v4.0.0"
LOG_FOLDER = "logs"
LOG_FILENAME = "sync.log"
DEFAULT_CONFIG_FILEPATH = join("helpers", ".env.default")
# Google -> Monica contact syncing script
# Make sure you installed all requirements using 'pip install -r requirements.txt'


def main() -> None:
    try:
        # Setup argument parser
        parser = argparse.ArgumentParser(description='Syncs Google contacts to a Monica instance.')
        parser.add_argument('-i', '--initial', action='store_true',
                            required=False, help="build the syncing database and do a full sync")
        parser.add_argument('-sb', '--syncback', action='store_true',
                            required=False, help="sync new Monica contacts back to Google. "
                                                 "Can be combined with other arguments")
        parser.add_argument('-d', '--delta', action='store_true',
                            required=False,
                            help="do a delta sync of new or changed Google contacts")
        parser.add_argument('-f', '--full', action='store_true',
                            required=False,
                            help="do a full sync and request a new delta sync token")
        parser.add_argument('-c', '--check', action='store_true',
                            required=False,
                            help="check database consistency and report all errors. "
                            "Can be combined with other arguments")
        parser.add_argument('-e', '--env-file', type=str, required=False,
                            help="custom path to your .env config file")
        parser.add_argument('-u', '--update', action='store_true',
                            required=False,
                            help="Updates the environment files from 3.x to v4.x scheme")

        # Parse arguments
        args = parser.parse_args()

        # Set logging configuration
        if not os.path.exists(LOG_FOLDER):
            os.makedirs(LOG_FOLDER)
        log = logging.getLogger("GMSync")
        dotenv_log = logging.getLogger("dotenv.main")
        log.setLevel(logging.INFO)
        logging_format = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        log_filepath = join(LOG_FOLDER, LOG_FILENAME)
        handler = logging.FileHandler(filename=log_filepath, mode='a', encoding="utf8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging_format)
        log.addHandler(handler)
        dotenv_log.addHandler(handler)
        log.info(f"Script started ({VERSION})")

        # Convert environment if requested
        if args.update:
            update_environment(log)

        # Load raw config
        default_config = find_dotenv(DEFAULT_CONFIG_FILEPATH, raise_error_if_not_found=True)
        log.info(f"Loading default config from {default_config}")
        default_config_values = dotenv_values(default_config)
        if args.env_file:
            if not os.path.exists(args.env_file):
                raise ConfigError("Could not find the custom user config file, check your input!")
            # Use config from custom path
            user_config = abspath(args.env_file)
        else:
            # Search config path
            user_config = find_dotenv()
        if user_config:
            # Load user config from file
            log.info(f"Loading user config from {user_config}")
            user_config_values = dotenv_values(user_config)
        else:
            # Load user config from environment vars
            log.info("Loading user config from os environment")
            user_config_values = dict(os.environ)
        raw_config = {
            **default_config_values,
            **user_config_values
        }
        log.info("Config loading complete")

        # Parse config
        conf = Config(log, raw_config)
        log.info("Config successfully parsed")

        # Create sync object
        database = Database(log, abspath(conf.DATABASE_FILE))
        google = Google(log, database, abspath(conf.GOOGLE_CREDENTIALS_FILE),
                        abspath(conf.GOOGLE_TOKEN_FILE),
                        conf.GOOGLE_LABELS_INCLUDE, conf.GOOGLE_LABELS_EXCLUDE)
        monica = Monica(log, database, conf.TOKEN, conf.BASE_URL, conf.CREATE_REMINDERS,
                        conf.MONICA_LABELS_INCLUDE, conf.MONICA_LABELS_EXCLUDE)
        sync = Sync(log, database, monica, google, args.syncback, args.check,
                    conf.DELETE_ON_SYNC, conf.STREET_REVERSAL, conf.FIELDS)

        # Print chosen sync arguments (optional ones first)
        print("\nYour choice (unordered):")
        if args.syncback:
            print("- sync back")
        if args.check:
            print("- database check")

        # Start
        if args.initial:
            # Start initial sync
            print("- initial sync\n")
            sync.start_sync('initial')
        elif args.delta:
            # Start initial sync
            print("- delta sync\n")
            sync.start_sync('delta')
        elif args.full:
            # Start initial sync
            print("- full sync\n")
            sync.start_sync('full')
        elif args.syncback:
            # Start sync back from Monica to Google
            print("")
            sync.start_sync('syncBack')
        elif args.check:
            # Start database error check
            print("")
            sync.check_database()
        elif not args.update:
            # Wrong arguments
            print("Unknown sync arguments, check your input!\n")
            parser.print_help()
            sys.exit(2)

        # Its over now
        log.info("Script ended\n")

    except Exception as e:
        log.exception(e)
        log.info("Script aborted")
        print(f"\nScript aborted: {type(e).__name__}: {str(e)}")
        print(f"See log file ({log_filepath}) for all details")
        raise SystemExit(1) from e


def update_environment(log: logging.Logger):
    """Updates the config and other environment files to work with v.4.x"""
    log.info("Start updating environment")

    # Make 'data' folder
    if not os.path.exists("data"):
        os.makedirs("data")
        msg = "'data' folder created"
        log.info(msg)
        print(msg)

    # Convert config to '.env' file
    ENV_FILE = ".env"
    open(ENV_FILE, 'w').close()
    from conf import (BASE_URL, CREATE_REMINDERS, DELETE_ON_SYNC, FIELDS,
                      GOOGLE_LABELS, MONICA_LABELS, STREET_REVERSAL, TOKEN)
    set_key(ENV_FILE, "TOKEN", TOKEN)
    set_key(ENV_FILE, "BASE_URL", BASE_URL)
    set_key(ENV_FILE, "CREATE_REMINDERS", str(CREATE_REMINDERS))
    set_key(ENV_FILE, "DELETE_ON_SYNC", str(DELETE_ON_SYNC))
    set_key(ENV_FILE, "STREET_REVERSAL", str(STREET_REVERSAL))
    set_key(ENV_FILE, "FIELDS", ",".join([field for field, isTrue in FIELDS.items() if isTrue]))
    set_key(ENV_FILE, "GOOGLE_LABELS_INCLUDE", ",".join(GOOGLE_LABELS["include"]))
    set_key(ENV_FILE, "GOOGLE_LABELS_EXCLUDE", ",".join(GOOGLE_LABELS["exclude"]))
    set_key(ENV_FILE, "MONICA_LABELS_INCLUDE", ",".join(MONICA_LABELS["include"]))
    set_key(ENV_FILE, "MONICA_LABELS_EXCLUDE", ",".join(MONICA_LABELS["exclude"]))
    msg = "'.env' file created, old 'conf.py' can be deleted now"
    log.info(msg)
    print(msg)

    # Move token, credentials and database inside new 'data' folder
    files = ["syncState.db", "token.pickle", "credentials.json"]
    for filename in files:
        try:
            os.rename(filename, f"data/{filename}")
            msg = f"'{filename}' moved to 'data/{filename}'"
            log.info(msg)
            print(msg)
        except FileNotFoundError:
            msg = f"Could not move {filename}, file not found!"
            print(msg)
            log.warning(msg)

    # Finished
    log.info("Finished updating environment")


if __name__ == '__main__':
    main()

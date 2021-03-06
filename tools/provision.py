#!/usr/bin/env python
from __future__ import print_function
import os
import sys
import logging
import platform
import subprocess

os.environ["PYTHONUNBUFFERED"] = "y"

PY2 = sys.version_info[0] == 2

ZULIP_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(ZULIP_PATH)
from scripts.lib.zulip_tools import run, subprocess_text_output, OKBLUE, ENDC, WARNING
from scripts.lib.setup_venv import setup_virtualenv, VENV_DEPENDENCIES
from scripts.lib.node_cache import setup_node_modules


SUPPORTED_PLATFORMS = {
    "Ubuntu": [
        "trusty",
        "xenial",
    ],
}

PY2_VENV_PATH = "/srv/zulip-venv"
PY3_VENV_PATH = "/srv/zulip-py3-venv"
VAR_DIR_PATH = os.path.join(ZULIP_PATH, 'var')
LOG_DIR_PATH = os.path.join(VAR_DIR_PATH, 'log')
UPLOAD_DIR_PATH = os.path.join(VAR_DIR_PATH, 'uploads')
TEST_UPLOAD_DIR_PATH = os.path.join(VAR_DIR_PATH, 'test_uploads')
COVERAGE_DIR_PATH = os.path.join(VAR_DIR_PATH, 'coverage')
LINECOVERAGE_DIR_PATH = os.path.join(VAR_DIR_PATH, 'linecoverage-report')
NODE_TEST_COVERAGE_DIR_PATH = os.path.join(VAR_DIR_PATH, 'node-coverage')

if PY2:
    VENV_PATH = PY2_VENV_PATH
else:
    VENV_PATH = PY3_VENV_PATH

TRAVIS = "--travis" in sys.argv
PRODUCTION_TRAVIS = "--production-travis" in sys.argv

if not os.path.exists(os.path.join(ZULIP_PATH, ".git")):
    print("Error: No Zulip git repository present!")
    print("To setup the Zulip development environment, you should clone the code")
    print("from GitHub, rather than using a Zulip production release tarball.")
    sys.exit(1)

if platform.architecture()[0] == '64bit':
    arch = 'amd64'
elif platform.architecture()[0] == '32bit':
    arch = "i386"
else:
    logging.critical("Only x86 is supported; ping zulip-devel@googlegroups.com if you want another architecture.")
    sys.exit(1)

# Ideally we wouldn't need to install a dependency here, before we
# know the codename.
subprocess.check_call(["sudo", "apt-get", "update"])
subprocess.check_call(["sudo", "apt-get", "install", "-y", "lsb-release", "software-properties-common"])
vendor = subprocess_text_output(["lsb_release", "-is"])
codename = subprocess_text_output(["lsb_release", "-cs"])
if not (vendor in SUPPORTED_PLATFORMS and codename in SUPPORTED_PLATFORMS[vendor]):
    logging.critical("Unsupported platform: {} {}".format(vendor, codename))
    sys.exit(1)

POSTGRES_VERSION_MAP = {
    "trusty": "9.3",
    "xenial": "9.5",
}
POSTGRES_VERSION = POSTGRES_VERSION_MAP[codename]

UBUNTU_COMMON_APT_DEPENDENCIES = [
    "closure-compiler",
    "memcached",
    "rabbitmq-server",
    "redis-server",
    "hunspell-en-us",
    "supervisor",
    "git",
    "libssl-dev",
    "yui-compressor",
    "wget",
    "ca-certificates",      # Explicit dependency in case e.g. wget is already installed
    "puppet",               # Used by lint-all
    "gettext",              # Used by makemessages i18n
    "curl",                 # Used for fetching PhantomJS as wget occasionally fails on redirects
    "netcat",               # Used for flushing memcached
] + VENV_DEPENDENCIES

APT_DEPENDENCIES = {
    "trusty": UBUNTU_COMMON_APT_DEPENDENCIES + [
        "postgresql-9.3",
        "postgresql-9.3-tsearch-extras",
        "postgresql-9.3-pgroonga",
    ],
    "xenial": UBUNTU_COMMON_APT_DEPENDENCIES + [
        "postgresql-9.5",
        "postgresql-9.5-tsearch-extras",
        "postgresql-9.5-pgroonga",
    ],
}

TSEARCH_STOPWORDS_PATH = "/usr/share/postgresql/%s/tsearch_data/" % (POSTGRES_VERSION,)
REPO_STOPWORDS_PATH = os.path.join(
    ZULIP_PATH,
    "puppet",
    "zulip",
    "files",
    "postgresql",
    "zulip_english.stop",
)

LOUD = dict(_out=sys.stdout, _err=sys.stderr)


def main():
    # type: () -> int

    # npm install and management commands expect to be run from the root of the
    # project.
    os.chdir(ZULIP_PATH)

    run(["sudo", "./scripts/lib/setup-apt-repo"])

    # Add groonga repository to get the pgroonga packages; retry if it fails :/
    try:
        run(["sudo", "add-apt-repository", "-y", "ppa:groonga/ppa"])
    except subprocess.CalledProcessError:
        print(WARNING + "`Could not add groonga; retrying..." + ENDC)
        run(["sudo", "add-apt-repository", "-y", "ppa:groonga/ppa"])

    run(["sudo", "apt-get", "update"])
    run(["sudo", "apt-get", "-y", "install", "--no-install-recommends"] + APT_DEPENDENCIES[codename])

    if TRAVIS:
        if PY2:
            MYPY_REQS_FILE = os.path.join(ZULIP_PATH, "requirements", "mypy.txt")
            setup_virtualenv(PY3_VENV_PATH, MYPY_REQS_FILE, patch_activate_script=True,
                             virtualenv_args=['-p', 'python3'])
            DEV_REQS_FILE = os.path.join(ZULIP_PATH, "requirements", "py2_dev.txt")
            setup_virtualenv(PY2_VENV_PATH, DEV_REQS_FILE, patch_activate_script=True)
        else:
            DEV_REQS_FILE = os.path.join(ZULIP_PATH, "requirements", "py3_dev.txt")
            setup_virtualenv(VENV_PATH, DEV_REQS_FILE, patch_activate_script=True,
                             virtualenv_args=['-p', 'python3'])
    else:
        # Import tools/setup_venv.py instead of running it so that we get an
        # activated virtualenv for the rest of the provisioning process.
        from tools.setup import setup_venvs
        setup_venvs.main()

    # Put Python2 virtualenv activation in our .bash_profile.
    with open(os.path.expanduser('~/.bash_profile'), 'w+') as bash_profile:
        bash_profile.writelines([
            "source .bashrc\n",
            "source %s\n" % (os.path.join(VENV_PATH, "bin", "activate"),),
        ])

    run(["sudo", "cp", REPO_STOPWORDS_PATH, TSEARCH_STOPWORDS_PATH])

    # create log directory `zulip/var/log`
    run(["mkdir", "-p", LOG_DIR_PATH])
    # create upload directory `var/uploads`
    run(["mkdir", "-p", UPLOAD_DIR_PATH])
    # create test upload directory `var/test_upload`
    run(["mkdir", "-p", TEST_UPLOAD_DIR_PATH])
    # create coverage directory`var/coverage`
    run(["mkdir", "-p", COVERAGE_DIR_PATH])
    # create linecoverage directory`var/linecoverage-report`
    run(["mkdir", "-p", LINECOVERAGE_DIR_PATH])
    # create linecoverage directory`var/node-coverage`
    run(["mkdir", "-p", NODE_TEST_COVERAGE_DIR_PATH])

    run(["tools/setup/download-zxcvbn"])
    run(["tools/setup/emoji_dump/build_emoji"])
    run(["scripts/setup/generate_secrets.py", "--development"])
    if TRAVIS and not PRODUCTION_TRAVIS:
        run(["sudo", "service", "rabbitmq-server", "restart"])
        run(["sudo", "service", "redis-server", "restart"])
        run(["sudo", "service", "memcached", "restart"])
    elif "--docker" in sys.argv:
        run(["sudo", "service", "rabbitmq-server", "restart"])
        run(["sudo", "pg_dropcluster", "--stop", POSTGRES_VERSION, "main"])
        run(["sudo", "pg_createcluster", "-e", "utf8", "--start", POSTGRES_VERSION, "main"])
        run(["sudo", "service", "redis-server", "restart"])
        run(["sudo", "service", "memcached", "restart"])
    if not PRODUCTION_TRAVIS:
        # These won't be used anyway
        run(["scripts/setup/configure-rabbitmq"])
        run(["tools/setup/postgres-init-dev-db"])
        run(["tools/do-destroy-rebuild-database"])
        run(["tools/setup/postgres-init-test-db"])
        run(["tools/do-destroy-rebuild-test-database"])
        run(["python", "./manage.py", "compilemessages"])

    # Here we install nvm, node, and npm.
    run(["sudo", "tools/setup/install-node"])

    # This is a wrapper around `npm install`, which we run last since
    # it can often fail due to network issues beyond our control.
    try:
        # Hack: We remove `node_modules` as root to work around an
        # issue with the symlinks being improperly owned by root.
        if os.path.islink("node_modules"):
            run(["sudo", "rm", "-f", "node_modules"])
        setup_node_modules()
    except subprocess.CalledProcessError:
        print(WARNING + "`npm install` failed; retrying..." + ENDC)
        setup_node_modules()

    print()
    print(OKBLUE + "Zulip development environment setup succeeded!" + ENDC)
    return 0

if __name__ == "__main__":
    sys.exit(main())

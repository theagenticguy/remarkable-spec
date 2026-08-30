"""Create, report or delete the SYNC-type Data Automation project ``bda`` needs. Costs nothing.

Run by hand, never by a test and never by CI:

    mise run bda-project            # create it, or report the one that is already there
    mise run bda-project-delete     # remove it

Why a script exists at all
--------------------------
``RMSPEC_OCR_ENGINES`` defaults to ``bda``, and that engine is the only one whose obstacle is
configuration rather than a package: it needs a project whose ``projectType`` is ``SYNC``, which
the console does not create, whose standard output enables ``WORD`` granularity, without which a
reading has no confidence at all, and which names exactly one document text format, because two
is refused outright. None of that is discoverable and none of it is in the AWS user guide. A user
who has to assemble it by hand will get one of the three wrong, and the failure will arrive from
the service rather than from the thing that was wrong.

So the configuration is not written here. It is
:data:`rmspec.ocr.bda.SYNC_PROJECT_CONFIG`, beside the adapter whose reading depends on it, and
this file passes it through. A project made any other way is one the adapter may read worse, and
that coupling is the reason the constant does not live in this file.

Why it is here rather than in ``rmspec-ocr``
--------------------------------------------
Provisioning an account resource is not something a recognizer does, and a control-plane call has
no business in the wheel a user installs to read their notebook. ``probes/`` is where a script
that talks to the real service lives: outside ``packages/``, and so outside the coverage floor,
the architecture invariants and the no-billable-calls rule.

It creates nothing twice. ``ListDataAutomationProjects`` is consulted first, so a second run
reports the existing ARN instead of burning one of the hundred projects an account may hold. And
it can delete what it made, which the ``rmspec push`` docstring explains at length is a property
worth having: a command that creates a resource no command can remove is a trap, however small.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from botocore.exceptions import ClientError

from rmspec.ocr.bda import PROJECT_TYPE, STAGE, SYNC_PROJECT_CONFIG

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class DataAutomationProjects(Protocol):
    """The three control-plane methods this file calls, and nothing else.

    The same shape :class:`rmspec.ocr.bda.DataAutomationInvoker` has, for the same reason: a
    boto3 client is dynamically built, so a statically typed caller either declares the narrow
    surface it uses or reaches for a ``type: ignore`` -- and this repository allows no such
    escape hatch anywhere. Declaring it also documents the blast radius of this script: three
    calls, one of which reads.
    """

    def list_data_automation_projects(self, **kwargs: object) -> Mapping[str, Any]:
        """List the account's projects.

        Parameters
        ----------
        **kwargs
            The wire request, ``maxResults`` here.

        Returns
        -------
        Mapping[str, Any]
            The parsed response.
        """
        ...

    def create_data_automation_project(self, **kwargs: object) -> Mapping[str, Any]:
        """Create one project.

        Parameters
        ----------
        **kwargs
            The wire request: name, description, stage, type and standard output configuration.

        Returns
        -------
        Mapping[str, Any]
            The parsed response, carrying ``projectArn``.
        """
        ...

    def delete_data_automation_project(self, **kwargs: object) -> Mapping[str, Any]:
        """Delete one project.

        Parameters
        ----------
        **kwargs
            The wire request, ``projectArn`` here.

        Returns
        -------
        Mapping[str, Any]
            The parsed response.
        """
        ...


DEFAULT_REGION = "us-west-2"
"""Where the project is made when none is named. The same default as ``RMSPEC_AWS_REGION``."""

DEFAULT_NAME = "rmspec-ocr"
"""The project's name. One per account is enough: every rmspec run reads the same way."""

DESCRIPTION = "Handwriting reads for rmspec. Created by probes/bda_project.py."
"""What the project says about itself in the console, so nobody deletes it wondering."""

SETTING = "RMSPEC_BDA_PROJECT_ARN"
"""The variable whose value this whole script exists to produce."""

BUILD_SERVICE = "bedrock-data-automation"
"""The control-plane service. The runtime one is the adapter's, and this file never calls it."""

_LIST_PAGE = 100
"""Projects to ask for at once, which is also the per-account limit, so one page is every page."""


def say(message: str, /) -> None:
    """Write one line to stderr, for whoever ran this by hand.

    Parameters
    ----------
    message
        The line, without its newline.

    Notes
    -----
    stderr, not stdout: the one thing on stdout is the ``export`` line, so
    ``eval "$(mise run bda-project 2>/dev/null)"`` configures the shell that ran it. That is the
    same split ``rmspec env`` makes and for the same reason.
    """
    sys.stderr.write(f"{message}\n")


def find(client: DataAutomationProjects, /, *, name: str) -> str | None:
    """Return the ARN of the project called *name*, or ``None`` when there is none.

    Parameters
    ----------
    client
        A ``bedrock-data-automation`` client.
    name
        The project name to look for.

    Returns
    -------
    str or None
        The ARN, or ``None``.
    """
    listed = client.list_data_automation_projects(maxResults=_LIST_PAGE)
    for project in listed.get("projects", []):
        if project.get("projectName") == name:
            return str(project["projectArn"])
    return None


def create(client: DataAutomationProjects, /, *, name: str) -> str:
    """Create the project from the adapter's own configuration.

    Parameters
    ----------
    client
        A ``bedrock-data-automation`` client.
    name
        The project name.

    Returns
    -------
    str
        The new project's ARN.
    """
    created = client.create_data_automation_project(
        projectName=name,
        projectDescription=DESCRIPTION,
        projectStage=STAGE,
        projectType=PROJECT_TYPE,
        standardOutputConfiguration=SYNC_PROJECT_CONFIG,
    )
    return str(created["projectArn"])


def main(argv: Sequence[str] | None = None) -> int:
    """Create, report or delete the project, and print the setting that names it.

    Parameters
    ----------
    argv
        Command-line words, or ``None`` to read :data:`sys.argv`.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the service refused. A refusal message is the finding: all
        three of this project's non-obvious requirements were discovered by reading one.
    """
    parser = argparse.ArgumentParser(
        prog="rmspec-bda-project",
        description=main.__doc__,
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help="where the project lives")
    parser.add_argument("--name", default=DEFAULT_NAME, help="the project's name")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="remove the project instead of creating it",
    )
    arguments = parser.parse_args(argv)
    client: DataAutomationProjects = boto3.Session(region_name=arguments.region).client(
        BUILD_SERVICE
    )

    try:
        existing = find(client, name=arguments.name)
        if arguments.delete:
            if existing is None:
                say(f"no project called {arguments.name!r} in {arguments.region}; nothing to do")
                return 0
            client.delete_data_automation_project(projectArn=existing)
            say(f"deleted {existing}")
            say(f"unset {SETTING}")
            return 0
        if existing is not None:
            say(f"{arguments.name!r} already exists in {arguments.region}; reusing it")
            arn = existing
        else:
            arn = create(client, name=arguments.name)
            say(f"created a {PROJECT_TYPE} project in {arguments.region}, stage {STAGE}")
    except ClientError as exc:
        say(f"{exc.response['Error']['Code']}: {exc}")
        return 1

    sys.stdout.write(f"export {SETTING}={arn}\n")
    say(f"eval the line above, or add it to your shell profile, and `rmspec ocr` will use {arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

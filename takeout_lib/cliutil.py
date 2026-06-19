"""Small shared helpers for the standalone command-line tools.

Convention across the whole project:
  * ``--dry-run`` means "report what would change, change nothing."
  * Tools that modify files in place run for real by default but first ask for
    confirmation (skippable with ``--yes`` for scripting).
"""

import sys


def add_run_flags(parser):
    """Add the standard --dry-run / --yes flags to a tool's parser."""
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change and exit; modify nothing.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt (for scripting).")


def confirm(action_line):
    """Show an irreversible-change warning and ask the user to confirm.

    Returns True only if the user explicitly types y/yes. Used after the tool has
    already printed a summary of what it would do, so the choice is informed.
    """
    bar = "!" * 64
    print(f"\n{bar}")
    print(f"  {action_line}")
    print("  These files are changed IN PLACE and cannot be automatically undone.")
    print(bar)
    try:
        resp = input("  Type 'yes' to proceed (anything else cancels): ").strip().lower()
    except EOFError:
        return False
    return resp in ("y", "yes")


def gate(args, action_line):
    """Decide whether to apply changes. Returns True to proceed, and prints the
    appropriate message when not proceeding.

      --dry-run  -> never proceed (already reported above)
      --yes      -> proceed without prompting
      otherwise  -> proceed only if the user confirms
    """
    if args.dry_run:
        print("\n(dry run — nothing changed; omit --dry-run to apply)")
        return False
    if args.yes:
        return True
    if confirm(action_line):
        return True
    print("  Cancelled — nothing changed.")
    return False

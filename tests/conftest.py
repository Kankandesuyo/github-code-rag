import subprocess
import sys

import pytest


@pytest.fixture
def link_directory():
    def create(link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if sys.platform != "win32":
                raise
            # Junctions are available on Windows without symlink privileges.
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
            )
            assert link.is_junction()
        assert link.resolve() == target.resolve()

    return create

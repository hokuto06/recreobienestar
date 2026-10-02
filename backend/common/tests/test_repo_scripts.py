"""Scripts that something EXECUTES directly must be executable in the repo:
deploys rsync with -a, which copies the repo's file mode to the server.
backup_db.sh committed as 100644 silently broke the nightly backup
(systemd: status=203/EXEC) from 04/09/2026 — see ARCHITECTURE.md §14."""
import os
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BACKEND = Path(settings.BASE_DIR)
EXECUTED_DIRECTLY = [
    BACKEND / 'deploy' / 'scripts' / 'backup_db.sh',  # backup-recreobienestar.service ExecStart
    BACKEND / 'entrypoint.sh',                        # Docker ENTRYPOINT
]


class RepoScriptsAreExecutableTests(SimpleTestCase):
    def test_scripts_run_directly_are_executable(self):
        for path in EXECUTED_DIRECTLY + sorted((BACKEND / 'deploy' / 'scripts').glob('*.sh')):
            with self.subTest(script=str(path.relative_to(BACKEND))):
                self.assertTrue(path.is_file())
                self.assertTrue(os.access(path, os.X_OK), f'{path} is not executable (chmod +x, and commit the mode)')

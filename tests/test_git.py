import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import tests  # noqa: F401
import git


def _git(cwd, *args):
    subprocess.run(
        ['git'] + list(args),
        cwd=cwd, check=True,
        env={**os.environ,
             'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e',
             'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@e'},
        capture_output=True,
    )


def _git_available():
    try:
        subprocess.run(['git', '--version'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


@unittest.skipUnless(_git_available(), 'git not installed')
class GetGitInfo(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo = self._tmp.name
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'init')

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_repo(self):
        info = git.get_git_info(self.repo)
        self.assertIsNotNone(info)
        self.assertEqual(info['branch'], 'main')
        self.assertEqual(info['ahead'], 0)
        self.assertEqual(info['behind'], 0)
        self.assertEqual(info['added'], 0)
        self.assertEqual(info['modified'], 0)
        self.assertEqual(info['deleted'], 0)
        self.assertEqual(info['untracked'], 0)
        self.assertFalse(info['upstream'])
        self.assertIsNone(info['op_state'])

    def test_modified_and_untracked(self):
        Path(self.repo, 'a.txt').write_text('hi')
        _git(self.repo, 'add', 'a.txt')
        _git(self.repo, 'commit', '-q', '-m', 'a')
        Path(self.repo, 'a.txt').write_text('changed')
        Path(self.repo, 'new.txt').write_text('untracked')
        info = git.get_git_info(self.repo)
        self.assertEqual(info['modified'], 1)
        self.assertEqual(info['untracked'], 1)

    def test_added_file(self):
        Path(self.repo, 'b.txt').write_text('new')
        _git(self.repo, 'add', 'b.txt')
        info = git.get_git_info(self.repo)
        self.assertEqual(info['added'], 1)

    def test_deleted_file(self):
        Path(self.repo, 'c.txt').write_text('hi')
        _git(self.repo, 'add', 'c.txt')
        _git(self.repo, 'commit', '-q', '-m', 'c')
        Path(self.repo, 'c.txt').unlink()
        info = git.get_git_info(self.repo)
        self.assertEqual(info['deleted'], 1)

    def test_non_git_path_returns_none(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(git.get_git_info(d))

    def test_no_cwd(self):
        self.assertIsNone(git.get_git_info(None))


@unittest.skipUnless(_git_available(), 'git not installed')
class DetectOpState(unittest.TestCase):
    def test_none_for_clean_repo(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            self.assertIsNone(git._detect_op_state(d, git_dir))

    def test_merge_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'MERGE_HEAD').write_text('deadbeef\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('merge', None))

    def test_cherry_pick_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'CHERRY_PICK_HEAD').write_text('deadbeef\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('cherry-pick', None))

    def test_revert_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'REVERT_HEAD').write_text('deadbeef\n')
            self.assertEqual(git._detect_op_state(d, git_dir), ('revert', None))

    def test_rebase_with_progress(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            rm_dir = Path(d, '.git', 'rebase-merge')
            rm_dir.mkdir()
            (rm_dir / 'msgnum').write_text('2\n')
            (rm_dir / 'end').write_text('5\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('rebase', '2/5'))

    def test_bisect_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'BISECT_LOG').write_text('git bisect start\n')
            self.assertEqual(git._detect_op_state(d, git_dir), ('bisect', None))


if __name__ == '__main__':
    unittest.main()

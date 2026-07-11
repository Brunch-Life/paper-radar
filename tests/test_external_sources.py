import os
import unittest
from pathlib import Path

os.environ.setdefault(
    "DEEPREAD_SKILL",
    str(Path(__file__).resolve().parents[1] / "skill-paper-deep-read.md"),
)
from deepread_opus import normalize_fetch_url


class ExternalSourceUrlTests(unittest.TestCase):
    def test_github_blob_pdf_becomes_raw(self):
        self.assertEqual(
            normalize_fetch_url(
                "https://github.com/Robbyant/lingbot-va/blob/main/LingBot_VA2_paper.pdf"
            ),
            "https://github.com/Robbyant/lingbot-va/raw/main/LingBot_VA2_paper.pdf",
        )

    def test_huggingface_blob_becomes_resolve(self):
        self.assertEqual(
            normalize_fetch_url("https://huggingface.co/org/repo/blob/main/paper.pdf"),
            "https://huggingface.co/org/repo/resolve/main/paper.pdf",
        )

    def test_gitlab_blob_becomes_raw(self):
        self.assertEqual(
            normalize_fetch_url("https://gitlab.com/org/repo/-/blob/main/paper.pdf"),
            "https://gitlab.com/org/repo/-/raw/main/paper.pdf",
        )

    def test_project_page_is_unchanged(self):
        url = "https://example.org/project?paper=1#overview"
        self.assertEqual(normalize_fetch_url(url), "https://example.org/project?paper=1")

    def test_non_pdf_github_blob_is_unchanged(self):
        url = "https://github.com/org/repo/blob/main/train.py"
        self.assertEqual(normalize_fetch_url(url), url)


if __name__ == "__main__":
    unittest.main()

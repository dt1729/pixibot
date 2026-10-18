"""The standards documents must be readable, which they were not from M41 to M50.

pixibot.lib.standards moved into lib/ at M41 and its directory path stopped
resolving. Nothing imported load_standard or available_standards, so no gate
noticed that decision 21's "full standards read on demand" had stopped working.
These tests exist so the next move breaks a test instead of a feature.
"""

import unittest

from pixibot.lib import standards


class StandardsTest(unittest.TestCase):
    def test_the_directory_the_module_reads_actually_exists(self):
        self.assertTrue(standards._STD_DIR.is_dir(), standards._STD_DIR)

    def test_the_documents_are_discoverable(self):
        found = standards.available_standards()
        self.assertTrue(found, "available_standards() found nothing")
        for name in ("lld.md", "programming.md", "testing.md", "best-practices.md"):
            self.assertIn(name, found)

    def test_every_discoverable_document_loads(self):
        for name in standards.available_standards():
            with self.subTest(document=name):
                self.assertGreater(len(standards.load_standard(name)), 100)

    def test_a_missing_document_raises_rather_than_returning_empty(self):
        with self.assertRaises(FileNotFoundError):
            standards.load_standard("not-a-standard.md")

    def test_the_always_on_contract_names_the_documents_it_points_at(self):
        contract = standards.standards_contract()
        self.assertIn("standards/*.md", contract)
        self.assertGreater(len(contract), 100)

    def test_the_documents_ship_inside_the_package(self):
        # a module that reads files outside its own package depends on the repo
        # layout, which is exactly how this broke
        import pixibot
        package_root = __import__("pathlib").Path(pixibot.__file__).parent
        self.assertEqual(standards._STD_DIR.parent, package_root)


if __name__ == "__main__":
    unittest.main()

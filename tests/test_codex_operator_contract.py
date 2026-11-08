import unittest
from pathlib import Path

class CodexOperatorContractTests(unittest.TestCase):
    def test_profile_reference_and_import_are_present(self):
        skill = Path("SKILL.md").read_text()
        self.assertIn("import-profile", skill)
        self.assertIn("references/visual-profile.md", skill)
        self.assertNotIn("ARK_API_KEY", skill)
        self.assertTrue(Path("references/visual-profile.md").is_file())
        reference = Path("references/visual-profile.md").read_text()
        self.assertIn("For both the source image and candidate image", reference)
        self.assertIn("as `womens_pants_set` before considering the", reference)

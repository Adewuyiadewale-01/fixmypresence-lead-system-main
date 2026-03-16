import os
import sys
import unittest

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from enricher.scraper_v2 import PageArtifact, PersonExtractor


TEAM_HTML = """
<html lang="en">
  <body>
    <section class="team-grid">
      <div class="member-card">
        <h2>Max Spanjer</h2>
        <p>Founder</p>
        <p>After more than 12 years of international experience in financial markets, Max founded Greenstar Capital.</p>
        <a href="mailto:max.spanjer@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Adam Windmill</h2>
        <p>Managing Director</p>
        <p>Adam brings experience in environmental and commodity markets, towards risk management and investment.</p>
        <a href="mailto:adam.windmill@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Joep Gorgels</h2>
        <p>Shipping Director</p>
        <p>Joep is a senior banker and shipping executive with a wide range of leadership roles.</p>
        <a href="mailto:joep.gorgels@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Akram Mhamdi</h2>
        <p>Managing Director</p>
        <p>Akram brings deep expertise in environmental and refrigerant gas markets.</p>
        <a href="mailto:akram.mhamdi@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Pietro Casa</h2>
        <p>Sales &amp; Consulting</p>
        <p>Pietro holds a master's degree in European Studies, specializing in environmental governance and international trade.</p>
        <a href="mailto:pietro.casa@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Koen Verweij</h2>
        <p>Commercial Director</p>
        <p>As an Olympic and world speedskating champion, Koen knows what it takes to perform at an elite level.</p>
        <a href="mailto:contact@greenstar-capital.com">Get in Touch</a>
      </div>
      <div class="member-card">
        <h2>Deirdre Rodink</h2>
        <p>https://lh3.googleusercontent.com/a/avatar</p>
        <p>Office Manager</p>
        <p>With a Master's degree in Art History, Deirdre ensures the daily operations of Greenstar Capital run seamlessly.</p>
        <a href="mailto:contact@greenstar-capital.com">Get in Touch</a>
      </div>
    </section>
    <section class="content-block">
      <h2>Greenstar Capital's EU ETS fund</h2>
      <p>Fit for 55 legislation agreed on by European Council</p>
      <a href="mailto:euets@greenstar-capital.com">Get in Touch</a>
    </section>
  </body>
</html>
"""


def make_artifact(html: str, url: str = "https://example.com/team",
                  emails=None, linkedin_urls=None) -> PageArtifact:
    return PageArtifact(
        url=url,
        soup=BeautifulSoup(html, "html.parser"),
        emails=set(emails or []),
        linkedin_urls=set(linkedin_urls or []),
    )


class TestPersonExtractor(unittest.TestCase):
    def setUp(self):
        self.extractor = PersonExtractor()

    def test_wix_like_team_section_extracts_names_and_roles(self):
        artifact = make_artifact(TEAM_HTML, emails=[
            "max.spanjer@greenstar-capital.com",
            "adam.windmill@greenstar-capital.com",
            "joep.gorgels@greenstar-capital.com",
            "akram.mhamdi@greenstar-capital.com",
            "pietro.casa@greenstar-capital.com",
            "contact@greenstar-capital.com",
        ])

        persons = self.extractor.extract_from_page_artifact(artifact)
        by_name = {person["name"]: person for person in persons}

        self.assertEqual(len(persons), 7)
        self.assertEqual(by_name["Max Spanjer"]["role"], "Founder")
        self.assertEqual(by_name["Pietro Casa"]["role"], "Sales & Consulting")
        self.assertEqual(by_name["Koen Verweij"]["role"], "Commercial Director")
        self.assertEqual(by_name["Deirdre Rodink"]["role"], "Office Manager")

    def test_personal_email_is_primary_confidence(self):
        artifact = make_artifact("""
        <div class="team">
          <div class="card">
            <h3>Alice Smith</h3>
            <p>CTO</p>
            <p>Alice leads product and engineering.</p>
            <a href="mailto:alice.smith@example.com">Get in Touch</a>
          </div>
        </div>
        """, emails=["alice.smith@example.com"])

        persons = self.extractor.extract_from_page_artifact(artifact)
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["name"], "Alice Smith")
        self.assertEqual(persons[0]["confidence"], "primary")
        self.assertEqual(persons[0]["strategy"], "container_primary")

    def test_generic_contact_is_secondary_confidence(self):
        artifact = make_artifact("""
        <div class="team">
          <div class="card">
            <h3>Maria Gomez</h3>
            <p>Office Manager</p>
            <a href="mailto:contact@example.com">Get in Touch</a>
          </div>
        </div>
        """, emails=["contact@example.com"])

        persons = self.extractor.extract_from_page_artifact(artifact)
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0]["name"], "Maria Gomez")
        self.assertEqual(persons[0]["confidence"], "secondary")
        self.assertEqual(persons[0]["strategy"], "container_secondary")

    def test_shared_generic_inbox_keeps_two_people(self):
        artifact = make_artifact("""
        <div class="team">
          <div class="card">
            <h3>Jane Doe</h3>
            <p>Commercial Director</p>
            <a href="mailto:contact@example.com">Get in Touch</a>
          </div>
          <div class="card">
            <h3>John Roe</h3>
            <p>Office Manager</p>
            <a href="mailto:contact@example.com">Get in Touch</a>
          </div>
        </div>
        """, emails=["contact@example.com"])

        persons = self.extractor.extract_from_page_artifact(artifact)
        deduped = self.extractor._dedupe_persons(persons)

        self.assertEqual(len(deduped), 2)
        self.assertEqual({person["name"] for person in deduped}, {"Jane Doe", "John Roe"})

    def test_non_person_page_is_rejected(self):
        artifact = make_artifact("""
        <section class="article">
          <h2>Greenstar Capital's EU ETS fund</h2>
          <p>Fit for 55 legislation agreed on by European Council</p>
          <a href="mailto:contact@example.com">Get in Touch</a>
        </section>
        """, emails=["contact@example.com"])

        persons = self.extractor.extract_from_page_artifact(artifact)
        self.assertEqual(persons, [])

    def test_cta_is_ignored_for_role_and_bio(self):
        artifact = make_artifact("""
        <div class="team">
          <div class="card">
            <h3>Sarah Connor</h3>
            <p>Managing Director</p>
            <p>Sarah oversees long-term investment strategy and risk management.</p>
            <a href="mailto:sarah.connor@example.com">Get in Touch</a>
          </div>
        </div>
        """, emails=["sarah.connor@example.com"])

        persons = self.extractor.extract_from_page_artifact(artifact)
        self.assertEqual(persons[0]["role"], "Managing Director")
        self.assertIn("investment strategy", persons[0]["bio"])


class TestPersonExtractorAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.extractor = PersonExtractor()

    async def test_page_local_extraction_does_not_create_euets_person(self):
        artifact = make_artifact(
            TEAM_HTML,
            emails=[
                "max.spanjer@greenstar-capital.com",
                "adam.windmill@greenstar-capital.com",
                "joep.gorgels@greenstar-capital.com",
                "akram.mhamdi@greenstar-capital.com",
                "pietro.casa@greenstar-capital.com",
                "contact@greenstar-capital.com",
            ],
        )

        site_emails = [
            "max.spanjer@greenstar-capital.com",
            "adam.windmill@greenstar-capital.com",
            "joep.gorgels@greenstar-capital.com",
            "akram.mhamdi@greenstar-capital.com",
            "pietro.casa@greenstar-capital.com",
            "contact@greenstar-capital.com",
            "euets@greenstar-capital.com",
        ]

        async with aiohttp.ClientSession() as session:
            persons = await self.extractor.extract_persons(
                session,
                [artifact],
                [],
                site_emails,
                site_domain="greenstar-capital.com",
            )

        by_name = {person["name"]: person for person in persons}
        self.assertEqual(len(persons), 7)
        self.assertNotIn("Euets", by_name)
        self.assertEqual(by_name["Max Spanjer"]["role"], "Founder")
        self.assertEqual(by_name["Pietro Casa"]["role"], "Sales & Consulting")
        self.assertEqual(by_name["Koen Verweij"]["email"], "contact@greenstar-capital.com")
        self.assertEqual(by_name["Deirdre Rodink"]["role"], "Office Manager")


if __name__ == "__main__":
    unittest.main()

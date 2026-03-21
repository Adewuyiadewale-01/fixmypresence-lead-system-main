"""
Tests for scraper_v2: existing extraction + upgrade tests for page classifier,
contact-anchor extraction, structural peer promotion, sibling sweep, and
verified people section logic.

Upgrade fixtures are simplified HTML inspired by real sites:
- axento.nl  : strong repeated cards with mixed contact coverage
- famadvocaten.nl : article/blog noise with names and role words
- thriven.nl : dashboard/account pages with forms but no staff cards
"""
import os
import sys
import unittest

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from enricher.scraper_v2 import (
    CandidatePool, PageArtifact, PersonExtractor,
    classify_page, find_verified_sections, _container_shape,
    is_hard_auth_page, is_soft_article_page,
)


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


# ─── Upgrade Fixtures ────────────────────────────────────────────────────────

UPGRADE_TEAM_PAGE_REPEATED_CARDS = """
<html lang="en"><body>
<header><nav>
  <a href="/">Home</a>
  <a href="/about">About</a>
  <a href="https://linkedin.com/company/testcorp">LinkedIn</a>
  <a href="mailto:info@testcorp.nl">info@testcorp.nl</a>
</nav></header>
<main>
  <h1>Our Team</h1>
  <div class="team-section">
    <div class="team-card">
      <img src="/img/alice.jpg" alt="Alice">
      <h3>Alice van den Berg</h3>
      <p>Managing Partner</p>
      <a href="mailto:alice@testcorp.nl">alice@testcorp.nl</a>
      <a href="https://linkedin.com/in/alice-van-den-berg">LinkedIn</a>
    </div>
    <div class="team-card">
      <img src="/img/bob.jpg" alt="Bob">
      <h3>Bob Jansen</h3>
      <p>Senior Consultant</p>
      <a href="mailto:bob@testcorp.nl">bob@testcorp.nl</a>
      <a href="https://linkedin.com/in/bob-jansen">LinkedIn</a>
    </div>
    <div class="team-card">
      <img src="/img/carol.jpg" alt="Carol">
      <h3>Carol de Vries</h3>
      <p>Tax Advisor</p>
      <a href="tel:+31201234567">+31 20 123 4567</a>
    </div>
  </div>
</main>
<footer>
  <a href="https://linkedin.com/company/testcorp">Company LinkedIn</a>
  <a href="mailto:info@testcorp.nl">Contact us</a>
</footer>
</body></html>
"""

UPGRADE_ARTICLE_PAGE = """
<html lang="en"><body>
<header><nav>
  <a href="/">Home</a>
  <a href="/blog">Blog</a>
</nav></header>
<main>
  <article>
    <h1>How Tax Advisory Changed in 2024</h1>
    <div class="meta">
      <span class="author">By John Smith</span>
      <time datetime="2024-03-15">March 15, 2024</time>
    </div>
    <p>Senior Partner Alice van den Berg spoke at the annual conference about
    new regulatory changes affecting tax advisory firms. Director Bob Jansen
    also presented findings from their latest research on corporate compliance.</p>
    <p>The Managing Director of the Dutch Association said that these changes
    would fundamentally alter how accountants and tax advisors operate.</p>
    <div class="tags">
      <a href="/tag/tax">Tax</a>
      <a href="/tag/advisory">Advisory</a>
    </div>
    <div class="share">
      <a href="https://linkedin.com/shareArticle?url=test">Share on LinkedIn</a>
    </div>
  </article>
  <script type="application/ld+json">
  {
    "@type": "BlogPosting",
    "headline": "How Tax Advisory Changed in 2024",
    "datePublished": "2024-03-15",
    "author": {"@type": "Person", "name": "John Smith"}
  }
  </script>
</main>
</body></html>
"""

UPGRADE_DASHBOARD_PAGE = """
<html lang="en"><body>
<header><nav>
  <a href="/dashboard">Dashboard</a>
  <a href="/settings">Settings</a>
  <a href="/logout">Logout</a>
</nav></header>
<main>
  <h1>Account Settings</h1>
  <form action="/account/update" method="post">
    <label>Email</label>
    <input type="email" name="email" value="user@example.com">
    <label>Password</label>
    <input type="password" name="password">
    <label>Subscription</label>
    <select name="plan">
      <option>Basic</option>
      <option>Pro</option>
    </select>
    <button type="submit">Update Account</button>
  </form>
  <div class="account-nav">
    <a href="/orders">My Orders</a>
    <a href="/subscriptions">Subscriptions</a>
    <a href="/api-keys">API Keys</a>
  </div>
</main>
</body></html>
"""

UPGRADE_EMPLOYEE_LIST = """
<html lang="en"><body>
<main>
  <h1>Our People</h1>
  <div class="employee-list">
    <div class="employee-card">
      <img src="/img/diana.jpg" alt="Diana">
      <h3>Diana Bakker</h3>
      <p>CEO</p>
      <a href="https://linkedin.com/in/diana-bakker">LinkedIn</a>
      <a href="/people/diana-bakker">View profile</a>
    </div>
    <div class="employee-card">
      <img src="/img/erik.jpg" alt="Erik">
      <h3>Erik Mulder</h3>
      <p>CTO</p>
      <a href="mailto:erik@company.nl">erik@company.nl</a>
      <a href="/people/erik-mulder">View profile</a>
    </div>
    <div class="employee-card">
      <img src="/img/fenna.jpg" alt="Fenna">
      <h3>Fenna de Groot</h3>
      <p>CFO</p>
      <a href="/people/fenna-de-groot">View profile</a>
    </div>
  </div>
</main>
</body></html>
"""

UPGRADE_FOOTER_ONLY = """
<html lang="en"><body>
<main>
  <h1>Welcome to Our Company</h1>
  <p>We provide excellent consulting services across the Netherlands.</p>
</main>
<footer>
  <div class="footer-contact">
    <a href="https://linkedin.com/in/ceo-person">CEO LinkedIn</a>
    <a href="mailto:ceo@company.nl">ceo@company.nl</a>
    <p>Manager of Operations</p>
  </div>
</footer>
</body></html>
"""

UPGRADE_ROLE_ONLY_SIBLINGS = """
<html lang="en"><body>
<main>
  <h1>Our Team</h1>
  <div class="team-grid">
    <div class="member-card">
      <img src="/img/george.jpg" alt="George">
      <h3>George Visser</h3>
      <p>Senior Accountant</p>
    </div>
    <div class="member-card">
      <img src="/img/hannah.jpg" alt="Hannah">
      <h3>Hannah Smit</h3>
      <p>Tax Advisor</p>
    </div>
    <div class="member-card">
      <img src="/img/ivan.jpg" alt="Ivan">
      <h3>Ivan Bos</h3>
      <p>Junior Consultant</p>
    </div>
  </div>
</main>
</body></html>
"""

UPGRADE_SINGLE_ROLE_ONLY = """
<html lang="en"><body>
<main>
  <h1>About</h1>
  <div class="about-section">
    <div class="person-card">
      <h3>Karl Hendriks</h3>
      <p>Managing Director</p>
    </div>
  </div>
  <p>Our company was founded in 1995 and has been serving clients ever since.</p>
</main>
</body></html>
"""

UPGRADE_MIXED_CONFIRMED_PEERS = """
<html lang="en"><body>
<main>
  <h1>Leadership</h1>
  <div class="leadership-grid">
    <div class="leader-card">
      <img src="/img/lisa.jpg" alt="Lisa">
      <h3>Lisa Vermeer</h3>
      <p>Partner</p>
      <a href="https://linkedin.com/in/lisa-vermeer">LinkedIn</a>
    </div>
    <div class="leader-card">
      <img src="/img/mark.jpg" alt="Mark">
      <h3>Mark de Jong</h3>
      <p>Partner</p>
    </div>
    <div class="leader-card">
      <img src="/img/nina.jpg" alt="Nina">
      <h3>Nina Kuiper</h3>
      <p>Senior Associate</p>
    </div>
  </div>
</main>
</body></html>
"""

UPGRADE_CONTACT_ANCHOR_NEAR_NAME = """
<html lang="en"><body>
<main>
  <h1>About Us</h1>
  <div class="about-content">
    <div class="person-block">
      <h3>Olivia Peters</h3>
      <span>Head of Legal</span>
      <a href="https://linkedin.com/in/olivia-peters">Connect on LinkedIn</a>
    </div>
    <p>Our legal team handles all corporate matters.</p>
  </div>
</main>
</body></html>
"""

UPGRADE_GENERIC_EMAIL_NO_STRUCTURE = """
<html lang="en"><body>
<main>
  <h1>Contact Us</h1>
  <p>For general inquiries, please reach out:</p>
  <a href="mailto:info@company.nl">info@company.nl</a>
  <a href="mailto:support@company.nl">support@company.nl</a>
  <p>Our office is located in Amsterdam.</p>
</main>
</body></html>
"""

UPGRADE_TEL_ONLY = """
<html lang="en"><body>
<main>
  <div class="team-section">
    <div class="member">
      <h3>Pieter Dijkstra</h3>
      <p>Consultant</p>
      <a href="tel:+31612345678">Call me</a>
    </div>
  </div>
</main>
</body></html>
"""

UPGRADE_JSONLD_PERSON = """
<html lang="en"><body>
<main>
  <div class="team">
    <p>Meet our leadership team.</p>
  </div>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    "employee": [
      {
        "@type": "Person",
        "name": "Quinn Bakker",
        "jobTitle": "Director",
        "sameAs": ["https://linkedin.com/in/quinn-bakker"],
        "email": "quinn@company.nl"
      },
      {
        "@type": "Person",
        "name": "Rachel Kok",
        "jobTitle": "Manager",
        "email": "rachel@company.nl"
      }
    ]
  }
  </script>
</main>
</body></html>
"""

UPGRADE_TEAM_WITH_NEWS = """
<html lang="en"><body>
<main>
  <section class="team">
    <h1>Our Team</h1>
    <div class="team-grid">
      <div class="team-card">
        <h3>Sam Meijer</h3>
        <p>Partner</p>
        <a href="https://linkedin.com/in/sam-meijer">LinkedIn</a>
      </div>
      <div class="team-card">
        <h3>Tara Willems</h3>
        <p>Senior Advisor</p>
        <a href="mailto:tara@firm.nl">tara@firm.nl</a>
      </div>
    </div>
  </section>
  <section class="news">
    <article>
      <h2>Firm Wins Award</h2>
      <time datetime="2024-06-01">June 1, 2024</time>
      <p>Our firm was recognized for excellence in advisory services.</p>
    </article>
  </section>
</main>
</body></html>
"""

UPGRADE_ARTICLE_EMPLOYEE_PROFILE = """
<html lang="en"><body>
<main>
  <article class="profile-article">
    <h1>Joelle Lochtman</h1>
    <div class="meta">
      <span class="author">Team Profile</span>
      <time datetime="2024-06-01">June 1, 2024</time>
    </div>
    <p>Advocaat familierecht</p>
    <a href="https://www.linkedin.com/in/joelle-lochtman-7b7b291a0">LinkedIn</a>
  </article>
</main>
</body></html>
"""

UPGRADE_TEAM_PROFILE_LINKS_NO_CONTACT = """
<html lang="en"><body>
<main>
  <section class="leadership">
    <div class="leader">
      <h3>Anne de Boer</h3>
      <p>Partner</p>
      <a href="/people/anne-de-boer">View profile</a>
    </div>
    <div class="leader">
      <h3>Bram Jansen</h3>
      <p>Director</p>
      <a href="/people/bram-jansen">View profile</a>
    </div>
  </section>
</main>
</body></html>
"""


# ─── Upgrade Test Classes ────────────────────────────────────────────────────

class TestPageClassifier(unittest.TestCase):
    """Tests for the lightweight fetched-page classifier."""

    def _classify(self, html, url="https://example.com"):
        soup = BeautifulSoup(html, 'html.parser')
        return classify_page(soup, url)

    def test_article_page_classified_as_article(self):
        result = self._classify(UPGRADE_ARTICLE_PAGE)
        self.assertGreater(result["article_score"], 0)
        self.assertGreater(result["article_score"], result["people_hint"],
                           "Article page should score higher on article than people")

    def test_dashboard_classified_as_auth(self):
        result = self._classify(UPGRADE_DASHBOARD_PAGE)
        self.assertGreater(result["auth_score"], 0)
        self.assertGreater(result["auth_score"], result["people_hint"],
                           "Dashboard page should score higher on auth than people")
        soup = BeautifulSoup(UPGRADE_DASHBOARD_PAGE, 'html.parser')
        self.assertTrue(is_hard_auth_page("https://example.com/dashboard/settings", soup, result))

    def test_team_page_not_suppressed(self):
        result = self._classify(UPGRADE_TEAM_PAGE_REPEATED_CARDS)
        self.assertGreater(result["people_hint"], 0)
        self.assertGreaterEqual(result["people_hint"], result["article_score"])
        self.assertGreaterEqual(result["people_hint"], result["auth_score"])

    def test_team_page_with_news_not_suppressed(self):
        from enricher.scraper_v2 import should_suppress_level2
        result = self._classify(UPGRADE_TEAM_WITH_NEWS)
        self.assertGreater(result["people_hint"], 0)
        # The page has both people and article signals — should not be suppressed
        self.assertFalse(should_suppress_level2(result),
                         "Team page with news section should not be suppressed")

    def test_jsonld_person_boosts_people_hint(self):
        result = self._classify(UPGRADE_JSONLD_PERSON)
        self.assertGreater(result["people_hint"], 0)
        # May fire as jsonld_person or jsonld_person_list depending on nesting
        has_jsonld = any("jsonld_person" in s for s in result["signals"])
        self.assertTrue(has_jsonld, f"Expected jsonld signal, got: {result['signals']}")

    def test_classifier_returns_signals(self):
        result = self._classify(UPGRADE_DASHBOARD_PAGE)
        self.assertIsInstance(result["signals"], list)
        self.assertTrue(len(result["signals"]) > 0)

    def test_account_form_without_password_is_hard_auth(self):
        html = """
        <html><body>
        <main>
          <h1>My Account Settings</h1>
          <form action="/account/update">
            <label>Subscription</label>
            <input type="email" name="email">
            <button>Update Account</button>
          </form>
          <nav>
            <a href="/orders">Orders</a>
            <a href="/subscriptions">Subscriptions</a>
            <a href="/api-keys">API Keys</a>
          </nav>
        </main>
        </body></html>
        """
        soup = BeautifulSoup(html, 'html.parser')
        result = classify_page(soup, "https://example.com/account/settings")
        self.assertTrue(is_hard_auth_page("https://example.com/account/settings", soup, result))

    def test_article_page_without_people_evidence_is_soft_article(self):
        soup = BeautifulSoup(UPGRADE_ARTICLE_PAGE, 'html.parser')
        result = classify_page(soup, "https://example.com/blog/how-tax-advisory-changed")
        self.assertTrue(is_soft_article_page("https://example.com/blog/how-tax-advisory-changed", soup, result))

    def test_article_like_employee_profile_not_soft_rejected(self):
        soup = BeautifulSoup(UPGRADE_ARTICLE_EMPLOYEE_PROFILE, 'html.parser')
        result = classify_page(soup, "https://example.com/people/joelle-lochtman")
        self.assertFalse(is_soft_article_page("https://example.com/people/joelle-lochtman", soup, result))

    def test_team_with_profile_links_and_no_contact_not_soft_rejected(self):
        soup = BeautifulSoup(UPGRADE_TEAM_PROFILE_LINKS_NO_CONTACT, 'html.parser')
        result = classify_page(soup, "https://example.com/leadership")
        self.assertFalse(is_soft_article_page("https://example.com/leadership", soup, result))


class TestCandidatePoolGuards(unittest.TestCase):
    def test_auth_urls_filtered_early(self):
        pool = CandidatePool("https://example.com")
        pool.add("https://example.com/dashboard/orders", "sitemap")
        pool.add("https://example.com/account/settings", "homepage_anchor")
        self.assertEqual(pool.ranked(), [])

    def test_article_urls_downranked_below_people_urls(self):
        pool = CandidatePool("https://example.com")
        pool.add("https://example.com/over-ons", "sitemap")
        pool.add("https://example.com/wie-heeft-of-krijgt-ouderlijk-gezag", "sitemap")
        ranked = pool.ranked(5)
        self.assertEqual(ranked[0], "https://example.com/over-ons")


class TestContactAnchorExtraction(unittest.TestCase):
    """Tests for personal-signal normalization + flat contact-anchor extraction."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_personal_linkedin_near_name_extracts(self):
        soup = BeautifulSoup(UPGRADE_CONTACT_ANCHOR_NEAR_NAME, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        names = [p["name"] for p in persons]
        self.assertIn("Olivia Peters", names)

    def test_generic_email_no_structure_rejected(self):
        soup = BeautifulSoup(UPGRADE_GENERIC_EMAIL_NO_STRUCTURE, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        self.assertEqual(len(persons), 0)

    def test_footer_linkedin_ignored(self):
        soup = BeautifulSoup(UPGRADE_FOOTER_ONLY, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        self.assertEqual(len(persons), 0)

    def test_tel_not_primary_admission(self):
        soup = BeautifulSoup(UPGRADE_TEL_ONLY, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        primary = [p for p in persons if p.get("confidence") == "primary"]
        self.assertEqual(len(primary), 0,
                         "tel: link only should not produce primary-confidence card")


class TestStructuralPeerPromotion(unittest.TestCase):
    """Tests for the pending bucket and structural peer promotion."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_single_role_only_card_rejected(self):
        soup = BeautifulSoup(UPGRADE_SINGLE_ROLE_ONLY, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        self.assertEqual(len(persons), 0)

    def test_two_sibling_role_cards_promoted(self):
        soup = BeautifulSoup(UPGRADE_ROLE_ONLY_SIBLINGS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        self.assertGreaterEqual(len(persons), 2)
        for p in persons:
            self.assertIn(p.get("evidence"),
                          ["structural_peer_match", "sibling_sweep"])

    def test_structural_fingerprint_consistency(self):
        soup = BeautifulSoup(UPGRADE_ROLE_ONLY_SIBLINGS, 'html.parser')
        cards = soup.select('.member-card')
        shapes = [_container_shape(card) for card in cards]
        self.assertTrue(all(s == shapes[0] for s in shapes),
                        f"Identical structures should produce same fingerprint: {shapes}")


class TestSiblingSweep(unittest.TestCase):
    """Tests for sibling sweep after a confirmed card."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_mixed_confirmed_and_peers_all_extracted(self):
        soup = BeautifulSoup(UPGRADE_MIXED_CONFIRMED_PEERS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        names = [p["name"] for p in persons]
        self.assertIn("Lisa Vermeer", names)
        self.assertIn("Mark de Jong", names)
        self.assertIn("Nina Kuiper", names)

    def test_sibling_sweep_or_promotion_evidence_tag(self):
        soup = BeautifulSoup(UPGRADE_MIXED_CONFIRMED_PEERS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        # Peers without contact signals should be found via structural_promotion
        # or sibling_sweep (depending on which pass catches them first)
        non_primary = [p for p in persons
                       if p.get("evidence") in ("sibling_sweep", "structural_peer_match")]
        self.assertGreater(len(non_primary), 0)

    def test_team_page_full_extraction(self):
        """axento.nl-style: repeated cards with mixed contact coverage."""
        soup = BeautifulSoup(UPGRADE_TEAM_PAGE_REPEATED_CARDS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://testcorp.nl")
        names = [p["name"] for p in persons]
        self.assertIn("Alice van den Berg", names)
        self.assertIn("Bob Jansen", names)
        self.assertIn("Carol de Vries", names,
                      "Card with only tel: should be found via sibling sweep")

    def test_employee_list_extraction(self):
        soup = BeautifulSoup(UPGRADE_EMPLOYEE_LIST, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://company.nl")
        names = [p["name"] for p in persons]
        self.assertIn("Diana Bakker", names)
        self.assertIn("Erik Mulder", names)
        self.assertIn("Fenna de Groot", names,
                      "Card with only profile link should be found via sibling sweep")


class TestVerifiedPeopleSection(unittest.TestCase):
    """Tests for verified people section definition."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_promoted_only_not_verified(self):
        soup = BeautifulSoup(UPGRADE_ROLE_ONLY_SIBLINGS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        sections = find_verified_sections(persons)
        verified = [s for s in sections if s.get("has_primary") and s.get("card_count", 0) >= 2]
        self.assertEqual(len(verified), 0,
                         "Promoted-only cards should not produce a verified section")

    def test_mixed_section_is_verified(self):
        soup = BeautifulSoup(UPGRADE_MIXED_CONFIRMED_PEERS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        sections = find_verified_sections(persons)
        verified = [s for s in sections if s.get("has_primary") and s.get("card_count", 0) >= 2]
        self.assertGreater(len(verified), 0)


class TestJSONLDRegression(unittest.TestCase):
    """Regression: JSON-LD extraction should still work."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_jsonld_persons_extracted(self):
        soup = BeautifulSoup(UPGRADE_JSONLD_PERSON, 'html.parser')
        persons = self.extractor.strategy2_json_ld(soup)
        names = [p["name"] for p in persons]
        self.assertIn("Quinn Bakker", names)
        self.assertIn("Rachel Kok", names)


class TestNoDuplicateInflation(unittest.TestCase):
    """Ensure contact-anchor + container extraction don't create duplicates."""

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_no_duplicates_on_team_page(self):
        soup = BeautifulSoup(UPGRADE_TEAM_PAGE_REPEATED_CARDS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://testcorp.nl")
        names = [p["name"] for p in persons]
        self.assertEqual(len(names), len(set(n.lower() for n in names)),
                         f"Duplicate persons detected: {names}")


class TestContactMisassignmentFix(unittest.TestCase):
    """Regression: when two people share one ancestor section, each person
    should get the contact signal that triggered their own walk-up, not the
    first contact found in the shared parent."""

    SHARED_SECTION_HTML = """
    <html lang="en"><body>
    <main>
      <div class="team-section">
        <div class="person">
          <h3>Alice Jansen</h3>
          <p>CEO</p>
          <a href="https://linkedin.com/in/alice-jansen">LinkedIn</a>
        </div>
        <div class="person">
          <h3>Bob Jones</h3>
          <p>CTO</p>
          <a href="https://linkedin.com/in/bob-jones">LinkedIn</a>
        </div>
      </div>
    </main>
    </body></html>
    """

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_each_person_gets_own_linkedin(self):
        soup = BeautifulSoup(self.SHARED_SECTION_HTML, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://example.com")
        by_name = {p["name"]: p for p in persons}
        alice = by_name.get("Alice Jansen")
        bob = by_name.get("Bob Jones")
        self.assertIsNotNone(alice, "Alice should be extracted")
        self.assertIsNotNone(bob, "Bob should be extracted")
        if alice and bob:
            self.assertIn("alice", alice.get("linkedin", "").lower(),
                          "Alice should have her own LinkedIn, not Bob's")
            self.assertIn("bob", bob.get("linkedin", "").lower(),
                          "Bob should have his own LinkedIn, not Alice's")


class TestInternalFieldsStripped(unittest.TestCase):
    """Internal BS4 objects (_container, _name_node, etc.) must not appear
    in the final output of the extraction pipeline."""

    _INTERNAL_KEYS = {"_container", "_name_node", "_container_id", "parent_section_id"}

    def setUp(self):
        self.extractor = PersonExtractor()

    def test_v2_output_has_no_internal_fields(self):
        soup = BeautifulSoup(UPGRADE_TEAM_PAGE_REPEATED_CARDS, 'html.parser')
        persons = self.extractor.extract_from_page_artifact_v2(soup, "https://testcorp.nl")
        self.assertGreater(len(persons), 0, "Should extract at least one person")
        for person in persons:
            leaked = self._INTERNAL_KEYS & set(person.keys())
            self.assertEqual(leaked, set(),
                             f"Internal fields leaked into output for {person.get('name')}: {leaked}")


class TestProductionPathUsesV2(unittest.IsolatedAsyncioTestCase):
    """extract_persons should use the v2 pipeline, not the legacy v1."""

    def setUp(self):
        self.extractor = PersonExtractor()

    async def test_extract_persons_finds_all_employees(self):
        """The UPGRADE_EMPLOYEE_LIST fixture has 3 employees. The legacy v1
        path only finds 1. If extract_persons returns all 3, v2 is wired in."""
        soup = BeautifulSoup(UPGRADE_EMPLOYEE_LIST, 'html.parser')
        artifact = PageArtifact(url="https://company.nl", soup=soup, emails=set(), linkedin_urls=set())
        async with aiohttp.ClientSession() as session:
            persons = await self.extractor.extract_persons(
                session, [artifact], [], [], site_domain="company.nl"
            )
        names = [p["name"] for p in persons]
        self.assertIn("Diana Bakker", names)
        self.assertIn("Erik Mulder", names)
        self.assertIn("Fenna de Groot", names,
                      "extract_persons should use v2 pipeline and find all 3 employees")


if __name__ == "__main__":
    unittest.main()
